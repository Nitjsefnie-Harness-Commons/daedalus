#!/usr/bin/env python3
"""Command expiry and single-consumer delivery stay pinned.

These tests cover claim-registry behavior and forced interleavings across the
queue and legacy delivery paths, proving that one stream receives each
command.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _bridge import (TOK, next_stream_data, put_command,  # noqa: E402
                     stream_response)


def _load_queue(name):
    return _util.load(_util.ROOT / 'command_queue.py', name=name)


def _set_mtime(path, stamp):
    os.utime(path, (stamp, stamp))


def test_remove_expired_removes_an_expired_json(tmp):
    queue = _load_queue('command_queue_expired')
    path = Path(tmp) / 'queued.json'
    path.write_text('{"id":"expired"}', encoding='utf-8')
    now = time.time()
    _set_mtime(path, now - 91)

    queue.remove_expired(path, now, 90)

    assert not path.exists(), path


def test_remove_expired_leaves_a_fresh_json(tmp):
    queue = _load_queue('command_queue_fresh')
    path = Path(tmp) / 'queued.json'
    path.write_text('{"id":"fresh"}', encoding='utf-8')
    now = time.time()
    _set_mtime(path, now - 1)

    queue.remove_expired(path, now, 90)

    assert path.exists(), path


def test_remove_expired_leaves_a_malformed_legacy_file(tmp):
    queue = _load_queue('command_queue_malformed')
    path = Path(tmp) / 'legacy.json'
    path.write_text('{not-json', encoding='utf-8')
    now = time.time()
    _set_mtime(path, now - 91)

    queue.remove_expired(path, now, 90, legacy=True)

    assert path.exists(), path


def test_remove_expired_removes_an_expired_tmp(tmp):
    queue = _load_queue('command_queue_expired_tmp')
    path = Path(tmp) / 'queued.json.tmp'
    path.write_text('{"id":"expired"}', encoding='utf-8')
    now = time.time()
    _set_mtime(path, now - 91)

    queue.remove_expired(path, now, 90)

    assert not path.exists(), path


def test_remove_expired_leaves_a_non_object_legacy_json(tmp):
    queue = _load_queue('command_queue_non_object_legacy')
    path = Path(tmp) / 'legacy.json'
    path.write_text('["not a command"]', encoding='utf-8')
    now = time.time()
    _set_mtime(path, now - 91)

    queue.remove_expired(path, now, 90, legacy=True)

    assert path.exists(), path


def test_remove_expired_leaves_a_file_at_the_ttl_boundary(tmp):
    queue = _load_queue('command_queue_ttl_boundary')
    path = Path(tmp) / 'boundary.json'
    path.write_text('{"id":"boundary"}', encoding='utf-8')
    now = 1000.0
    _set_mtime(path, now - 90)

    queue.remove_expired(path, now, 90)

    assert path.exists(), path


def test_remove_expired_leaves_an_unrelated_suffix(tmp):
    queue = _load_queue('command_queue_unrelated_suffix')
    path = Path(tmp) / 'queued.bak'
    path.write_text('{"id":"unrelated"}', encoding='utf-8')
    now = time.time()
    _set_mtime(path, now - 91)

    queue.remove_expired(path, now, 90)

    assert path.exists(), path


def test_remove_expired_does_not_follow_a_symlink(tmp):
    queue = _load_queue('command_queue_symlink')
    target = Path(tmp) / 'target.json'
    path = Path(tmp) / 'link.json'
    target.write_text('{"id":"target"}', encoding='utf-8')
    now = time.time()
    _set_mtime(target, now - 91)
    try:
        path.symlink_to(target)
        os.utime(path, (now, now), follow_symlinks=False)
    except (OSError, NotImplementedError):
        _util.skip('this platform cannot create or timestamp symlinks')

    queue.remove_expired(path, now, 90)

    assert path.is_symlink(), path
    assert target.exists(), target


def test_a_second_claimer_of_one_key_is_refused(tmp):
    queue = _load_queue('command_queue_claim_refused')
    del tmp

    assert queue.claim('queue:target/one.json') is True
    try:
        assert queue.claim('queue:target/one.json') is False
    finally:
        queue.release('queue:target/one.json')


def test_a_released_key_can_be_claimed_again(tmp):
    queue = _load_queue('command_queue_claim_again')
    del tmp

    assert queue.claim('queue:target/two.json') is True
    queue.release('queue:target/two.json')

    assert queue.claim('queue:target/two.json') is True
    queue.release('queue:target/two.json')


def test_a_claim_released_by_an_exception_can_be_claimed_again(tmp):
    queue = _load_queue('command_queue_claim_exception')
    del tmp

    try:
        with queue.claimed('queue:target/three.json') as owned:
            assert owned is True
            raise RuntimeError('write failed')
    except RuntimeError:
        pass

    assert queue.claim('queue:target/three.json') is True
    queue.release('queue:target/three.json')


def test_a_non_string_or_empty_claim_key_is_refused(tmp):
    queue = _load_queue('command_queue_claim_types')
    invalid = (Path(tmp) / 'path.json', '', None)

    for key in invalid:
        try:
            queue.claim(key)
        except TypeError:
            continue
        raise AssertionError(f'claim accepted invalid key {key!r}')


def _slow_queue_read_dir(tmp):
    fault_dir = Path(tmp) / 'slow-queue-read'
    fault_dir.mkdir()
    read_count = fault_dir / 'command-read-count'
    (fault_dir / 'sitecustomize.py').write_text(
        'import os\n'
        'import pathlib\n'
        'import threading\n'
        'import time\n'
        '_real_read_text = pathlib.Path.read_text\n'
        '_real_stat = pathlib.Path.stat\n'
        '_read_lock = threading.Lock()\n'
        '_read_count = 0\n'
        '_both_read = threading.Event()\n'
        '_stat_lock = threading.Lock()\n'
        '_stat_count = 0\n'
        '_both_stat = threading.Event()\n'
        '_local = threading.local()\n'
        f'_read_log = {str(read_count)!r}\n'
        'def _is_command_file(path):\n'
        '    return path.suffix == ".json" and (\n'
        '        path.parent.name == "commands" or\n'
        '        path.parent.parent.name == "commands")\n'
        'def _slow_queue_read(path, *args, **kwargs):\n'
        '    global _read_count\n'
        '    text = _real_read_text(path, *args, **kwargs)\n'
        '    if _is_command_file(path):\n'
        '        _local.read_done = True\n'
        '        with open(_read_log, "ab") as log:\n'
        '            log.write(b".")\n'
        '        with _read_lock:\n'
        '            _read_count += 1\n'
        '            if _read_count == 2:\n'
        '                _both_read.set()\n'
        '        _both_read.wait(1)\n'
        '        time.sleep(0.1)\n'
        '    return text\n'
        'def _slow_queue_stat(path, *args, **kwargs):\n'
        '    global _stat_count\n'
        '    result = _real_stat(path, *args, **kwargs)\n'
        '    if _is_command_file(path) and _local.__dict__.get(\n'
        '            "read_done", False):\n'
        '        with _stat_lock:\n'
        '            _stat_count += 1\n'
        '            if _stat_count == 2:\n'
        '                _both_stat.set()\n'
        '        _both_stat.wait(1)\n'
        '        time.sleep(0.1)\n'
        '    return result\n'
        'pathlib.Path.read_text = _slow_queue_read\n'
        'pathlib.Path.stat = _slow_queue_stat\n',
        encoding='utf-8')
    return fault_dir, read_count


def _assert_one_delivery(delivered, command_id, served, read_count):
    log = ''.join(served)
    reads = read_count.read_bytes().count(b'.') if read_count.exists() else 0
    assert reads == 1, (
        f'{command_id} was read {reads} times; bridge stdout: {log!r}')
    assert len(delivered) == 1, (
        f'{command_id} reached {len(delivered)} streams: {delivered}; '
        f'bridge stdout: {log!r}')
    assert delivered[0][1].get('id') == command_id, (
        f'wrong frame for {command_id}: {delivered}; '
        f'bridge stdout: {log!r}')
    lines = [line for line in served
             if '[STREAM] DELIVERED' in line and f'id={command_id}' in line]
    assert len(lines) == 1, (
        f'expected one DELIVERED log for {command_id}: {lines}; '
        f'bridge stdout: {log!r}')


def _read_streams_once(responses):
    delivered = []
    for label, response in responses:
        try:
            delivered.append((label, next_stream_data(response, timeout=8)))
        except AssertionError:
            # A timeout here is the expected outcome for the consumer that
            # lost the claim; that is the point of the assertion below. The
            # count assertion in _assert_one_delivery turns one timed-out
            # stream into evidence.
            pass
    return delivered


def _claim_trace_dir(tmp):
    trace_dir = Path(tmp) / 'claim-trace'
    trace_dir.mkdir()
    trace = trace_dir / 'claims.log'
    (trace_dir / 'sitecustomize.py').write_text(
        'import json\n'
        'import pathlib\n'
        'import sys\n'
        'import threading\n'
        f'_trace = {str(trace)!r}\n'
        f'sys.path.insert(0, {str(_util.ROOT)!r})\n'
        'import command_queue\n'
        '_record_lock = threading.Lock()\n'
        '_sequence = 0\n'
        '_active = {}\n'
        'def _record(event, key, result=None, claim_seq=None):\n'
        '    global _sequence\n'
        '    with _record_lock:\n'
        '        _sequence += 1\n'
        '        data = {"seq": _sequence, "event": event, "key": key}\n'
        '        if result is not None:\n'
        '            data["result"] = result\n'
        '        if claim_seq is not None:\n'
        '            data["claim_seq"] = claim_seq\n'
        '        if event == "claim" and result is True:\n'
        '            _active.setdefault(key, []).append(_sequence)\n'
        '        if event == "release":\n'
        '            pending = _active.get(key, [])\n'
        '            if pending:\n'
        '                data["claim_seq"] = pending.pop(0)\n'
        '        with open(_trace, "a", encoding="utf-8") as log:\n'
        '            log.write(json.dumps(data) + "\\n")\n'
        '_real_claim = command_queue.claim\n'
        '_real_release = command_queue.release\n'
        'def claim(key):\n'
        '    result = _real_claim(key)\n'
        '    _record("claim", key, result)\n'
        '    return result\n'
        'def release(key):\n'
        '    _real_release(key)\n'
        '    _record("release", key)\n'
        '_real_unlink = pathlib.Path.unlink\n'
        'def unlink(path, *args, **kwargs):\n'
        '    key = f"legacy:{path.name}"\n'
        '    if path.parent.name == "commands" and path.suffix == ".json":\n'
        '        with _record_lock:\n'
        '            pending = _active.get(key, [])\n'
        '            claim_seq = pending[0] if pending else None\n'
        '        if claim_seq is not None:\n'
        '            _record("delivery", key, claim_seq=claim_seq)\n'
        '    return _real_unlink(path, *args, **kwargs)\n'
        'pathlib.Path.unlink = unlink\n'
        'command_queue.claim = claim\n'
        'command_queue.release = release\n',
        encoding='utf-8')
    return trace_dir, trace


def _claim_events(trace):
    if not trace.exists():
        return []
    events = []
    for line in trace.read_text(encoding='utf-8').splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _wait_for_delivery(trace, key, after=0):
    deadline = time.time() + 5
    while time.time() < deadline:
        events = _claim_events(trace)
        deliveries = [
            event for event in events
            if event.get('event') == 'delivery'
            and event.get('key') == key
            and event.get('claim_seq') is not None
            and event.get('seq', 0) > after]
        if deliveries:
            return deliveries[0]
        time.sleep(0.01)
    raise AssertionError(f'no delivery after {after}: {events}')


def _assert_no_unreleased_claims(events):
    successful = {
        event['seq'] for event in events
        if event.get('event') == 'claim'
        and event.get('result') is True}
    released = {
        event['claim_seq'] for event in events
        if event.get('event') == 'release'
        and event.get('claim_seq') is not None}
    assert not successful - released, events


def _load_server_for_drain(tmp, name):
    root = Path(tmp) / name
    root.mkdir()
    settings = {'DAEDALUS_DIR': str(root), 'DAEDALUS_PORT': '0'}
    saved = {key: os.environ.get(key) for key in settings}
    os.environ.update(settings)
    try:
        return _util.load(_util.ROOT / 'server.py', name=name)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _raise_broken_pipe(_data):
    raise BrokenPipeError('injected write failure')


def test_two_streams_covering_one_queue_deliver_a_command_once(tmp):
    fault_dir, read_count = _slow_queue_read_dir(tmp)
    served = []
    with _util.bridge(tmp, env={'PYTHONPATH': str(fault_dir)},
                      output=served) as (base, _docroot):
        ext_conn, ext_response = stream_response(base, TOK, tab='extension')
        tab_conn, tab_response = stream_response(base, TOK, tab='dup')
        try:
            assert ext_response.status == 200, (
                ext_response.status, ''.join(served))
            assert tab_response.status == 200, (
                tab_response.status, ''.join(served))
            status, body = put_command(
                base, {'token': TOK, 'tab': 'dup', 'id': 'once',
                       'code': '1'})
            assert status == 200, (status, body, ''.join(served))
            delivered = _read_streams_once(
                (('extension', ext_response), ('dup', tab_response)))
            _assert_one_delivery(delivered, 'once', served, read_count)
        finally:
            ext_response.close()
            ext_conn.close()
            tab_response.close()
            tab_conn.close()


def test_two_streams_covering_one_legacy_file_deliver_a_command_once(tmp):
    fault_dir, read_count = _slow_queue_read_dir(tmp)
    served = []
    with _util.bridge(tmp, env={'PYTHONPATH': str(fault_dir)},
                      output=served) as (base, docroot):
        ext_conn, ext_response = stream_response(base, TOK, tab='extension')
        tab_conn, tab_response = stream_response(base, TOK, tab='dup')
        try:
            assert ext_response.status == 200, (
                ext_response.status, ''.join(served))
            assert tab_response.status == 200, (
                tab_response.status, ''.join(served))
            legacy = Path(docroot) / 'commands' / f'{TOK}.json'
            legacy.write_text(
                '{"id":"legacy-once","code":"1"}', encoding='utf-8')
            delivered = _read_streams_once(
                (('extension', ext_response), ('dup', tab_response)))
            _assert_one_delivery(
                delivered, 'legacy-once', served, read_count)
        finally:
            ext_response.close()
            ext_conn.close()
            tab_response.close()
            tab_conn.close()


def test_two_streams_covering_a_broadcast_queue_deliver_a_command_once(tmp):
    fault_dir, read_count = _slow_queue_read_dir(tmp)
    served = []
    with _util.bridge(tmp, env={'PYTHONPATH': str(fault_dir)},
                      output=served) as (base, _docroot):
        ext_conn, ext_response = stream_response(base, TOK, tab='extension')
        tab_conn, tab_response = stream_response(base, TOK, tab='dup')
        try:
            assert ext_response.status == 200, (
                ext_response.status, ''.join(served))
            assert tab_response.status == 200, (
                tab_response.status, ''.join(served))
            status, body = put_command(
                base, {'token': TOK, 'id': 'broadcast-once', 'code': '1'})
            assert status == 200, (status, body, ''.join(served))
            delivered = _read_streams_once(
                (('extension', ext_response), ('dup', tab_response)))
            _assert_one_delivery(
                delivered, 'broadcast-once', served, read_count)
        finally:
            ext_response.close()
            ext_conn.close()
            tab_response.close()
            tab_conn.close()


def test_two_streams_covering_a_per_tab_legacy_file_deliver_once(tmp):
    fault_dir, read_count = _slow_queue_read_dir(tmp)
    served = []
    with _util.bridge(tmp, env={'PYTHONPATH': str(fault_dir)},
                      output=served) as (base, docroot):
        ext_conn, ext_response = stream_response(base, TOK, tab='extension')
        tab_conn, tab_response = stream_response(base, TOK, tab='dup')
        try:
            assert ext_response.status == 200, (
                ext_response.status, ''.join(served))
            assert tab_response.status == 200, (
                tab_response.status, ''.join(served))
            legacy = Path(docroot) / 'commands' / f'{TOK}_dup.json'
            legacy.write_text(
                '{"id":"per-tab-legacy-once","code":"1"}',
                encoding='utf-8')
            delivered = _read_streams_once(
                (('extension', ext_response), ('dup', tab_response)))
            _assert_one_delivery(
                delivered, 'per-tab-legacy-once', served, read_count)
        finally:
            ext_response.close()
            ext_conn.close()
            tab_response.close()
            tab_conn.close()


def test_symlinked_queue_directory_reaches_extension_stream(tmp):
    served = []
    with _util.bridge(tmp, output=served) as (base, docroot):
        commands = Path(docroot) / 'commands'
        target = commands / 'alias-target'
        alias = commands / f'{TOK}_dup'
        target.mkdir()
        try:
            alias.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            _util.skip('this platform cannot create directory symlinks')
        conn, response = stream_response(base, TOK, tab='extension')
        try:
            assert response.status == 200, (response.status, ''.join(served))
            status, body = put_command(
                base, {'token': TOK, 'tab': 'dup', 'id': 'alias-queue',
                       'code': '1'})
            assert status == 200, (status, body, ''.join(served))
            frame = next_stream_data(response, timeout=8)
            assert frame.get('id') == 'alias-queue', frame
        finally:
            response.close()
            conn.close()


def test_a_successful_legacy_delivery_releases_its_claim(tmp):
    fault_dir, trace = _claim_trace_dir(tmp)
    key = f'legacy:{TOK}.json'
    with _util.bridge(tmp, env={'PYTHONPATH': str(fault_dir)}) as (
            base, docroot):
        conn, response = stream_response(base, TOK, tab='extension')
        legacy = Path(docroot) / 'commands' / f'{TOK}.json'
        try:
            assert response.status == 200, response.status
            legacy.write_text(
                '{"id":"legacy-first","code":"1"}',
                encoding='utf-8')
            first = next_stream_data(response, timeout=8)
            assert first.get('id') == 'legacy-first', first
            deadline = time.time() + 5
            while legacy.exists() and time.time() < deadline:
                time.sleep(0.01)
            assert not legacy.exists(), (
                'the first legacy file was not consumed')
            first_delivery = _wait_for_delivery(trace, key)
            legacy.write_text(
                '{"id":"legacy-second","code":"2"}',
                encoding='utf-8')
            second = next_stream_data(response, timeout=8)
            assert second.get('id') == 'legacy-second', second
            deadline = time.time() + 5
            pair = None
            while time.time() < deadline:
                events = _claim_events(trace)
                second_deliveries = [
                    event for event in events
                    if event.get('event') == 'delivery'
                    and event.get('key') == key
                    and event.get('claim_seq') is not None
                    and event.get('seq', 0) > first_delivery['seq']]
                if second_deliveries:
                    delivery = second_deliveries[0]
                    claim = next(
                        event for event in events
                        if event.get('seq') == delivery.get('claim_seq')
                        and event.get('event') == 'claim'
                        and event.get('key') == key
                        and event.get('result') is True)
                    claim_index = events.index(claim)
                    delivery_index = events.index(delivery)
                    release = next(
                        (event for event in events[delivery_index + 1:]
                         if event.get('event') == 'release'
                         and event.get('key') == key
                         and event.get('claim_seq') == claim.get('seq')),
                        None)
                    if release is not None:
                        pair = (claim, delivery, release,
                                events[claim_index + 1:events.index(release)])
                        break
                time.sleep(0.01)
            assert pair is not None, _claim_events(trace)
            assert pair[3] == [pair[1]], pair
            deadline = time.time() + 5
            while legacy.exists() and time.time() < deadline:
                time.sleep(0.01)
            assert not legacy.exists(), (
                'the second legacy file was not consumed')
        finally:
            response.close()
            conn.close()
    _assert_no_unreleased_claims(_claim_events(trace))


def test_queue_write_failure_keeps_file_and_releases_claim(tmp):
    server = _load_server_for_drain(tmp, 'server_queue_write_failure')
    qdir = server.CMD_DIR / 'write-fail-queue'
    qdir.mkdir(parents=True)
    command = qdir / '0000000000001_000001.json'
    command.write_text('{"id":"write-fail"}', encoding='utf-8')
    handler = object.__new__(server.Handler)
    handler._write_frame = _raise_broken_pipe
    key = f'queue:{qdir.name}/{command.name}'
    try:
        try:
            server.Handler._drain_queue(handler, qdir, None, None)
        except BrokenPipeError:
            pass
        else:
            raise AssertionError('the injected queue write did not fail')
        assert command.exists(), command
        assert server.command_queue.claim(key) is True
    finally:
        server.command_queue.release(key)


def test_legacy_write_failure_keeps_file_and_releases_claim(tmp):
    server = _load_server_for_drain(tmp, 'server_legacy_write_failure')
    server.CMD_DIR.mkdir(parents=True, exist_ok=True)
    command = server.CMD_DIR / 'legacy-write-fail.json'
    command.write_text('{"id":"write-fail"}', encoding='utf-8')
    handler = object.__new__(server.Handler)
    handler._write_frame = _raise_broken_pipe
    key = f'legacy:{command.name}'
    try:
        try:
            server.Handler._drain_legacy_file(handler, command, None)
        except BrokenPipeError:
            pass
        else:
            raise AssertionError('the injected legacy write did not fail')
        assert command.exists(), command
        assert server.command_queue.claim(key) is True
    finally:
        server.command_queue.release(key)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
