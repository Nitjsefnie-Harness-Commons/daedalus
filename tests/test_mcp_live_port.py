#!/usr/bin/env python3
"""The live MCP listener's HTTP surface.

Split from tests/test_mcp_server.py.
"""
import contextlib
import http.client
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _mcp_load  # noqa: E402

DEPS = _mcp_load.DEPS
if DEPS:
    import logging
    logging.getLogger('httpx').setLevel(
        logging.WARNING)  # quiet per-request logs
    logging.getLogger('mcp').setLevel(logging.WARNING)  # quiet mcp INFO logs


def _await_mcp_line(output, proc):
    """Read the child's actual MCP port off its drained stdout.

    The startup line follows the bind, so its number is the bound port
    itself. A crash line is raised verbatim, not translated. The wait has no
    deadline: one here would bound the front end's import now that readiness
    does not cover it, and an exited child is all it can honestly watch for.
    """
    seen = 0
    while True:
        pending = output[seen:]
        seen += len(pending)
        for line in pending:
            match = re.search(
                r'\[MCP\] streamable-http on 127\.0\.0\.1:(\d+)', line)
            if match:
                port = int(match.group(1))
                if port == 0:
                    raise AssertionError(
                        'MCP announced the configured port, not the bound '
                        f'one: {line!r}')
                return port
            if '[MCP] serve crashed:' in line:
                raise AssertionError(line.rstrip())
        if proc.poll() is not None:
            raise AssertionError(
                'the bridge exited before announcing its MCP port:\n'
                + ''.join(output))
        time.sleep(0.05)


@contextlib.contextmanager
def _bridge_with_live_mcp(tmp, env):
    """Yield (base, mcp_port) with the bridge child's MCP listener live.

    The child binds MCP to port 0 and prints the actual port, which the
    fixture's drain thread relays here; a crash arrives as its own line.
    """
    output, child = [], []
    with _util.bridge(tmp, env={**env, 'DAEDALUS_MCP_PORT': '0'},
                      output=output, proc_out=child) as (base, _docroot):
        port = _await_mcp_line(output, child[0])
        _mcp_load._wait_for_mcp(port)
        yield base, port


def test_port_zero_bridge_mcp_list_tabs_round_trip(tmp):
    """The child binds the bridge first, so MCP reaches its
    actual port at 0."""
    _mcp_load._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    env = {'DAEDALUS_TOKEN': _mcp_load.TOK, 'TOKEN': ''}
    with _bridge_with_live_mcp(tmp, env) as (base, port):
        status, body = _util.post_json(base + '/sync-tabs', {
            'token': _mcp_load.TOK,
            'tabs': [{'tabId': 'ephemeral-tab',
                      'url': 'https://example.com/ephemeral',
                      'title': 'Ephemeral'}],
        })
        assert status == 200, (status, body)
        session_id = _mcp_load._open_mcp_session(port)
        reply = _mcp_load._call_mcp_tool(
            port, session_id, 'port-zero-tabs', 'list_tabs')
        text = _mcp_load._mcp_tool_text(reply)
        assert reply.get('result', {}).get('isError') is not True, reply
        assert ('ephemeral-tab' in text
                and 'example.com/ephemeral' in text), text


def test_an_unauthenticated_body_is_refused_before_it_is_read(tmp):
    """Credentials are decided before the body is parsed, and it is capped.

    Size is pinned separately: only an authenticated caller reaches the cap.
    """
    _mcp_load._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    env = {'DAEDALUS_TOKEN': _mcp_load.TOK, 'TOKEN': '',
           'DAEDALUS_MCP_MAX_BODY_SIZE': '4096'}
    with _bridge_with_live_mcp(tmp, env) as (_base, port):
        url = f'http://127.0.0.1:{port}/mcp'
        duplicate_carrier = (
            b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":'
            b'{"name":"segment_job","arguments":{"job":"a","job":"b"}}}')

        status, body = _util.request(
            url, 'POST', body=duplicate_carrier,
            headers={'Content-Type': 'application/json'})
        assert status == 401, (status, body)

        status, body = _util.request(
            url, 'POST', body=duplicate_carrier,
            headers={'Content-Type': 'application/json',
                     'Authorization': f'Bearer {_mcp_load.TOK}',
                     'Accept': 'application/json, text/event-stream'})
        assert status == 400, (status, body)
        assert b'duplicate job' in body, body

        oversized = json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
            'params': {'pad': 'x' * 20000},
        }).encode()
        status, body = _util.request(
            url, 'POST', body=oversized,
            headers={'Content-Type': 'application/json',
                     'Authorization': f'Bearer {_mcp_load.TOK}',
                     'Accept': 'application/json, text/event-stream'})
        assert status == 413, (status, body)
        assert b'too large' in body, body


