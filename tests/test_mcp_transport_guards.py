#!/usr/bin/env python3
"""Focused branch coverage for the extracted MCP bridge transport."""
import asyncio
import importlib.util
import sys
import time
from contextvars import ContextVar
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


DEPS = importlib.util.find_spec('httpx') is not None


def _transport():
    if not DEPS:
        _util.skip('mcp_transport dependency (httpx) not installed')
    return _util.load(
        _util.ROOT / 'mcp_transport.py',
        'mcp_transport_guards_' + str(time.time_ns()))


def _session(transport, token='mcptok'):
    token_var = ContextVar(
        'mcp_transport_guard_token_' + str(time.time_ns()),
        default=token)
    return transport.BridgeSession('http://127.0.0.1:18001', token_var)


class ResponseProbe:
    """A complete JSON response used below the session boundary."""

    def __init__(self, body, status_error=None):
        self.body = body
        self.status_error = status_error

    def raise_for_status(self):
        if self.status_error is not None:
            raise self.status_error

    def json(self):
        return self.body


class ClientProbe:
    """Record shaped HTTP requests and provide deterministic result replies."""

    def __init__(self, replies=(), post_response=None,
                 delete_response=None):
        self.replies = list(replies)
        self.calls = []
        self.post_response = post_response or ResponseProbe({'ok': True})
        self.delete_response = (
            delete_response or ResponseProbe({'deleted': True}))

    async def get(self, path, **kwargs):
        self.calls.append(('get', path, kwargs))
        if not self.replies:
            raise RuntimeError('unexpected result poll')
        return ResponseProbe(self.replies.pop(0))

    async def post(self, path, **kwargs):
        self.calls.append(('post', path, kwargs))
        return self.post_response

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return self.delete_response


def _capture(coroutine):
    try:
        return asyncio.run(coroutine)
    except Exception as failure:  # noqa: BLE001
        return f'raised {type(failure).__name__}: {failure}'


def test_explicit_http_client_uses_the_explicit_url(tmp):
    """A compatibility client binds to the call-site URL."""
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
    """An unset request context cannot authorize a bridge request."""
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
    """POST authorizes before the bridge reads its JSON body."""
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
    """POST does not turn an HTTP error response into a successful result."""
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
    """DELETE authorizes before the bridge reads its JSON body."""
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
    """DELETE does not turn an HTTP error response into a successful result."""
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
    """A stale delivery stays while the named delivery is awaited."""
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
    """A result without an acceptance generation cannot be consumed safely."""
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
    """A replaced generation is retried instead of reported as consumed."""
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


def test_poll_reports_timeout_when_no_attempt_is_admitted(tmp):
    """A zero-length polling window has one explicit timeout outcome."""
    del tmp
    transport = _transport()
    session = _session(transport)

    result = _capture(session.poll_result('', 0))

    expected = 'raised TimeoutError: no result within 0s'
    assert result == expected, (result, expected)


def test_extension_command_surfaces_result_error(tmp):
    """An extension error is not returned as a successful empty result."""
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


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='mcptransportguards_')


if __name__ == '__main__':
    raise SystemExit(main())
