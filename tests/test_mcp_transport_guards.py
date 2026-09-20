#!/usr/bin/env python3
"""Focused branch coverage for the extracted MCP bridge transport."""
import asyncio
import importlib.util
import sys
import time
from contextvars import ContextVar
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _frontend import (  # noqa: E402
    TruncatingFrontEndHandler, truncating_front_end)
from _mcp_transport_probes import (  # noqa: E402
    ClientProbe, ResponseProbe, clock_script)


DEPS = importlib.util.find_spec('httpx') is not None


def _transport():
    if not DEPS:
        _util.skip('daedalus_mcp.transport dependency (httpx) not installed')
    return _util.load(
        _util.ROOT / 'daedalus_mcp' / 'transport.py',
        'mcp_transport_guards_' + str(time.time_ns()))


def _session(transport, token='mcptok', url='http://127.0.0.1:18001'):
    token_var = ContextVar(
        'mcp_transport_guard_token_' + str(time.time_ns()),
        default=token)
    return transport.BridgeSession(url, token_var)


def _capture(coroutine, transport=None):
    """Run one coroutine; with `transport`, close the run's real clients."""
    async def run():
        try:
            return await coroutine
        finally:
            if transport is not None:
                await transport.BridgeTransport.close_current_loop_clients()

    try:
        return asyncio.run(run())
    except Exception as failure:  # noqa: BLE001
        return f'raised {type(failure).__name__}: {failure}'


def test_explicit_http_client_uses_the_explicit_url(tmp):
    del tmp
    transport = _transport()

    async def exercise():
        session = _session(transport)
        client = session.http_client('http://127.0.0.1:18002')
        actual = str(client.base_url)
        await transport.BridgeTransport.close_current_loop_clients()
        return actual

    actual = asyncio.run(exercise())
    expected = 'http://127.0.0.1:18002'
    assert actual == expected, (actual, expected)


def test_empty_token_context_is_rejected(tmp):
    del tmp
    transport = _transport()
    session = _session(transport, token='')
    try:
        result = session.token()
    except RuntimeError as failure:
        result = f'raised RuntimeError: {failure}'
    expected = 'raised RuntimeError: no token in context'
    assert result == expected, (result, expected)


def test_post_sends_authorization_header(tmp):
    del tmp
    transport = _transport()
    session = _session(transport)
    client = ClientProbe()
    session.http_client = lambda: client

    result = asyncio.run(session.post('/segment-job', {'job': 'clip'}))

    assert result == {'ok': True}
    method, path, kwargs = client.calls[0]
    actual = method, path, kwargs.get('headers')
    expected = (
        'post', '/segment-job', {'Authorization': 'Bearer mcptok'})
    assert actual == expected, (actual, expected)


def test_post_propagates_http_status_error(tmp):
    del tmp
    transport = _transport()
    session = _session(transport)
    response = ResponseProbe(
        {'error': 'HTTP 500'}, RuntimeError('HTTP 500'))
    client = ClientProbe(post_response=response)
    session.http_client = lambda: client

    result = _capture(session.post('/segment-job', {'job': 'clip'}))

    expected = 'raised RuntimeError: HTTP 500'
    assert result == expected, (result, expected)
    method, path, kwargs = client.calls[0]
    actual = method, path, kwargs.get('headers')
    expected_call = (
        'post', '/segment-job', {'Authorization': 'Bearer mcptok'})
    assert actual == expected_call, (actual, expected_call)


def test_delete_sends_authorization_header(tmp):
    del tmp
    transport = _transport()
    session = _session(transport)
    client = ClientProbe()
    session.http_client = lambda: client

    result = asyncio.run(session.delete('/upload', {'id': 'shot'}))

    assert result == {'deleted': True}
    method, path, kwargs = client.calls[0]
    actual = method, path, kwargs.get('headers')
    expected = 'DELETE', '/upload', {'Authorization': 'Bearer mcptok'}
    assert actual == expected, (actual, expected)


