#!/usr/bin/env python3
"""Standalone publication and lifecycle guarantees for the command queue."""
import contextlib
import io
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


def _load_queue(name):
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'command_queue.py', name=name)


def _load_service(name):
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'stream_service.py', name=name)


def test_command_queue_imports_without_daedalus_configuration(_tmp):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('DAEDALUS_') and key != 'TOKEN'}
    loaded = subprocess.run(
        [sys.executable, '-c', 'import daedalus_bridge.command_queue'],
        cwd=str(_util.ROOT), env=env, stderr=subprocess.PIPE, text=True)
    assert loaded.returncode == 0, loaded.stderr


def test_queue_naming_contract_is_pinned(tmp):
    del tmp
    queue = _load_queue('command_queue_names')

    assert queue.command_target_names('tok', 'tab') == (
        'tok_tab', 'tok_tab.json')


def test_path_safety_helpers_stay_under_the_module_namespace(_tmp):
    queue = _load_queue('command_queue_path_safety_namespace')
    assert queue.path_safety.derived_component is not None
    assert 'derived_component' not in vars(queue)


def test_enqueue_waits_for_the_shared_filesystem_lock(tmp):
    queue = _load_queue('command_queue_filesystem_lock')
    cmd_dir = Path(tmp) / 'commands'
    finished = threading.Event()
    delivery_ids = []
    failures = []

    class ObservedLock:
        def __init__(self):
            self._lock = threading.Lock()
            self.attempted = threading.Event()

        def acquire(self):
            self.attempted.set()
            return self._lock.acquire()

        def release(self):
            self._lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, _kind, _value, _traceback):
            self.release()

    observed = ObservedLock()
    queue.command_fs_lock = observed
    observed.acquire()
    observed.attempted.clear()

    def publish():
        try:
            delivery_ids.append(
                queue.enqueue(cmd_dir, 'tok', 'tab', {'id': 'queued'}))
        except Exception as error:  # preserve a worker assertion failure
            failures.append(error)
        finally:
            finished.set()

    worker = threading.Thread(target=publish)
    worker.start()
    try:
        assert observed.attempted.wait(5), (
            'enqueue never attempted the shared command filesystem lock')
        assert not (cmd_dir / 'tok_tab').exists()
    finally:
        observed.release()
    assert finished.wait(5), (
        'enqueue stayed blocked after the lock was released')
    worker.join()
    assert failures == []
    assert len(delivery_ids) == 1, delivery_ids
    delivery_id = delivery_ids[0]
    assert (cmd_dir / 'tok_tab' / f'{delivery_id}.json').exists()


def test_notify_dashboard_publishes_an_event_and_wakes_the_token(tmp):
    queue = _load_queue('command_queue_dashboard_notify')
    cmd_dir = Path(tmp) / 'commands'
    queue.notify_dashboard(cmd_dir, 'tok', {'type': 'tabs-synced'})
    published, = (cmd_dir / 'tok_dashboard').iterdir()
    assert json.loads(published.read_text(encoding='utf-8')) == {
        'id': published.stem, 'kind': 'event', 'type': 'tabs-synced'}
    assert queue.event('tok').is_set()


def test_notify_dashboard_publishes_no_final_name_before_the_replace(tmp):
    """A dashboard event's final name appears only with its full document.

    The SSE drain delivers queue files by name, so a publisher that created
    `<stem>.json` and filled it in place hands the dashboard stream a torn
    frame -- a staged run observed the final file at five bytes. While the
    writer holds the document, only the dot-prefixed temp may exist in the
    queue directory.
    """
    queue = _load_queue('command_queue_dashboard_publish_pause')
    cmd_dir = Path(tmp) / 'commands'
    dash_dir = cmd_dir / 'tok_dashboard'
    payload = {'type': 'tabs-synced'}
    paused, release = threading.Event(), threading.Event()
    real_replace = queue.atomic_file.replace_atomically

    def replace_paused(src, dst):
        paused.set()
        release.wait()
        real_replace(src, dst)

    # command_queue calls through the shared daedalus_bridge.atomic_file
    # module, so this attribute is the one _publish resolves at call time.
    queue.atomic_file.replace_atomically = replace_paused
    worker = threading.Thread(
        target=queue.notify_dashboard, args=(cmd_dir, 'tok', payload))
    worker.start()
    try:
        assert paused.wait(5), (
            'notify_dashboard never reached the atomic replacement')
        assert not list(dash_dir.glob('*.json')), (
            'a final event name was visible before the replace')
    finally:
        release.set()
        worker.join(5)
        queue.atomic_file.replace_atomically = real_replace
    assert not worker.is_alive(), 'the publisher stayed blocked'
    names = sorted(path.name for path in dash_dir.iterdir())
    assert len(names) == 1 and names[0].endswith('.json'), names
    final = dash_dir / names[0]
    assert json.loads(final.read_text(encoding='utf-8')) == {
        'id': final.stem, 'kind': 'event', **payload}


