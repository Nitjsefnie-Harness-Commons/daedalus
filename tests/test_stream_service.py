#!/usr/bin/env python3
"""Standalone state and lifecycle guarantees for the SSE stream service."""
import os
import subprocess
import threading
import time

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


def _load_service(name):
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'stream_service.py', name=name)


class _RecordingByteSink:
    def __init__(self, fail_at=None):
        self.data = b''
        self.flushes = 0
        self.fail_at = fail_at

    def write(self, data):
        if self.fail_at == 'write':
            raise BrokenPipeError('write failed')
        self.data += data

    def flush(self):
        self.flushes += 1
        if self.fail_at == 'flush':
            raise BrokenPipeError('flush failed')


def test_stream_service_imports_without_daedalus_configuration(_tmp):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('DAEDALUS_') and key != 'TOKEN'}
    loaded = subprocess.run(
        [sys.executable, '-c', 'import daedalus_bridge.stream_service'],
        cwd=str(_util.ROOT), env=env, stderr=subprocess.PIPE, text=True)
    assert loaded.returncode == 0, loaded.stderr


def test_write_frame_emits_exact_command_event_and_flushes_once(_tmp):
    service = _load_service('stream_service_frame_bytes')
    sink = _RecordingByteSink()

    service.write_frame(sink, {'id': 'frame-check'})

    assert sink.data == (
        b'event: command\n'
        b'data: {"id": "frame-check"}\n\n')
    assert sink.flushes == 1, sink.flushes


def test_write_frame_propagates_write_errors(_tmp):
    service = _load_service('stream_service_frame_write_error')
    sink = _RecordingByteSink(fail_at='write')

    try:
        service.write_frame(sink, {'id': 'frame-check'})
    except BrokenPipeError as error:
        assert str(error) == 'write failed', error
    else:
        raise AssertionError('write error was swallowed')

    assert sink.flushes == 0, sink.flushes


def test_write_frame_propagates_flush_errors(_tmp):
    service = _load_service('stream_service_frame_flush_error')
    sink = _RecordingByteSink(fail_at='flush')

    try:
        service.write_frame(sink, {'id': 'frame-check'})
    except BrokenPipeError as error:
        assert str(error) == 'flush failed', error
    else:
        raise AssertionError('flush error was swallowed')

    assert sink.flushes == 1, sink.flushes


def test_queue_drain_honors_explicit_ttl_and_frame_writer(tmp):
    service = _load_service('stream_service_queue_drain')
    qdir = Path(tmp) / 'commands' / 'tok'
    qdir.mkdir(parents=True)
    fresh = qdir / '0000000000001_000001.json'
    expired = qdir / '0000000000002_000002.json'
    fresh.write_text('{"id":"fresh"}', encoding='utf-8')
    expired.write_text('{"id":"expired"}', encoding='utf-8')
    now = time.time()
    os.utime(fresh, (now - 50, now - 50))
    os.utime(expired, (now - 150, now - 150))
    frames = []

    delivered = service.drain_queue(
        qdir, None, None, command_ttl=100, frame_writer=frames.append)

    assert delivered == 1, delivered
    assert frames == [{'id': 'fresh'}], frames
    assert not fresh.exists(), fresh
    assert not expired.exists(), expired


def test_queue_drain_stops_after_stream_is_killed(tmp):
    service = _load_service('stream_service_queue_killed')
    qdir = Path(tmp) / 'commands' / 'tok'
    qdir.mkdir(parents=True)
    first = qdir / '0000000000001_000001.json'
    second = qdir / '0000000000002_000002.json'
    first.write_text('{"id":"first"}', encoding='utf-8')
    second.write_text('{"id":"second"}', encoding='utf-8')
    killed = threading.Event()
    frames = []

    def capture(frame):
        frames.append(frame)
        killed.set()

    delivered = service.drain_queue(
        qdir, None, killed, command_ttl=100, frame_writer=capture)

    assert delivered == 1, delivered
    assert frames == [{'id': 'first'}], frames
    assert not first.exists(), first
    assert second.exists(), second


def test_queue_drain_leaves_hidden_and_non_json_entries_alone(tmp):
    service = _load_service('stream_service_queue_names')
    qdir = Path(tmp) / 'commands' / 'tok'
    qdir.mkdir(parents=True)
    hidden = qdir / '.in-flight.json'
    ready = qdir / '0000000000001_000001.json'
    non_json = qdir / '0000000000002_000002.tmp'
    hidden.write_text('{"id":"hidden"}', encoding='utf-8')
    ready.write_text('{"id":"ready"}', encoding='utf-8')
    non_json.write_text('{"id":"temporary"}', encoding='utf-8')
    frames = []

    delivered = service.drain_queue(
        qdir, None, None, command_ttl=100, frame_writer=frames.append)

    assert delivered == 1, delivered
    assert frames == [{'id': 'ready'}], frames
    assert hidden.exists(), hidden
    assert not ready.exists(), ready
    assert non_json.exists(), non_json


