"""Loading the MCP front end against a fixture bridge, and driving it.

Not a suite itself — run_tests.py only loads `test_*.py`.

Every suite that needs the MCP server loads it through here, so the settings
that decide WHERE it loads from are read in one place: a shell that exports
`DAEDALUS_*` cannot redirect one caller's load into another caller's process.
The same module carries the session and command-answering helpers, because they
are read off the same loaded module and the same `TOK` that names its queue;
splitting them would put one home in this file and the other in a suite, which
is the duplication this module exists to remove.
"""
import asyncio
import contextlib
import http.client
import importlib.util
import io
import json
import os
import time

import _daedalus_env
import _util

# find_spec asks whether the dependency is installed without importing it: an
# import kept only for its truthiness reads as dead code to every linter.
DEPS = all(importlib.util.find_spec(name) is not None
           for name in ('httpx', 'mcp', 'starlette'))

TOK = 'mcptok'
BRIDGE_ENV = {'DAEDALUS_TOKEN': TOK, 'TOKEN': ''}

# The listener's bind re-reads the process environment after the load's
# isolation has ended, so the token it authenticates against has to be in the
# environment and not only in the mapping the load applied. This publication
# therefore belongs to the fixture that owns the token, not to whichever suite
# happened to import it: a suite that imported a sibling for this side effect
# was reading a copy of another suite's environment, which is the defect this
# module exists to end.
os.environ.update(BRIDGE_ENV)


def _need_deps():
    if not DEPS:
        _util.skip('daedalus_mcp.server dependencies (httpx/mcp/starlette) '
                   'not installed')


def _load_mcp(base_url, mcp_port=None, max_body_size=None):
    applied = dict(BRIDGE_ENV, DAEDALUS_LOCAL_URL=base_url)
    if mcp_port is not None:
        applied['DAEDALUS_MCP_PORT'] = str(mcp_port)
    if max_body_size is not None:
        applied['DAEDALUS_MCP_MAX_BODY_SIZE'] = str(max_body_size)
    with _daedalus_env.isolated(applied):
        return _util.load(_util.ROOT / 'daedalus_mcp' / 'server.py',
                          'mcp_server_under_test_' + str(time.time_ns()))


def _start_in_thread(mod, local_url=None):
    # Rebinding reads the environment again after import isolation has ended.
    with _daedalus_env.isolated({}):
        return mod.start_in_thread(local_url)


def _wait_for_mcp(port, deadline=20):
    probe = {'jsonrpc': '2.0', 'id': 'wait-for-mcp',
             'method': 'initialize', 'params': {}}
    url = f'http://127.0.0.1:{port}/mcp'
    deadline = time.time() + deadline
    while True:
        try:
            status, raw = _util.request(url, 'POST', body=probe, timeout=1)
        except (OSError, http.client.HTTPException):
            if time.time() > deadline:
                raise AssertionError('MCP port never came up') from None
            time.sleep(0.1)
            continue
        try:
            error = json.loads(raw).get('error')
        except json.JSONDecodeError:
            error = None
        if status == 401 and error == 'missing Bearer token':
            return
        raise AssertionError(
            f'port {port} is answered by something that is not the MCP '
            f'server: {status} {raw[:200]!r}')


def _load_mcp_at_port(base_url, port, max_body_size=None):
    return _load_mcp(base_url, mcp_port=port, max_body_size=max_body_size)