def test_bearer_middleware_requires_configured_token_on_live_mcp_port(tmp):
    """Only the configured bridge token may pass the live MCP middleware."""
    _mcp_load._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    env = {'DAEDALUS_TOKEN': _mcp_load.TOK, 'TOKEN': ''}
    with _bridge_with_live_mcp(tmp, env) as (_base, port):
        url = f'http://127.0.0.1:{port}/mcp'
        rpc = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}
        status, body = _util.post_json(url, rpc)
        assert status == 401, (status, body)
        assert body['error'] == 'missing Bearer token', body
        for bad in ('a/b', 'a.b', ''):
            status, body = _util.request(
                url, 'POST', body=rpc,
                headers={'Authorization': f'Bearer {bad}'})
            assert status == 401, (bad, status, body)
        status, body = _util.request(
            url, 'POST', body=rpc,
            headers={'Authorization': 'Bearer othermcptok',
                     'Accept': 'application/json, text/event-stream'})
        assert status == 401, (status, body)
        assert json.loads(body)['error'] == 'unauthorized', body
        # The configured bearer reaches MCP itself; the deliberately minimal
        # handshake may still receive a protocol error, but auth must open.
        status, _ = _util.request(
            url, 'POST', body=rpc,
            headers={'Authorization': f'Bearer {_mcp_load.TOK}',
                     'Accept': 'application/json, text/event-stream'})
        assert status != 401, status


def test_bearer_middleware_rejects_duplicate_authorization_headers(tmp):
    """MCP authentication never selects one of two bearer credentials."""
    _mcp_load._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    env = {'DAEDALUS_TOKEN': _mcp_load.TOK, 'TOKEN': ''}
    with _bridge_with_live_mcp(tmp, env) as (base, port):
        rpc = json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}
        }).encode()
        orders = ((f'Bearer {_mcp_load.TOK}', 'Bearer othermcptok'),
                  ('Bearer othermcptok', f'Bearer {_mcp_load.TOK}'))
        for authorizations in orders:
            connection = http.client.HTTPConnection(
                '127.0.0.1', port, timeout=10)
            connection.putrequest('POST', '/mcp')
            connection.putheader('Content-Type', 'application/json')
            connection.putheader(
                'Accept', 'application/json, text/event-stream')
            connection.putheader('Content-Length', str(len(rpc)))
            for authorization in authorizations:
                connection.putheader('Authorization', authorization)
            connection.endheaders(rpc)
            response = connection.getresponse()
            status = response.status
            raw = response.read()
            connection.close()
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = {}
            assert status == 400 and body.get('error') == (
                'duplicate Authorization header'), (
                    authorizations, status, body)
            health_status, health = _util.get_json(base + '/health')
            assert health_status == 200 and health['ok'] is True, (
                health_status, health)

        status, _ = _util.request(
            f'http://127.0.0.1:{port}/mcp', 'POST', body=rpc,
            headers={'Authorization': f'Bearer {_mcp_load.TOK}',
                     'Accept': 'application/json, text/event-stream'})
        assert status not in (400, 401), status


def test_a_parser_refusal_carries_the_date_its_connection_refreshed(tmp):
    """A refusal uvicorn answers itself still carries a current Date.

    It never reaches the tick middleware, so the refresh that reaches it is
    the one the connection does. Two refusals either side of a gap carry
    different dates; without that refresh both carry the last one, however
    long the gap. Compared as values, so nothing depends on the clock.
    """
    _mcp_load._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    _mod, port = _mcp_load._start_mcp_in_process('http://127.0.0.1:1')
    first = _mcp_load._refusal_date(port)
    time.sleep(2)
    assert first != _mcp_load._refusal_date(port), first


def test_mcp_initialize_accepts_nested_application_token_members(tmp):
    """Nested application members named token are not MCP credentials."""
    _mcp_load._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    with _util.bridge(tmp, env=_mcp_load.BRIDGE_ENV) as (base, _docroot):
        _mod, port = _mcp_load._start_mcp_in_process(base)
        initialize = {
            'jsonrpc': '2.0',
            'id': 'nested-application-members',
            'method': 'initialize',
            'params': {
                'protocolVersion': '2024-11-05',
                'capabilities': {
                    'experimental': {
                        'alpha': {'token': 'red'},
                        'beta': {'token': 'blue'},
                    },
                },
                'clientInfo': {'name': 'application-data', 'version': '0'},
            },
        }
        status, session_id, raw = _mcp_load._mcp_request(port, initialize)
        assert status == 200 and session_id, (status, session_id, raw)


def test_bearer_middleware_fails_closed_without_configured_token(tmp):
    """A missing bridge-token configuration leaves no bearer authorized."""
    _mcp_load._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    env = {'DAEDALUS_TOKEN': '', 'TOKEN': ''}
    with _bridge_with_live_mcp(tmp, env) as (_base, port):
        url = f'http://127.0.0.1:{port}/mcp'
        rpc = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}
        status, body = _util.request(
            url, 'POST', body=rpc,
            headers={'Authorization': f'Bearer {_mcp_load.TOK}',
                     'Accept': 'application/json, text/event-stream'})
        assert status == 401, (status, body)
        assert json.loads(body)['error'] == 'unauthorized', body


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