def test_legacy_extension_drain_uses_explicit_command_directory(tmp):
    service = _load_service('stream_service_legacy_extension_drain')
    command_dir = Path(tmp) / 'commands'
    command_dir.mkdir()
    legacy = command_dir / 'tok_42.json'
    legacy.write_text('{"id":"legacy"}', encoding='utf-8')
    frames = []

    delivered = service.drain_legacy_ext(
        command_dir, 'tok', None,
        extension_legacy_name='tok_extension.json',
        command_ttl=100, frame_writer=frames.append)

    assert delivered == 1, delivered
    assert frames == [{'id': 'legacy', 'chromeTab': '42'}], frames
    assert not legacy.exists(), legacy


def test_legacy_extension_name_must_be_keyword_only(tmp):
    service = _load_service('stream_service_legacy_extension_keyword')
    command_dir = Path(tmp) / 'commands'
    command_dir.mkdir()
    frames = []

    try:
        service.drain_legacy_ext(
            command_dir, 'tok', 'tok_extension.json', None,
            command_ttl=100, frame_writer=frames.append)
    except TypeError as error:
        assert 'positional' in str(error), error
    else:
        raise AssertionError('extension legacy name accepted positionally')


def test_legacy_extension_drain_stops_after_stream_is_killed(tmp):
    service = _load_service('stream_service_legacy_extension_killed')
    command_dir = Path(tmp) / 'commands'
    command_dir.mkdir()
    first = command_dir / 'tok_41.json'
    second = command_dir / 'tok_42.json'
    first.write_text('{"id":"first"}', encoding='utf-8')
    second.write_text('{"id":"second"}', encoding='utf-8')
    killed = threading.Event()
    frames = []

    def capture(frame):
        frames.append(frame)
        killed.set()

    delivered = service.drain_legacy_ext(
        command_dir, 'tok', killed,
        extension_legacy_name='tok_extension.json',
        command_ttl=100, frame_writer=capture)

    assert delivered == 1, delivered
    assert frames == [{'id': 'first', 'chromeTab': '41'}], frames
    assert not first.exists(), first
    assert second.exists(), second


def test_legacy_extension_drain_skips_dashboard_name(tmp):
    service = _load_service('stream_service_legacy_dashboard_skip')
    command_dir = Path(tmp) / 'commands'
    command_dir.mkdir()
    tab = command_dir / 'tok_42.json'
    dashboard = command_dir / 'tok_dashboard.json'
    tab.write_text('{"id":"tab"}', encoding='utf-8')
    dashboard.write_text('{"id":"dashboard"}', encoding='utf-8')
    frames = []

    delivered = service.drain_legacy_ext(
        command_dir, 'tok', None,
        extension_legacy_name='tok_extension.json',
        command_ttl=100, frame_writer=frames.append)

    assert delivered == 1, delivered
    assert frames == [{'id': 'tab', 'chromeTab': '42'}], frames
    assert not tab.exists(), tab
    assert dashboard.exists(), dashboard


def test_legacy_extension_drain_skips_its_own_legacy_name(tmp):
    service = _load_service('stream_service_legacy_extension_skip')
    command_dir = Path(tmp) / 'commands'
    command_dir.mkdir()
    tab = command_dir / 'tok_42.json'
    extension = command_dir / 'tok_extension.json'
    tab.write_text('{"id":"tab"}', encoding='utf-8')
    extension.write_text('{"id":"extension"}', encoding='utf-8')
    frames = []

    delivered = service.drain_legacy_ext(
        command_dir, 'tok', None, extension_legacy_name=extension.name,
        command_ttl=100, frame_writer=frames.append)

    assert delivered == 1, delivered
    assert frames == [{'id': 'tab', 'chromeTab': '42'}], frames
    assert not tab.exists(), tab
    assert extension.exists(), extension


def test_inherited_legacy_non_object_is_intentionally_retained(tmp):
    """Pin inherited, intentional retention without delivery."""
    service = _load_service('stream_service_legacy_non_object')
    legacy = Path(tmp) / 'tok_42.json'
    legacy.write_text('["not a command"]', encoding='utf-8')
    frames = []

    delivered = service.drain_legacy_file(
        legacy, '42', command_ttl=100, frame_writer=frames.append)

    assert delivered == 0, delivered
    assert frames == [], frames
    assert legacy.exists(), legacy


def test_expired_legacy_file_is_dropped_without_delivery(tmp):
    service = _load_service('stream_service_legacy_expired')
    legacy = Path(tmp) / 'tok_42.json'
    legacy.write_text('{"id":"expired"}', encoding='utf-8')
    expired_at = time.time() - 150
    os.utime(legacy, (expired_at, expired_at))
    frames = []

    delivered = service.drain_legacy_file(
        legacy, '42', command_ttl=100, frame_writer=frames.append)

    assert delivered == 0, delivered
    assert frames == [], frames
    assert not legacy.exists(), legacy


def test_register_returns_handles_that_unregister_the_stream(_tmp):
    service = _load_service('stream_service_register')

    stream_id, killed = service.register('tok', 'tab')

    assert isinstance(killed, threading.Event), killed
    assert not killed.is_set()
    assert service.snapshot() == (1, ['tab'])

    service.unregister(stream_id, killed)

    assert service.snapshot() == (0, [])


