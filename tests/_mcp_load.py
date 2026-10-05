"""Loading the MCP front end against a fixture bridge, and driving it.

Not a suite itself — run_tests.py only loads `test_*.py`.

Every suite that needs the MCP server loads it through here, so the
settings that decide WHERE it loads from are read in one place: a shell
that exports `DAEDALUS_*` cannot redirect one caller's load into another
caller's process. The session and command-answering helpers read off the
same loaded module and `TOK`.
"""
import asyncio
import contextlib
import http.client
import importlib.util
import io
import json
import os
import socket
import sys
import time
import types

import _daedalus_env
import _util

# find_spec asks whether the dependency is installed without importing it: an
# import kept only for its truthiness reads as dead code to every linter.
DEPS = all(importlib.util.find_spec(name) is not None
           for name in ('httpx', 'mcp', 'starlette'))

TOK = 'mcptok'
BRIDGE_ENV = {'DAEDALUS_TOKEN': TOK, 'TOKEN': ''}

# The listener's bind re-reads the process environment after the load's
# isolation has ended, so the token has to be published here, beside the
# fixture that owns it — a suite that imported a sibling for this side
# effect read a copy of another suite's environment.
os.environ.update(BRIDGE_ENV)


class _FakeUvicornServer:
    """Stands where uvicorn.Server stands, minus the OS behind it: `handed`
    and `built` are class state so the front end can be subclassed the way
    it subclasses the real one — `_serve` derives its own Server from
    whatever `uvicorn.Server` names, and a lambda is not a base class.
    """
    handed = []
    built = []

    def __init__(self, config):
        self.config = config
        self.server_state = types.SimpleNamespace(default_headers=[])
        self.should_exit = False
        type(self).built.append(self)

    async def on_tick(self, counter):
        del counter
        return False

    async def startup(self, sockets=None):
        pass

    def run(self, sockets=None):
        type(self).handed.extend(sockets or ())

        async def serve():
            await self.startup(sockets)
            self.should_exit = True
        asyncio.run(serve())


class _FakeConfig:
    """Stands where uvicorn.Config stands, for the members `_serve` reads.

    `load()` is what resolves `http` into a protocol class on the real
    Config, so the fake does the same; `date_header` and `encoded_headers`
    are the two the tick reads to rebuild the cached headers.
    """

    def __init__(self, app, **settings):
        self.app = app
        self.settings = settings
        self.date_header = True
        self.encoded_headers = [(b'server', b'uvicorn')]
        self.loaded = False
        self.http_protocol_class = _FakeProtocol

    def load(self):
        self.loaded = True


class _FakeProtocol:
    """Stands where uvicorn's HTTP protocol class stands."""

    def __init__(self, config=None, server_state=None, app_state=None,
                 _loop=None):
        self.config = config
        self.server_state = server_state
        self.app_state = app_state
        self.connections = 0

    def connection_made(self, transport):
        self.connections += 1


def _serve_with_fake_uvicorn(mod):
    """Run _serve to completion without its real front end.

    The fake stands where uvicorn.Config, uvicorn.Server and the HTTP
    protocol class stand, serving nothing. Returns (handed sockets, the
    captured banner text, the built Server instances); the caller closes
    any real socket in the first.
    """
    handed, built, banner = [], [], io.StringIO()
    names = {'Config': _FakeConfig,
             'Server': type('Server', (_FakeUvicornServer,),
                            {'handed': handed, 'built': built})}
    previous = sys.modules.get('uvicorn')
    fake = types.ModuleType('uvicorn')
    fake.__dict__.update(names)
    sys.modules['uvicorn'] = fake
    try:
        with contextlib.redirect_stdout(banner):
            mod._serve()
    finally:
        if previous is None:
            sys.modules.pop('uvicorn', None)
        else:
            sys.modules['uvicorn'] = previous
    return handed, banner.getvalue(), built


def _refusal_date(port):
    """The Date one parser-level refusal answers with, over a new socket."""
    sock = socket.create_connection(('127.0.0.1', port), timeout=10)
    try:
        sock.sendall(b'NOT-A-REQUEST\r\n\r\n')
        raw = b''
        while b'\r\n\r\n' not in raw:
            raw += sock.recv(4096)
    finally:
        sock.close()
    assert raw.startswith(b'HTTP/1.1 400'), raw[:120]
    for line in raw.split(b'\r\n'):
        if line[:5].lower() == b'date:':
            return line.split(b':', 1)[1].strip()
    raise AssertionError(f'the refusal carried no Date: {raw[:200]!r}')


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
    accepted request and the crash print precedes the serve thread's exit.
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


@contextlib.contextmanager
def surface_responder_errors(thread, errors, timeout, stop=None):
    """Join a responder thread and prefer its failure to the caller's.

    A responder that fails in its own thread leaves the caller to report a
    timeout instead, so the two are joined and the responder's own
    exception is re-raised in the caller's place. `stop` is set first when
    given, for a responder that loops until told to stop.
    """
    try:
        yield
    finally:
        if stop is not None:
            stop.set()
        thread.join(timeout=timeout)
        failure = next((item for item in errors
                        if isinstance(item, Exception)), None)
        if failure is not None:
            raise failure from None


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
    # Named before the answer, not while posting it: a truthy command that
    # is missing either field would otherwise die as a bare KeyError on the
    # next line, naming neither the tool nor what it answered.
    if not isinstance(queued, dict) or not {'id', '_did'} <= set(queued):
        raise AssertionError(
            "a wait=False send answers {'command': <the enqueued command>}"
            f' with its id and delivery id; got {value!r}')
    status, _ = _util.post_json(base + '/result', {
        'token': TOK, 'tabId': tab, 'id': queued['id'], 'result': result,
        'error': None, 'ts': 1, '_did': queued['_did']})
    assert status == 200, status
    return value, queued
