#!/usr/bin/env python3
"""Standalone publication and lifecycle guarantees for the command queue."""
import contextlib
import io
import json
import os
import subprocess
import sys
import threading
import time
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
                queue.enqueue(cmd_dir, 'tok', 'tab', {'id': 'queued'},
                              command_ttl=90))
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
    delivery_id = delivery_ids[0][0]
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
    delivery_id, duplicate = queue.enqueue(
        cmd_dir, 'tok', 'tab', {'id': 'queued'}, command_ttl=90)
    assert duplicate is False
    destination = cmd_dir / 'tok_tab' / f'{delivery_id}.json'
    assert json.loads(destination.read_text(encoding='utf-8')) == {
        'id': 'queued', '_did': delivery_id}
    assert queue.event('tok').is_set()


def test_an_identical_enqueue_scans_under_the_shared_lock(tmp):
    """The dedup scan shares one lock section with the publish.

    A scan that runs outside the lock reads the queue between another
    producer's scan and its publish, so two identical enqueues can both
    admit fresh deliveries — the defect this branch fixes, back as a race.
    """
    queue = _load_queue('command_queue_dedup_lock')
    cmd_dir = Path(tmp) / 'commands'
    parked, gate = threading.Event(), threading.Event()

    class Observed:
        def __init__(self):
            self._inner = threading.Lock()
            self._count_lock = threading.Lock()
            self.attempts = 0
            self.reacquired = threading.Event()

        def acquire(self):
            with self._count_lock:
                self.attempts += 1
                if self.attempts >= 2:
                    self.reacquired.set()
            return self._inner.acquire()

        def release(self):
            self._inner.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *_args):
            self.release()

    real_publish = queue._publish

    def parked_publish(*args, **kwargs):
        parked.set()
        gate.wait(5)
        return real_publish(*args, **kwargs)

    queue._publish = parked_publish
    lock = Observed()
    queue.command_fs_lock = lock
    payload = {'id': 'same', 'code': '1'}
    producer_done = threading.Event()
    replay_done = threading.Event()
    results, failures = [], []

    def enqueue_into(sink, done):
        try:
            sink.append(queue.enqueue(
                cmd_dir, 'tok', 'tab', payload, command_ttl=90))
        except Exception as error:  # preserve a worker assertion failure
            failures.append(error)
        finally:
            done.set()

    producer = threading.Thread(
        target=enqueue_into, args=(results, producer_done))
    producer.start()
    assert parked.wait(5), 'the first enqueue never reached its publish'
    replay = threading.Thread(
        target=enqueue_into, args=(results, replay_done))
    replay.start()
    try:
        assert lock.reacquired.wait(5), (
            'the identical enqueue never attempted the shared lock while '
            'the first delivery was still publishing')
    finally:
        gate.set()
        producer.join(5)
        replay.join(5)
    assert producer_done.wait(5) and replay_done.wait(5), (
        'an enqueue stayed blocked after the gate released')
    assert failures == [], failures
    assert len(results) == 2, results
    first_did, first_duplicate = results[0]
    assert first_duplicate is False
    second_did, second_duplicate = results[1]
    assert second_did == first_did, (first_did, second_did)
    assert second_duplicate is True
    published = sorted((cmd_dir / 'tok_tab').glob('*.json'))
    assert len(published) == 1, published
    assert json.loads(published[0].read_text(encoding='utf-8')) == {
        'id': 'same', 'code': '1', '_did': first_did}


def test_an_identical_live_command_admits_one_delivery(tmp):
    queue = _load_queue('command_queue_dedup_live')
    cmd_dir = Path(tmp) / 'commands'
    command = {'id': 'same', 'code': '1'}
    first_did, duplicate = queue.enqueue(
        cmd_dir, 'tok', 'tab', command, command_ttl=90)
    assert duplicate is False
    replay_did, duplicate = queue.enqueue(
        cmd_dir, 'tok', 'tab', command, command_ttl=90)
    assert duplicate is True
    assert replay_did == first_did
    published = sorted((cmd_dir / 'tok_tab').glob('*.json'))
    assert len(published) == 1, published
    assert json.loads(published[0].read_text(encoding='utf-8')) == {
        'id': 'same', 'code': '1', '_did': first_did}


def test_a_same_id_with_a_changed_payload_enqueues_fresh(tmp):
    queue = _load_queue('command_queue_dedup_payload')
    cmd_dir = Path(tmp) / 'commands'
    first_did, duplicate = queue.enqueue(
        cmd_dir, 'tok', 'tab', {'id': 'same', 'code': '1'},
        command_ttl=90)
    assert duplicate is False
    second_did, duplicate = queue.enqueue(
        cmd_dir, 'tok', 'tab', {'id': 'same', 'code': '2'},
        command_ttl=90)
    assert duplicate is False
    assert second_did != first_did
    published = list((cmd_dir / 'tok_tab').glob('*.json'))
    assert len(published) == 2, published


def test_a_same_id_with_changed_typed_fields_enqueues_fresh(tmp):
    queue = _load_queue('command_queue_dedup_typed')
    cmd_dir = Path(tmp) / 'commands'
    first_did, duplicate = queue.enqueue(
        cmd_dir, 'tok', 'extension',
        {'id': 'same', 'type': 'shot', 'quality': 'low'}, command_ttl=90)
    assert duplicate is False
    second_did, duplicate = queue.enqueue(
        cmd_dir, 'tok', 'extension',
        {'id': 'same', 'type': 'shot', 'quality': 'high'}, command_ttl=90)
    assert duplicate is False
    assert second_did != first_did
    published = list((cmd_dir / 'tok_extension').glob('*.json'))
    assert len(published) == 2, published