def test_register_replaces_equal_but_distinct_domain_inputs(_tmp):
    service = _load_service('stream_service_replace')
    first_inputs = tuple(['tok', 'tab'])
    second_inputs = tuple(['tok', 'tab'])
    assert first_inputs == second_inputs
    assert first_inputs is not second_inputs
    first_id, first_killed = service.register(*first_inputs)

    second_id, second_killed = service.register(*second_inputs)

    live, _tabs = service.snapshot()
    assert first_id != second_id
    assert first_killed.is_set()
    assert not second_killed.is_set()
    assert live == 1, live


def test_streams_for_two_tabs_of_one_token_coexist(_tmp):
    service = _load_service('stream_service_domain_inputs')

    _first_id, first_killed = service.register('tok', 'first')
    _second_id, second_killed = service.register('tok', 'second')

    assert not first_killed.is_set()
    assert not second_killed.is_set()
    assert service.snapshot() == (2, ['first', 'second'])


def test_two_tabless_streams_coexist_without_killing_either(_tmp):
    service = _load_service('stream_service_tabless')
    _first_id, first_killed = service.register('tok', '')

    _second_id, second_killed = service.register('tok', '')

    live, _tabs = service.snapshot()
    assert not first_killed.is_set()
    assert not second_killed.is_set()
    assert live == 2, live


def test_unregister_with_stale_kill_event_keeps_replacement(_tmp):
    service = _load_service('stream_service_stale_unregister')
    _first_id, first_killed = service.register('tok', 'tab')
    second_id, second_killed = service.register('tok', 'tab')

    service.unregister(second_id, first_killed)

    live, tabs = service.snapshot()
    assert not second_killed.is_set()
    assert (live, tabs) == (1, ['tab']), (live, tabs)


def test_snapshot_reports_live_count_and_tab_names_including_empty(_tmp):
    service = _load_service('stream_service_snapshot')
    service.register('tok-a', 'shared')
    service.register('tok-b', 'shared')
    service.register('tok-c', '')

    assert service.snapshot() == (3, ['', 'shared'])


def test_delivery_clock_records_each_delivery(_tmp):
    service = _load_service('stream_service_delivery_clock')

    class Clock:
        def __init__(self):
            self.calls = 0
            self.values = iter((123.456, 789.012))

        def time(self):
            self.calls += 1
            return next(self.values)

    clock = Clock()
    service.time = clock
    assert service.last_delivery_at() is None

    service.record_delivery()
    assert service.last_delivery_at() == 123.456

    service.record_delivery()
    assert service.last_delivery_at() == 789.012
    assert clock.calls == 2


def test_poll_declines_while_the_drain_holds_the_legacy_claim(tmp):
    svc = _load_service('stream_service_poll_claim')
    cq = svc.command_queue
    _, legacy = cq.command_target_names('tok')
    path = Path(tmp) / legacy
    path.write_text('{"id": "poll-overlap", "code": "1"}', encoding='utf-8')
    assert cq.claim(svc.legacy_claim_key(legacy))

    try:
        assert svc.poll_legacy(Path(tmp), 'tok') == (200, {})
        assert path.exists(), path
    finally:
        cq.release(svc.legacy_claim_key(legacy))


def test_the_drain_declines_while_poll_holds_the_legacy_claim(tmp):
    svc = _load_service('stream_service_drain_claim')
    cq = svc.command_queue
    _, legacy = cq.command_target_names('tok')
    path = Path(tmp) / legacy
    path.write_text('{"id": "drain-overlap"}', encoding='utf-8')
    frames = []
    assert cq.claim(svc.legacy_claim_key(legacy))

    try:
        delivered = svc.drain_legacy_file(
            path, None, command_ttl=100, frame_writer=frames.append)
    finally:
        cq.release(svc.legacy_claim_key(legacy))

    assert delivered == 0, delivered
    assert frames == [], frames
    assert path.exists(), path


def test_poll_consumes_an_object_and_answers_it(tmp):
    svc = _load_service('stream_service_poll_consume')
    cq = svc.command_queue
    _, legacy = cq.command_target_names('tok')
    path = Path(tmp) / legacy
    path.write_text('{"id": "poll-1", "code": "1"}', encoding='utf-8')

    answer = svc.poll_legacy(Path(tmp), 'tok')

    assert answer == (200, {'id': 'poll-1', 'code': '1'}), answer
    assert not path.exists(), path


def test_poll_answers_empty_for_a_missing_or_malformed_file(tmp):
    svc = _load_service('stream_service_poll_empty')
    cq = svc.command_queue
    _, legacy = cq.command_target_names('tok')
    path = Path(tmp) / legacy

    assert svc.poll_legacy(Path(tmp), 'tok') == (200, {})

    path.write_text('{"id": "half', encoding='utf-8')

    assert svc.poll_legacy(Path(tmp), 'tok') == (200, {})
    assert path.exists(), path


def test_poll_refuses_an_unsafe_token_name(tmp):
    svc = _load_service('stream_service_poll_unsafe')

    answer = svc.poll_legacy(Path(tmp), 'ba..d')

    assert answer == (400, {'error': 'invalid path component'}), answer


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