def test_delete_propagates_http_status_error(tmp):
    del tmp
    transport = _transport()
    session = _session(transport)
    response = ResponseProbe(
        {'error': 'HTTP 500'}, RuntimeError('HTTP 500'))
    client = ClientProbe(delete_response=response)
    session.http_client = lambda: client

    result = _capture(session.delete('/upload', {'id': 'shot'}))

    expected = 'raised RuntimeError: HTTP 500'
    assert result == expected, (result, expected)
    method, path, kwargs = client.calls[0]
    actual = method, path, kwargs.get('headers')
    expected_call = 'DELETE', '/upload', {
        'Authorization': 'Bearer mcptok'}
    assert actual == expected_call, (actual, expected_call)


def test_poll_skips_a_different_delivery(tmp):
    del tmp
    transport = _transport()
    session = _session(transport)
    wanted = {
        'id': 'command',
        'deliveryId': 'wanted',
        'resultGeneration': 'generation-2',
        'result': {'value': 2},
    }
    client = ClientProbe((
        {
            'id': 'other',
            'deliveryId': 'stale',
            'resultGeneration': 'generation-1',
        },
        wanted,
        {'consumed': True, 'resultGeneration': 'generation-2'},
    ))
    session.http_client = lambda: client

    result = _capture(session.poll_result(
        '', 1, interval=0, expect_id='command',
        expect_delivery='wanted'))

    assert result == wanted, (result, wanted)


def test_poll_skips_a_result_without_generation(tmp):
    del tmp
    transport = _transport()
    session = _session(transport)
    wanted = {
        'id': 'command',
        'deliveryId': 'wanted',
        'resultGeneration': 'generation-2',
        'result': {'value': 2},
    }
    client = ClientProbe((
        {'id': 'command', 'deliveryId': 'wanted'},
        wanted,
        {'consumed': True, 'resultGeneration': 'generation-2'},
    ))
    session.http_client = lambda: client

    result = _capture(session.poll_result(
        '', 1, interval=0, expect_id='command',
        expect_delivery='wanted'))

    assert result == wanted, (result, wanted)


def test_poll_retries_a_failed_conditional_consume(tmp):
    del tmp
    transport = _transport()
    session = _session(transport)
    first = {
        'id': 'command',
        'deliveryId': 'wanted',
        'resultGeneration': 'generation-1',
        'result': {'value': 1},
    }
    second = {
        'id': 'command',
        'deliveryId': 'wanted',
        'resultGeneration': 'generation-2',
        'result': {'value': 2},
    }
    client = ClientProbe((
        first,
        {'consumed': False, 'resultGeneration': 'generation-2'},
        second,
        {'consumed': True, 'resultGeneration': 'generation-2'},
    ))
    session.http_client = lambda: client

    result = _capture(session.poll_result(
        '', 1, interval=0, expect_id='command',
        expect_delivery='wanted'))

    assert result == second, (result, second)


def test_poll_survives_a_transport_failure_on_the_peek(tmp):
    """A reset on the peek is retried while the deadline has time left.

    The bridge answers /result at once, so a transport failure on that
    GET is the proxy's, and the command it asks about is already queued.
    Raising it out of the poll reported a failure for work the browser
    went on to do; the poll now keeps going, like a pending slot.
    """
    del tmp
    transport = _transport()
    session = _session(transport)
    wanted = {
        'id': 'command',
        'deliveryId': 'wanted',
        'resultGeneration': 'generation-1',
        'result': {'value': 1},
    }
    client = ClientProbe((
        transport.httpx.ReadError('connection reset by peer'),
        wanted,
        {'consumed': True, 'resultGeneration': 'generation-1'},
    ))
    session.http_client = lambda: client

    result = _capture(session.poll_result(
        '', 1, interval=0, expect_id='command',
        expect_delivery='wanted'))

    assert result == wanted, (result, wanted)
    peeks = [call for call in client.calls if call[1] == '/result']
    assert len(peeks) == 3, client.calls


def test_poll_reports_timeout_after_only_transport_failures(tmp):
    """Retrying a failed peek is bounded by the same deadline."""
    del tmp
    transport = _transport()
    session = _session(transport)
    client = ClientProbe(
        [transport.httpx.ReadError('connection reset by peer')] * 3)
    session.http_client = lambda: client
    # Three admitted peeks, then the clock steps past the deadline.
    session.monotonic = clock_script(100.0, 100.0, 100.0, 100.0, 100.5)

    result = _capture(session.poll_result(
        '', 0.001, interval=0, expect_id='command',
        expect_delivery='wanted'))

    peeks = [call for call in client.calls if call[1] == '/result']
    assert len(peeks) == 3, client.calls
    expected = 'raised TimeoutError: no result within 0.001s'
    assert result == expected, (result, expected)