def test_the_notify_failure_line_redacts_the_credential(tmp):
    """The failure diagnostic is not a second copy of the credential.

    An OSError str() renders the path it failed on, and the queue
    directory is named from the token, so a queue directory blocked by a
    file spelled the whole credential on the [DASH-NOTIFY-FAIL] line.
    """
    queue = _load_queue('command_queue_notify_redact')
    cmd_dir = Path(tmp) / 'commands'
    cmd_dir.mkdir()
    (cmd_dir / 'tok-verify_dashboard').write_text(
        'a file, not a directory', encoding='utf-8')
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        queue.notify_dashboard(cmd_dir, 'tok-verify', {'type': 'result'})
    line = output.getvalue()
    assert 'tok-verify' not in line, line
    assert 'tok-veri…' in line, line


# Runs in a child whose preferred encoding is verified not to be UTF-8, then
# publishes and drains two non-ASCII titles. In this process the check would
# be decided by whatever locale the machine happens to have: on a UTF-8 host
# the unfixed write_text is already UTF-8 and the defect is invisible. The
# driver is a file rather than a -c argument, and spells its titles as code
# points, because a C locale cannot carry either on the command line.
_NON_UTF8_DRAIN = """
import json, locale, sys
sys.path.insert(0, sys.argv[1])
import _util
queue = _util.load(sys.argv[2], name='cq_probe')
service = _util.load(sys.argv[3], name='ss_probe')
from pathlib import Path
report = {'encoding': locale.getpreferredencoding(False), 'titles': {}}
cmd_dir = Path(sys.argv[4]) / 'commands'
dash_dir = cmd_dir / 'tok_dashboard'
for title in ('caf' + chr(0xE9), 'tab ' + chr(0x1F680)):
    queue.notify_dashboard(
        cmd_dir, 'tok', {'type': 'tab-registered', 'title': title})
    names = sorted(path.name for path in dash_dir.iterdir())
    raw = (dash_dir / names[0]).read_bytes() if names else b''
    try:
        decoded = json.loads(raw.decode('utf-8')).get('title')
    except (UnicodeDecodeError, ValueError) as error:
        decoded = 'undecodable: ' + type(error).__name__
    frames = []
    try:
        delivered = service.drain_queue(
            dash_dir, None, None, command_ttl=100,
            frame_writer=frames.append)
    except Exception as error:
        delivered = 'raised: ' + type(error).__name__
    report['titles'][title] = {
        'names': names, 'bytes': len(raw), 'decoded': decoded,
        'delivered': delivered, 'frames': frames,
        'residue': sorted(path.name for path in dash_dir.iterdir())}
    for path in dash_dir.iterdir():
        path.unlink()
print(json.dumps(report, ensure_ascii=True))
"""


def _drain_under_non_utf8_locale(tmp, queue_path, service_path):
    env = {key: value for key, value in os.environ.items()
           if key not in ('PYTHONIOENCODING', 'PYTHONUTF8', 'LC_ALL',
                          'LANG', 'LC_CTYPE')}
    env.update(PYTHONUTF8='0', PYTHONCOERCECLOCALE='0', LC_ALL='C',
               LANG='C')
    driver = Path(tmp) / 'drain_probe.py'
    driver.write_text(_NON_UTF8_DRAIN, encoding='ascii')
    run = subprocess.run(
        [sys.executable, '-X', 'utf8=0', str(driver),
         str(_util.ROOT / 'tests'), str(queue_path), str(service_path),
         tmp],
        cwd=str(_util.ROOT), env=env, capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=120)
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
    # The drain logs each delivery to stdout; the report is the last line.
    report = json.loads(run.stdout.strip().splitlines()[-1])
    if report['encoding'].lower().replace('-', '') == 'utf8':
        _util.skip('the interpreter cannot be put in a non-UTF-8 locale here')
    return report


