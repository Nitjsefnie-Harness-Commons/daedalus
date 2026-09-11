#!/usr/bin/env python3
"""Suite for scripts/mcp_probe.py, the documented manual MCP client.

The probe is run as a subprocess against a stub that answers the way the
pinned MCP transport does, because the defect it pins was invisible to any
test that spoke to the probe's functions directly: the transport answers a
notification with 202 and no body, and the probe's helper read that body
as JSON.
"""
import contextlib
import http.server
import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


PROBE = _util.ROOT / 'scripts' / 'mcp_probe.py'
TOKEN = 'probetok'
SESSION = 'probe-session'
TOOLS = [{'name': 'ping', 'description': 'Round-trip a document.title'}]


class _McpStubHandler(http.server.BaseHTTPRequestHandler):
    """A streamable-HTTP MCP endpoint reduced to what the probe sends it.

    `notifications/initialized` is answered the way mcp 2.1.1 answers it:
    without an id it is a notification and earns 202 with an empty body,
    since there is no JSON-RPC id to answer; with an id it is a request for
    a method the server does not serve, and earns a -32601 error frame.
    """

    seen = []
    # Methods answered with 200 and no body at all, to stand in for a
    # front end that acknowledges a request without answering it.
    empty_for = frozenset()

    def do_POST(self):  # noqa: N802  (http.server's spelling)
        length = int(self.headers.get('Content-Length') or 0)
        payload = json.loads(self.rfile.read(length) or b'{}')
        self.seen.append({
            'method': payload.get('method'),
            'has_id': 'id' in payload,
            'id': payload.get('id'),
            'session': self.headers.get('Mcp-Session-Id'),
            'authorization': self.headers.get('Authorization'),
        })
        method = payload.get('method')
        if method in self.empty_for:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', '0')
            self.send_header('Mcp-Session-Id', SESSION)
            self.end_headers()
            return
        if method == 'notifications/initialized' and 'id' not in payload:
            self.send_response(202)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if method == 'notifications/initialized':
            frame = json.dumps({'jsonrpc': '2.0', 'id': payload['id'],
                                'error': {'code': -32601,
                                          'message': 'Method not found'}})
            body = ('event: message\ndata: ' + frame + '\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if method == 'initialize':
            result = {'protocolVersion': '2024-11-05', 'capabilities': {},
                      'serverInfo': {'name': 'stub', 'version': '0'}}
        elif method == 'tools/list':
            result = {'tools': TOOLS}
        elif method == 'tools/call':
            result = {'content': [{'type': 'text', 'text': json.dumps(
                {'echo': payload.get('params')})}]}
        else:
            result = {}
        body = json.dumps({'jsonrpc': '2.0', 'id': payload.get('id'),
                           'result': result}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Mcp-Session-Id', SESSION)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        del format, args


@contextlib.contextmanager
def _mcp_stub(empty_for=()):
    _McpStubHandler.seen = []
    _McpStubHandler.empty_for = frozenset(empty_for)
    server = http.server.ThreadingHTTPServer(
        ('127.0.0.1', 0), _McpStubHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}/mcp'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _run_probe(url, *argv):
    if importlib.util.find_spec('httpx') is None:
        _util.skip('scripts/mcp_probe.py dependency (httpx) not installed')
    env = dict(os.environ, TOKEN=TOKEN, DAEDALUS_MCP_URL=url,
               PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run(
        [sys.executable, str(PROBE), *argv], cwd=str(_util.ROOT), env=env,
        capture_output=True, text=True, encoding='utf-8', timeout=60)


def test_the_probe_survives_the_bodiless_answer_to_its_notification(tmp):
    """`list` sends a real notification and survives the 202 it earns.

    rpc() gave every message an id, `notifications/initialized` included,
    so the transport answered it as an unknown request and the handshake
    was never completed. Without the id the transport answers 202 with no
    body, and the helper ended with `r.json()` on that nothing: dropping
    the id alone moved the traceback rather than removing it.
    """
    del tmp
    with _mcp_stub() as url:
        run = _run_probe(url, 'list')
        seen = list(_McpStubHandler.seen)
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
    assert 'ping' in run.stdout, run.stdout
    assert 'Round-trip a document.title' in run.stdout, run.stdout
    methods = [request['method'] for request in seen]
    assert methods == ['initialize', 'notifications/initialized',
                       'tools/list'], methods
    # Absent, not null: `id: null` is still a request-shaped message.
    assert seen[1]['has_id'] is False, seen[1]
    assert seen[0]['id'] and seen[2]['id'], seen
    # The session the initialize answer opened carries through the
    # bodiless notification to the request that lists the tools.
    assert seen[2]['session'] == SESSION, seen
    assert seen[2]['authorization'] == f'Bearer {TOKEN}', seen


def test_the_probe_calls_a_tool_after_the_bodiless_notification(tmp):
    del tmp
    with _mcp_stub() as url:
        run = _run_probe(url, 'call', 'ping', '{"tab_id": "t1"}')
        seen = list(_McpStubHandler.seen)
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
    printed = json.loads(run.stdout)
    echoed = json.loads(printed['content'][0]['text'])
    assert echoed == {'echo': {'name': 'ping', 'arguments': {'tab_id': 't1'}}}
    methods = [request['method'] for request in seen]
    assert methods == ['initialize', 'notifications/initialized',
                       'tools/call'], methods
    assert seen[1]['has_id'] is False, seen[1]
    assert seen[2]['session'] == SESSION, seen


def test_a_bodiless_answer_to_a_request_is_a_failure_not_a_result(tmp):
    """initialize, tools/list and tools/call each need a body.

    Routing only the two tool calls through answer() let an empty answer
    to initialize proceed to the notification and the listing, print the
    tools and exit 0: an initialization the server never confirmed was
    reported as a working front end.
    """
    del tmp
    for method, argv in (('initialize', ['list']),
                         ('tools/list', ['list']),
                         ('tools/call', ['call', 'ping', '{}'])):
        with _mcp_stub(empty_for={method}) as url:
            run = _run_probe(url, *argv)
            seen = list(_McpStubHandler.seen)
        assert run.returncode != 0, (method, run.stdout, run.stderr)
        assert method in run.stderr, (method, run.stderr)
        assert 'Traceback' not in run.stderr, (method, run.stderr)
        assert seen[-1]['method'] == method, (method, seen)
        if method == 'initialize':
            assert 'ping' not in run.stdout, (method, run.stdout)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