def test_poll_deadline_is_not_the_wall_clock(tmp):
    """A wall-clock step backwards must not extend the wait.

    The deadline was time.time() based, so an NTP correction between two
    polls put the clock before the deadline again and the wait outlived
    its timeout; the CLI waiter was moved to time.monotonic() for the
    same reason. The wall clock reads once and then steps back an hour,
    so a poll that consults it for the deadline runs the reply script
    dry instead of timing out on the session clock.
    """
    del tmp
    transport = _transport()
    session = _session(transport)
    client = ClientProbe([{'pending': True}] * 1000)
    session.http_client = lambda: client
    first = time.time()
    reads = []

    def stepped_back():
        reads.append(1)
        return first if len(reads) == 1 else first - 3600

    # The session clock admits exactly one peek; a wall-clock deadline
    # sits an epoch past every scripted read, so entry never ends.
    session.monotonic = clock_script(100.0, 100.0, 100.5)
    with mock.patch.object(time, 'time', stepped_back):
        result = _capture(session.poll_result(
            '', 0.001, interval=0, expect_id='command',
            expect_delivery='wanted'))

    peeks = [call for call in client.calls if call[1] == '/result']
    assert len(peeks) == 1, client.calls
    expected = 'raised TimeoutError: no result within 0.001s'
    assert result == expected, (result, expected)


def test_poll_reports_timeout_when_no_attempt_is_admitted(tmp):
    del tmp
    transport = _transport()
    session = _session(transport)

    result = _capture(session.poll_result('', 0))

    expected = 'raised TimeoutError: no result within 0s'
    assert result == expected, (result, expected)


def test_poll_honors_the_session_clock_for_loop_entry(tmp):
    """A clock already past the deadline admits zero peeks.

    Both of the loop's clock reads go through the session, so a clock
    scripted past the deadline before the first entry check ends the
    wait with no poll at all.
    """
    del tmp
    transport = _transport()
    session = _session(transport)
    client = ClientProbe(({'pending': True},))
    session.http_client = lambda: client
    # The deadline read sees 100.0; every later read is 100.5, past it.
    session.monotonic = clock_script(100.0, 100.5)

    result = _capture(session.poll_result('', 0.001, interval=0))

    peeks = [call for call in client.calls if call[1] == '/result']
    assert not peeks, client.calls
    expected = 'raised TimeoutError: no result within 0.001s'
    assert result == expected, (result, expected)


def test_poll_admits_a_read_just_inside_the_deadline(tmp):
    """A read strictly inside the deadline must still admit the poll.

    The clock steps from clearly before to just inside the boundary,
    then past it, so a deadline computed short from the timeout rejects
    the inside read and the hand-over never happens.
    """
    del tmp
    transport = _transport()
    session = _session(transport)
    wanted = {
        'id': 'command',
        'deliveryId': 'wanted',
        'resultGeneration': 'generation-1',
        'result': {'value': 1},
    }
    client = ClientProbe((
        wanted,
        {'consumed': True, 'resultGeneration': 'generation-1'},
    ))
    session.http_client = lambda: client
    # 100.0009 is inside 100.001 but outside a deadline shortened by 10%.
    session.monotonic = clock_script(100.0, 100.0009, 100.5)

    result = _capture(session.poll_result(
        '', 0.001, interval=0, expect_id='command',
        expect_delivery='wanted'))

    assert result == wanted, (result, wanted)