def test_the_same_payload_for_another_tab_enqueues_fresh(tmp):
    queue = _load_queue('command_queue_dedup_tab')
    cmd_dir = Path(tmp) / 'commands'
    first_did, duplicate = queue.enqueue(
        cmd_dir, 'tok', 'tab1', {'id': 'same', 'code': '1'},
        command_ttl=90)
    assert duplicate is False
    second_did, duplicate = queue.enqueue(
        cmd_dir, 'tok', 'tab2', {'id': 'same', 'code': '1'},
        command_ttl=90)
    assert duplicate is False
    assert second_did != first_did
    assert (cmd_dir / 'tok_tab1' / f'{first_did}.json').exists()
    assert (cmd_dir / 'tok_tab2' / f'{second_did}.json').exists()


def test_a_drained_command_enqueues_fresh(tmp):
    queue = _load_queue('command_queue_dedup_drained')
    cmd_dir = Path(tmp) / 'commands'
    first_did, _ = queue.enqueue(
        cmd_dir, 'tok', 'tab', {'id': 'same', 'code': '1'},
        command_ttl=90)
    (cmd_dir / 'tok_tab' / f'{first_did}.json').unlink()
    second_did, duplicate = queue.enqueue(
        cmd_dir, 'tok', 'tab', {'id': 'same', 'code': '1'},
        command_ttl=90)
    assert duplicate is False
    assert second_did != first_did
    published = sorted((cmd_dir / 'tok_tab').glob('*.json'))
    assert len(published) == 1, published


def test_an_expired_entry_is_not_a_live_duplicate(tmp):
    queue = _load_queue('command_queue_dedup_expired')
    cmd_dir = Path(tmp) / 'commands'
    first_did, _ = queue.enqueue(
        cmd_dir, 'tok', 'tab', {'id': 'same', 'code': '1'},
        command_ttl=90)
    aged = cmd_dir / 'tok_tab' / f'{first_did}.json'
    stamp = time.time() - 91
    os.utime(aged, (stamp, stamp))
    second_did, duplicate = queue.enqueue(
        cmd_dir, 'tok', 'tab', {'id': 'same', 'code': '1'},
        command_ttl=90)
    assert duplicate is False
    assert second_did != first_did
    assert aged.exists()
    published = sorted((cmd_dir / 'tok_tab').glob('*.json'))
    assert len(published) == 2, published


def test_hidden_and_temp_entries_are_not_live_duplicates(tmp):
    """A crashed publish's temp and a hidden file are not deliveries.

    `_live_duplicate` skips both names, so an identical payload in a
    `<seq>.json.tmp` — the shape a crashed publisher leaves — cannot
    absorb a retry onto a delivery the drain will never make: the drain
    skips those names too.
    """
    queue = _load_queue('command_queue_dedup_skip_names')
    cmd_dir = Path(tmp) / 'commands'
    qdir = cmd_dir / 'tok_tab'
    qdir.mkdir(parents=True)
    crashed = qdir / '1700000000000_000001.json.tmp'
    crashed.write_text(json.dumps(
        {'id': 'same', 'code': '1', '_did': 'crashed-did'},
        ensure_ascii=False), encoding='utf-8')
    hidden = qdir / '.hidden.json'
    hidden.write_text(json.dumps(
        {'id': 'same', 'code': '1', '_did': 'hidden-did'},
        ensure_ascii=False), encoding='utf-8')
    did, duplicate = queue.enqueue(
        cmd_dir, 'tok', 'tab', {'id': 'same', 'code': '1'}, command_ttl=90)
    assert duplicate is False
    assert did not in ('crashed-did', 'hidden-did')
    assert crashed.exists()
    assert hidden.exists()
    published = sorted(
        path.name for path in qdir.iterdir()
        if not path.name.startswith('.') and path.name.endswith('.json'))
    assert published == [f'{did}.json'], published


def test_a_refused_entry_is_not_a_live_duplicate(tmp):
    """An entry the drain would refuse is not a delivery to wait on.

    The alias is the drain's own refusal shape: a symlinked queue name is
    never delivered, so the identical payload behind it must not absorb a
    retry. `open_command_candidate` refuses the linked name where
    `O_NOFOLLOW` exists and by its identity check where it does not, so
    the scan matches nothing on every platform.
    """
    queue = _load_queue('command_queue_dedup_refused')
    cmd_dir = Path(tmp) / 'commands'
    qdir = cmd_dir / 'tok_tab'
    qdir.mkdir(parents=True)
    outside = Path(tmp) / 'outside.json'
    outside.write_text(json.dumps(
        {'id': 'same', 'code': '1'}, ensure_ascii=False), encoding='utf-8')
    alias = qdir / '1700000000000_000001.json'
    try:
        alias.symlink_to(outside)
    except (OSError, NotImplementedError):
        _util.skip('this platform cannot create symlinks')
    did, duplicate = queue.enqueue(
        cmd_dir, 'tok', 'tab', {'id': 'same', 'code': '1'}, command_ttl=90)
    assert duplicate is False
    assert alias.is_symlink()
    assert outside.exists()
    published = sorted(
        path.name for path in qdir.iterdir()
        if not path.name.startswith('.') and path.name.endswith('.json'))
    assert published == sorted([f'{did}.json', alias.name]), published


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