def test_notify_dashboard_delivers_non_ascii_titles_through_the_drain(tmp):
    """A dashboard event must reach the drain whatever its title contains.

    The drain decodes every entry as UTF-8, so an event written in the
    locale code page is undecodable wherever that code page is not UTF-8:
    `café` was left in place and re-read every tick until the TTL sweep,
    and an emoji failed inside the write after a zero-byte file already
    existed. Both lost the event. The child runs with a verified non-UTF-8
    preferred encoding, because on a UTF-8 host the unfixed write was
    already UTF-8 by accident and the defect cannot be seen.
    """
    report = _drain_under_non_utf8_locale(
        tmp, _util.ROOT / 'daedalus_bridge' / 'command_queue.py',
        _util.ROOT / 'daedalus_bridge' / 'stream_service.py')
    for title, seen in report['titles'].items():
        assert len(seen['names']) == 1, (title, seen)
        assert seen['names'][0].endswith('.json'), (title, seen)
        assert seen['bytes'] > 0, (title, seen)
        assert seen['decoded'] == title, (title, seen)
        assert seen['delivered'] == 1, (title, seen)
        assert seen['frames'] == [{
            'id': seen['names'][0][:-5], 'kind': 'event',
            'type': 'tab-registered', 'title': title}], (title, seen)
        assert seen['residue'] == [], (title, seen)


def test_next_seq_is_lexically_increasing_and_well_formed(_tmp):
    queue = _load_queue('command_queue_next_seq')
    first, second = queue.next_seq(), queue.next_seq()
    assert first < second, (first, second)
    for value in (first, second):
        assert (tuple(map(len, value.split('_'))) == (13, 6)
                and value.replace('_', '').isdigit()), value


def test_event_reuses_one_wake_event_per_token(_tmp):
    queue = _load_queue('command_queue_event')
    assert queue.event('tok') is queue.event('tok') is not queue.event('other')


def test_enqueue_atomically_publishes_a_waking_delivery(tmp):
    queue = _load_queue('command_queue_enqueue')
    cmd_dir = Path(tmp) / 'commands'
    delivery_id = queue.enqueue(cmd_dir, 'tok', 'tab', {'id': 'queued'})
    destination = cmd_dir / 'tok_tab' / f'{delivery_id}.json'
    assert json.loads(destination.read_text(encoding='utf-8')) == {
        'id': 'queued', '_did': delivery_id}
    assert queue.event('tok').is_set()


def test_collect_expired_removes_old_commands_and_empty_queues(tmp):
    queue = _load_queue('command_queue_collect_expired')
    cmd_dir = Path(tmp) / 'commands'
    queued = cmd_dir / 'tok' / 'old.json'
    legacy = cmd_dir / 'tok.json'
    queued.parent.mkdir(parents=True)
    queued.write_text('{"id":"queued"}', encoding='utf-8')
    legacy.write_text('{"id":"legacy"}', encoding='utf-8')
    os.utime(queued, (0, 0))
    os.utime(legacy, (0, 0))
    queue.collect_expired(cmd_dir, 1)
    assert not queued.exists(), queued
    assert not queued.parent.exists(), queued.parent
    assert not legacy.exists(), legacy


def test_collect_expired_sweeps_an_expired_legacy_temp(tmp):
    queue = _load_queue('command_queue_legacy_temp_expired')
    cmd_dir = Path(tmp) / 'commands'
    cmd_dir.mkdir()
    temp = cmd_dir / 'tok.json.tmp'
    temp.write_text('{"id":"queued"}', encoding='utf-8')
    os.utime(temp, (0, 0))
    queue.collect_expired(cmd_dir, 1)
    assert not temp.exists(), temp


