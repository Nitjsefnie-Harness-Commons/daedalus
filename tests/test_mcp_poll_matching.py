#!/usr/bin/env python3
"""The poll's delivery-matching rejections, each pinned on the session clock.

A peek whose body does not match the expectation is left in place for
its owner; each test here proves one rejection axis leaves the shared
slot untouched and ends the wait at the deadline, never consuming.
"""
import asyncio
import importlib.util
import sys
import time
from contextvars import ContextVar
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _mcp_transport_probes import (  # noqa: E402
    ClientProbe, clock_script)

DEPS = importlib.util.find_spec('httpx') is not None


def _transport():
    if not DEPS:
        _util.skip('daedalus_mcp.transport dependency (httpx) not installed')
    return _util.load(
        _util.ROOT / 'daedalus_mcp' / 'transport.py',
        'mcp_poll_matching_' + str(time.time_ns()))


def _session(transport, token='mcptok', url='http://127.0.0.1:18001'):
    token_var = ContextVar(
        'mcp_poll_matching_token_' + str(time.time_ns()),
        default=token)
    return transport.BridgeSession(url, token_var)


def _capture(coroutine):
    try:
        return asyncio.run(coroutine)
    except Exception as failure:  # noqa: BLE001
        return f'raised {type(failure).__name__}: {failure}'


def test_poll_rejects_a_body_without_a_delivery_id(tmp):
    del tmp
    transport = _transport()
    session = _session(transport)
    body = {
        'id': 'command',
        'resultGeneration': 'generation-1',
        'result': {'value': 1},
    }
    # The receipt after the body proves the only thing that stopped the
    # hand-over is the matching rule: the consume itself would have succeeded.
    # The clock script admits exactly one peek, then steps past the deadline.
    client = ClientProbe((
        body,
        {'consumed': True, 'resultGeneration': 'generation-1'},
    ))
    session.http_client = lambda: client
    session.monotonic = clock_script(100.0, 100.0, 100.0, 100.5)

    result = _capture(session.poll_result(
        '', 0.001, interval=0, expect_id='command'))

    peeks = [call for call in client.calls if call[1] == '/result']
    assert len(peeks) == 1, client.calls
    expected = 'raised TimeoutError: no result within 0.001s'
    assert result == expected, (result, expected)


def test_poll_rejects_an_empty_delivery_id(tmp):
    del tmp
    transport = _transport()
    session = _session(transport)
    body = {
        'id': 'command',
        'deliveryId': '',
        'resultGeneration': 'generation-1',
        'result': {'value': 1},
    }
    client = ClientProbe((
        body,
        {'consumed': True, 'resultGeneration': 'generation-1'},
    ))
    session.http_client = lambda: client
    session.monotonic = clock_script(100.0, 100.0, 100.0, 100.5)

    result = _capture(session.poll_result(
        '', 0.001, interval=0, expect_id='command'))

    peeks = [call for call in client.calls if call[1] == '/result']
    assert len(peeks) == 1, client.calls
    expected = 'raised TimeoutError: no result within 0.001s'
    assert result == expected, (result, expected)


def test_poll_rejects_a_delivery_id_when_none_is_expected(tmp):
    del tmp
    transport = _transport()
    session = _session(transport)
    body = {
        'id': 'command',
        'deliveryId': 'someone-elses',
        'resultGeneration': 'generation-1',
        'result': {'value': 1},
    }
    client = ClientProbe((
        body,
        {'consumed': True, 'resultGeneration': 'generation-1'},
    ))
    session.http_client = lambda: client
    session.monotonic = clock_script(100.0, 100.0, 100.0, 100.5)

    result = _capture(session.poll_result(
        '', 0.001, interval=0, expect_id='command'))

    peeks = [call for call in client.calls if call[1] == '/result']
    assert len(peeks) == 1, client.calls
    expected = 'raised TimeoutError: no result within 0.001s'
    assert result == expected, (result, expected)


def test_poll_rejects_a_matching_delivery_with_a_foreign_command_id(tmp):
    del tmp
    transport = _transport()
    session = _session(transport)
    body = {
        'id': 'other',
        'deliveryId': 'wanted',
        'resultGeneration': 'generation-1',
        'result': {'value': 1},
    }
    client = ClientProbe((
        body,
        {'consumed': True, 'resultGeneration': 'generation-1'},
    ))
    session.http_client = lambda: client
    session.monotonic = clock_script(100.0, 100.0, 100.0, 100.5)

    result = _capture(session.poll_result(
        '', 0.001, interval=0, expect_id='command',
        expect_delivery='wanted'))

    peeks = [call for call in client.calls if call[1] == '/result']
    assert len(peeks) == 1, client.calls
    expected = 'raised TimeoutError: no result within 0.001s'
    assert result == expected, (result, expected)


def test_poll_rejects_a_matching_command_id_with_a_foreign_delivery_id(tmp):
    del tmp
    transport = _transport()
    session = _session(transport)
    body = {
        'id': 'command',
        'deliveryId': 'stale',
        'resultGeneration': 'generation-1',
        'result': {'value': 1},
    }
    client = ClientProbe((
        body,
        {'consumed': True, 'resultGeneration': 'generation-1'},
    ))
    session.http_client = lambda: client
    session.monotonic = clock_script(100.0, 100.0, 100.0, 100.5)

    result = _capture(session.poll_result(
        '', 0.001, interval=0, expect_id='command',
        expect_delivery='wanted'))

    peeks = [call for call in client.calls if call[1] == '/result']
    assert len(peeks) == 1, client.calls
    expected = 'raised TimeoutError: no result within 0.001s'
    assert result == expected, (result, expected)


def test_poll_rejects_an_empty_delivery_expectation(tmp):
    del tmp
    transport = _transport()
    session = _session(transport)
    # The empty string is the one value where a truthiness check and an
    # `is None` check disagree, so the body has to report the same empty
    # string the caller sent to catch the weaker spelling.
    body = {
        'id': 'command',
        'deliveryId': '',
        'resultGeneration': 'generation-1',
        'result': {'value': 1},
    }
    client = ClientProbe((
        body,
        {'consumed': True, 'resultGeneration': 'generation-1'},
    ))
    session.http_client = lambda: client
    session.monotonic = clock_script(100.0, 100.0, 100.0, 100.5)

    result = _capture(session.poll_result(
        '', 0.001, interval=0, expect_id='command', expect_delivery=''))

    peeks = [call for call in client.calls if call[1] == '/result']
    assert len(peeks) == 1, client.calls
    expected = 'raised TimeoutError: no result within 0.001s'
    assert result == expected, (result, expected)


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='mcppollmatching_')


if __name__ == '__main__':
    raise SystemExit(main())
