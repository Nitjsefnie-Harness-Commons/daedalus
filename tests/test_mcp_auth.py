#!/usr/bin/env python3
"""Multi-listener regression coverage for the MCP auth middleware."""
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import test_mcp_server  # noqa: E402


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


def test_live_listeners_keep_auth_state_and_body_limits_separate(tmp):
    """A later listener cannot replace an earlier listener's auth inputs."""
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