def test_poll_rejects_a_read_exactly_at_the_deadline(tmp):
    """A read equal to the deadline no longer admits a poll.

    The entry check is strict, so the clock stepping exactly onto the
    deadline ends the wait with nothing polled; an inclusive check
    would take the mismatched reply's peek.
    """
    del tmp
    transport = _transport()
    session = _session(transport)
    body = {
        'id': 'other',
        'deliveryId': 'stale',
        'resultGeneration': 'generation-1',
        'result': {'value': 1},
    }
    client = ClientProbe((body,))
    session.http_client = lambda: client
    # The read equals 100.0 + timeout, so entry turns on < vs <=.
    session.monotonic = clock_script(100.0, 100.001, 100.5)

    result = _capture(session.poll_result(
        '', 0.001, interval=0, expect_id='command',
        expect_delivery='wanted'))

    peeks = [call for call in client.calls if call[1] == '/result']
    assert not peeks, client.calls
    expected = 'raised TimeoutError: no result within 0.001s'
    assert result == expected, (result, expected)


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
    session.monotonic = clock_script(100.0, 100.0, 100.5)

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
    session.monotonic = clock_script(100.0, 100.0, 100.5)

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
    session.monotonic = clock_script(100.0, 100.0, 100.5)

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
    session.monotonic = clock_script(100.0, 100.0, 100.5)

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
    session.monotonic = clock_script(100.0, 100.0, 100.5)

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
    session.monotonic = clock_script(100.0, 100.0, 100.5)

    result = _capture(session.poll_result(
        '', 0.001, interval=0, expect_id='command', expect_delivery=''))

    peeks = [call for call in client.calls if call[1] == '/result']
    assert len(peeks) == 1, client.calls
    expected = 'raised TimeoutError: no result within 0.001s'
    assert result == expected, (result, expected)


def test_extension_command_surfaces_result_error(tmp):
    del tmp
    transport = _transport()
    session = _session(transport)

    async def put(_path, _payload):
        return {'did': 'delivery'}

    async def poll_result(*_args, **_kwargs):
        return {'error': 'capture failed', 'result': {}}

    session.put = put
    session.poll_result = poll_result
    result = _capture(session.ext_cmd('_ss', 'screenshot'))

    expected = 'raised RuntimeError: ext screenshot: capture failed'
    assert result == expected, (result, expected)


def _shielded_environment():
    """No shell setting may rebind the session away from the front end."""
    return mock.patch.dict('os.environ', {}, clear=False)


def test_poll_survives_a_truncated_peek_from_a_real_front_end(tmp):
    """The peek that a real proxy cuts off raises RemoteProtocolError.

    The scripted controls raise ReadError, so narrowing the poll's clause
    to that one class kept them green (issue 699) while httpx's answer to
    a body shorter than its Content-Length escaped poll_result on the
    first GET. This one drives a real client at a real front end.
    """
    del tmp
    transport = _transport()
    with _shielded_environment() as environ, truncating_front_end(1) as base:
        environ.pop('DAEDALUS_LOCAL_URL', None)
        environ.pop('DAEDALUS_PORT', None)
        session = _session(transport, url=base)
        result = _capture(session.poll_result(
            '', 10, interval=0, expect_id='job4', expect_delivery='d1'),
            transport)
        seen = list(TruncatingFrontEndHandler.seen)
    assert result == TruncatingFrontEndHandler.result, result
    assert seen == [
        ('GET', '/result?delivery=d1'),
        ('GET', '/result?delivery=d1'),
        ('GET', '/result?delivery=d1&consume=1&expected=g1'),
    ], seen


def test_poll_reports_timeout_when_a_real_front_end_cuts_every_peek(tmp):
    """Bounded by the deadline, and the failure it survives is named.

    The direct probe pins the family: a future httpx that reports a
    cut-off body as something other than RemoteProtocolError changes
    what the poll's clause has to admit, and this is where it shows.
    """
    del tmp
    transport = _transport()

    async def probe(session):
        try:
            await session.http_client().get(
                '/result', headers=session.auth())
        except transport.httpx.TransportError as failure:
            return type(failure).__name__
        return 'no failure'

    with _shielded_environment() as environ, truncating_front_end(None) as (
            base):
        environ.pop('DAEDALUS_LOCAL_URL', None)
        environ.pop('DAEDALUS_PORT', None)
        session = _session(transport, url=base)
        result = _capture(session.poll_result(
            '', 0.3, interval=0, expect_id='job4', expect_delivery='d1'),
            transport)
        raised = _capture(probe(session), transport)
        seen = list(TruncatingFrontEndHandler.seen)
    expected = 'raised TimeoutError: no result within 0.3s'
    assert result == expected, (result, expected)
    assert raised == 'RemoteProtocolError', raised
    peeks = [path for verb, path in seen if verb == 'GET']
    assert len(peeks) >= 2, seen


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='mcptransportguards_')


if __name__ == '__main__':
    raise SystemExit(main())
