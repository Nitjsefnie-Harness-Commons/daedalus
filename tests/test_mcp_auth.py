#!/usr/bin/env python3
"""Authentication and request-carrier coverage for the MCP middleware."""
import asyncio
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import test_mcp_server  # noqa: E402


def _auth_module():
    test_mcp_server._need_deps()
    return _util.load(
        _util.ROOT / 'mcp_auth.py',
        'mcp_auth_under_test_' + str(time.time_ns()))


def _start_listener(base, max_body_size):
    previous = os.environ.get('DAEDALUS_MCP_MAX_BODY_SIZE')
    os.environ['DAEDALUS_MCP_MAX_BODY_SIZE'] = str(max_body_size)
    try:
        return test_mcp_server._start_mcp_in_process(base)
    finally:
        if previous is None:
            os.environ.pop('DAEDALUS_MCP_MAX_BODY_SIZE', None)
        else:
            os.environ['DAEDALUS_MCP_MAX_BODY_SIZE'] = previous


def _initialize_body(padding=0):
    body = {
        'jsonrpc': '2.0',
        'id': 'initialize',
        'method': 'initialize',
        'params': {
            'protocolVersion': '2024-11-05',
            'capabilities': {},
            'clientInfo': {'name': 'auth-isolation', 'version': '0'},
        },
    }
    return json.dumps(body).encode() + b' ' * padding


def test_undeclared_oversized_body_is_refused_after_read(tmp):
    del tmp
    auth = _auth_module()
    outbound = []
    inbound = [{
        'type': 'http.request',
        'body': b'x' * 16,
        'more_body': False,
    }]

    async def receive():
        return inbound.pop(0)

    async def send(message):
        outbound.append(message)

    async def accepted(_scope, _receive, send_response):
        await send_response({
            'type': 'http.response.start',
            'status': 204,
            'headers': [],
        })
        await send_response({
            'type': 'http.response.body',
            'body': b'',
            'more_body': False,
        })

    scope = {
        'type': 'http',
        'asgi': {'version': '3.0'},
        'http_version': '1.1',
        'method': 'POST',
        'scheme': 'http',
        'path': '/mcp',
        'raw_path': b'/mcp',
        'query_string': b'',
        'headers': [
            (b'authorization',
             f'Bearer {test_mcp_server.TOK}'.encode()),
        ],
        'client': ('127.0.0.1', 12345),
        'server': ('127.0.0.1', 8086),
    }
    middleware = auth.BearerAuth(accepted, max_body_size=8)
    asyncio.run(middleware(scope, receive, send))

    start = next(
        message for message in outbound
        if message['type'] == 'http.response.start')
    body = b''.join(
        message.get('body', b'') for message in outbound
        if message['type'] == 'http.response.body')
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = f'non-JSON body: {body!r}'
    actual = (start['status'], payload)
    expected = (413, {'error': 'request body too large'})
    assert actual == expected, (actual, expected)


def test_top_level_json_scalar_has_no_job_carrier(tmp):
    del tmp
    auth = _auth_module()
    try:
        result = auth.ambiguous_json_carrier(b'0')
    except Exception as failure:  # noqa: BLE001
        result = f'raised {type(failure).__name__}: {failure}'
    assert result is None, result


def test_malformed_json_is_not_a_duplicate_carrier(tmp):
    del tmp
    auth = _auth_module()
    try:
        result = auth.ambiguous_json_carrier(b'{')
    except Exception as failure:  # noqa: BLE001
        result = f'raised {type(failure).__name__}: {failure}'
    assert result is None, result


def test_live_listeners_keep_auth_state_and_body_limits_separate(tmp):
    test_mcp_server._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')

    first_tmp = Path(tmp) / 'first'
    second_tmp = Path(tmp) / 'second'
    with _util.bridge(first_tmp) as (first_base, _first_docroot):
        status, body = _util.post_json(first_base + '/sync-tabs', {
            'token': test_mcp_server.TOK,
            'tabs': [{'tabId': 'first', 'url': 'https://first.example.com',
                      'title': 'first'}],
        })
        assert status == 200, (status, body)
        with _util.bridge(second_tmp) as (second_base, _second_docroot):
            status, body = _util.post_json(second_base + '/sync-tabs', {
                'token': test_mcp_server.TOK,
                'tabs': [{'tabId': 'second',
                          'url': 'https://second.example.com',
                          'title': 'second'}],
            })
            assert status == 200, (status, body)

            _first_mod, first_port = _start_listener(first_base, 256)
            _second_mod, second_port = _start_listener(second_base, 100000)

            first_session = test_mcp_server._open_mcp_session(first_port)
            reply = test_mcp_server._call_mcp_tool(
                first_port, first_session, 'first-tabs', 'list_tabs')
            text = test_mcp_server._mcp_tool_text(reply)
            assert reply.get('result', {}).get('isError') is not True, reply
            assert 'first' in text and 'second' not in text, text

            oversized = _initialize_body(padding=2048)
            status, _session_id, raw = test_mcp_server._mcp_request(
                first_port, oversized)
            assert status == 413, (status, raw)
            assert b'request body too large' in raw, raw

            status, session_id, raw = test_mcp_server._mcp_request(
                second_port, oversized)
            assert status == 200 and session_id, (status, session_id, raw)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='mcpauth_'))