def _start_mcp_in_process(base, max_body_size=None, diagnostics=None):
    """Start the in-process listener with its [MCP] prints captured.

    `diagnostics`, when given, receives the captured (stdout, stderr) texts.
    Neither capture is raced: the banner precedes the listener's first
    accepted request and the crash print precedes the serve thread's exit,
    so the success wait and the crash join end the redirect only after the
    print has flushed.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        mod = _load_mcp_at_port(base, 0, max_body_size=max_body_size)
        thread = _start_in_thread(mod)
        deadline = time.time() + 10
        try:
            while time.time() < deadline:
                if mod._bound.wait(timeout=0.05):
                    port = mod.bound_port
                    _wait_for_mcp(port)
                    return mod, port
                if mod.startup_error:
                    thread.join()
                    raise AssertionError(mod.startup_error)
            raise AssertionError(
                'MCP listener did not announce its port in 10s')
        finally:
            if diagnostics is not None:
                diagnostics.append((out.getvalue(), err.getvalue()))


def _mcp_request(port, body, authorizations=None, session_ids=None,
                 hosts=None, origins=None):
    """Send one MCP request while preserving repeated physical headers."""
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    connection = http.client.HTTPConnection(
        '127.0.0.1', port, timeout=10)
    connection.putrequest('POST', '/mcp', skip_host=hosts is not None)
    connection.putheader('Content-Type', 'application/json')
    connection.putheader('Accept', 'application/json, text/event-stream')
    connection.putheader('Content-Length', str(len(raw)))
    values = ([f'Bearer {TOK}'] if authorizations is None
              else authorizations)
    for authorization in values:
        connection.putheader('Authorization', authorization)
    for session_id in session_ids or ():
        connection.putheader('Mcp-Session-Id', session_id)
    for host in hosts or ():
        connection.putheader('Host', host)
    for origin in origins or ():
        connection.putheader('Origin', origin)
    connection.endheaders(raw)
    response = connection.getresponse()
    status = response.status
    session_id = response.getheader('Mcp-Session-Id')
    response_body = response.read()
    connection.close()
    return status, session_id, response_body


def _mcp_payload(raw):
    """Decode either a JSON MCP response or its streamable-HTTP SSE wrapper."""
    for line in raw.splitlines():
        if line.startswith(b'data: '):
            return json.loads(line[6:])
    return json.loads(raw)


def _open_mcp_session(port):
    """Initialize one live MCP session and return its transport id."""
    initialize = {
        'jsonrpc': '2.0', 'id': 'initialize', 'method': 'initialize',
        'params': {'protocolVersion': '2024-11-05', 'capabilities': {},
                   'clientInfo': {'name': 'security-regression',
                                  'version': '0'}}}
    status, session_id, raw = _mcp_request(port, initialize)
    assert status == 200 and session_id, (status, session_id, raw)
    status, _unused, raw = _mcp_request(
        port, {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        session_ids=(session_id,))
    assert status == 202, (status, raw)
    return session_id


def _call_mcp_tool(port, session_id, request_id, name, arguments=None):
    """Call one tool through the authenticated live MCP transport."""
    status, _unused, raw = _mcp_request(
        port,
        {'jsonrpc': '2.0', 'id': request_id, 'method': 'tools/call',
         'params': {'name': name, 'arguments': arguments or {}}},
        session_ids=(session_id,))
    assert status == 200, (status, raw)
    return _mcp_payload(raw)


def _mcp_tool_text(reply):
    """Join the text blocks returned by one MCP tools/call response."""
    return ''.join(
        item.get('text', '')
        for item in reply.get('result', {}).get('content', [])
        if isinstance(item, dict))


def _answer_mcp_command(base, docroot, mod, call, result, tab='extension'):
    """Run one MCP tool that sends a command, and answer what it sends.

    `call` is invoked with `wait=False`, so the bridge hands back the command
    it enqueued and this thread holds its bytes without reading the queue
    directory for them — nothing blocks, so there is no worker and no poll.
    Returns (what the tool returned, the payload the bridge got).

    `docroot` is unused now that the queue is not read; it stays in the
    signature so a call site that passes the fixture unchanged keeps working.
    """
    del docroot
    # The token is a ContextVar, and a tool call reaches the bridge through
    # it: this is what BearerAuth does per request.
    mod._token.set(TOK)
    value = asyncio.run(call())
    queued = value.get('command') if isinstance(value, dict) else None
    if not queued:
        raise AssertionError(
            f'the tool reported no command: {value!r}')
    status, _ = _util.post_json(base + '/result', {
        'token': TOK, 'tabId': tab, 'id': queued['id'], 'result': result,
        'error': None, 'ts': 1, '_did': queued['_did']})
    assert status == 200, status
    return value, queued