def test_collect_expired_sweeps_an_expired_legacy_tab_temp(tmp):
    queue = _load_queue('command_queue_legacy_tab_temp_expired')
    cmd_dir = Path(tmp) / 'commands'
    cmd_dir.mkdir()
    temp = cmd_dir / 'tok_tab.json.tmp'
    temp.write_text('{"id":"queued"}', encoding='utf-8')
    os.utime(temp, (0, 0))
    queue.collect_expired(cmd_dir, 1)
    assert not temp.exists(), temp


def test_collect_expired_retains_an_expired_legacy_temp_partial_json(tmp):
    """An expired temp that does not parse is retained.

    A crashed legacy writer's partial temp is indistinguishable from a
    live writer's, so the sweep keeps both — the same protection a
    malformed legacy final already gets.
    """
    queue = _load_queue('command_queue_legacy_temp_partial')
    cmd_dir = Path(tmp) / 'commands'
    cmd_dir.mkdir()
    temp = cmd_dir / 'tok.json.tmp'
    temp.write_text('{"id":"que', encoding='utf-8')
    os.utime(temp, (0, 0))
    queue.collect_expired(cmd_dir, 1)
    assert temp.exists(), temp


def test_collect_expired_retains_fresh_legacy_temps(tmp):
    queue = _load_queue('command_queue_legacy_temp_fresh')
    cmd_dir = Path(tmp) / 'commands'
    cmd_dir.mkdir()
    complete = cmd_dir / 'tok.json.tmp'
    partial = cmd_dir / 'tok_tab.json.tmp'
    complete.write_text('{"id":"queued"}', encoding='utf-8')
    partial.write_text('{"id":"que', encoding='utf-8')
    queue.collect_expired(cmd_dir, 60)
    assert complete.exists(), complete
    assert partial.exists(), partial


def test_collect_expired_leaves_a_hidden_temp_alone(tmp):
    queue = _load_queue('command_queue_hidden_temp')
    cmd_dir = Path(tmp) / 'commands'
    cmd_dir.mkdir()
    hidden = cmd_dir / '.tok.json.tmp'
    hidden.write_text('{"id":"queued"}', encoding='utf-8')
    os.utime(hidden, (0, 0))
    queue.collect_expired(cmd_dir, 1)
    assert hidden.exists(), hidden


def test_collect_expired_leaves_a_bare_tmp_name_alone(tmp):
    queue = _load_queue('command_queue_bare_tmp')
    cmd_dir = Path(tmp) / 'commands'
    cmd_dir.mkdir()
    stray = cmd_dir / 'tok.tmp'
    stray.write_text('{"id":"queued"}', encoding='utf-8')
    os.utime(stray, (0, 0))
    queue.collect_expired(cmd_dir, 1)
    assert stray.exists(), stray


def test_remove_expired_retains_a_refused_legacy_temp(tmp):
    queue = _load_queue('command_queue_legacy_temp_refused')
    cmd_dir = Path(tmp) / 'commands'
    cmd_dir.mkdir()
    target = cmd_dir / 'target.json'
    temp = cmd_dir / 'tok.json.tmp'
    target.write_text('{"id":"queued"}', encoding='utf-8')
    try:
        temp.symlink_to(target)
        os.utime(temp, (0, 0), follow_symlinks=False)
    except (OSError, NotImplementedError):
        _util.skip('this platform cannot create or timestamp symlinks')
    queue.remove_expired(temp, 2, 1, legacy=True)
    assert temp.is_symlink(), temp
    assert target.exists(), target


def test_gc_loop_forwards_directory_and_ttl_after_sleep(_tmp):
    queue = _load_queue('command_queue_gc_loop')
    cmd_dir = Path('commands')
    calls = []

    def sleep(interval):
        calls.append(('sleep', interval))

    def collect(directory, ttl):
        calls.append(('collect', directory, ttl))
        raise RuntimeError('one iteration complete')

    queue.time = type('Clock', (), {'sleep': staticmethod(sleep)})
    queue.collect_expired = collect
    try:
        queue.gc_loop(cmd_dir, 45)
    except RuntimeError as error:
        assert error.args == ('one iteration complete',)
    assert calls == [
        ('sleep', 30.0),
        ('collect', cmd_dir, 45),
    ]


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
