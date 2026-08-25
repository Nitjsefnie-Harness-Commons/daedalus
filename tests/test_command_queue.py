#!/usr/bin/env python3
"""Command-file expiry stays independent from the bridge server."""
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
    (fault_dir / 'sitecustomize.py').write_text(
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
        'def _is_command_file(path):\n'
        '    return path.suffix == ".json" and (\n'
        '        path.parent.name == "commands" or\n'
        '        path.parent.parent.name == "commands")\n'
        'def _slow_queue_read(path, *args, **kwargs):\n'
        '    global _read_count\n'
        '    text = _real_read_text(path, *args, **kwargs)\n'
        '    if _is_command_file(path):\n'
        '        _local.read_done = True\n'
        '        with _read_lock:\n'
        '            _read_count += 1\n'
        '            if _read_count == 2:\n'
        '                _both_read.set()\n'
        '        _both_read.wait(3)\n'
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
        '        _both_stat.wait(3)\n'
        '        time.sleep(0.1)\n'
        '    return result\n'
        'pathlib.Path.read_text = _slow_queue_read\n'
        'pathlib.Path.stat = _slow_queue_stat\n',
        encoding='utf-8')
    return fault_dir


def _assert_one_delivery(delivered, command_id, served):
    log = ''.join(served)
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
            pass
    return delivered


def test_two_streams_covering_one_queue_deliver_a_command_once(tmp):
    fault_dir = _slow_queue_read_dir(tmp)
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
            _assert_one_delivery(delivered, 'once', served)
        finally:
            ext_response.close()
            ext_conn.close()
            tab_response.close()
            tab_conn.close()


def test_two_streams_covering_one_legacy_file_deliver_a_command_once(tmp):
    fault_dir = _slow_queue_read_dir(tmp)
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
            _assert_one_delivery(delivered, 'legacy-once', served)
        finally:
            ext_response.close()
            ext_conn.close()
            tab_response.close()
            tab_conn.close()


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
