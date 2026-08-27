#!/usr/bin/env python3
"""Suite for mcp_server.py, bounded by what its dependencies allow.

mcp_server needs httpx, mcp and starlette, which a public checkout does not
necessarily have installed. Where they are missing every test here skips
cleanly; a suite that failed on an optional front end would be wrong. Where
they are present the tool handlers are exercised against a real bridge, with
the bearer token set the way the middleware would set it.
"""
import asyncio
import base64
import contextlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import importlib.util
import re
import socket
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _cmdqueue import clear_command_queue, wait_for_command  # noqa: E402

# find_spec asks whether the dependency is installed without importing it --
# an import kept only for its truthiness reads as dead code to every linter,
# and pylint is right that it is.
DEPS = all(importlib.util.find_spec(name) is not None
           for name in ('httpx', 'mcp', 'starlette'))
if DEPS:
    import logging
    logging.getLogger('httpx').setLevel(logging.WARNING)  # quiet per-request logs

TOK = 'mcptok'
os.environ['TOKEN'] = ''
os.environ['DAEDALUS_TOKEN'] = TOK


def _need_deps():
    if not DEPS:
        _util.skip('mcp_server dependencies (httpx/mcp/starlette) not installed')


def _load_mcp(base_url):
    """Import mcp_server.py with a private bridge session bound at import."""
    prev = os.environ.get('DAEDALUS_LOCAL_URL')
    root = str(_util.ROOT)
    added_root = root not in sys.path
    if added_root:
        sys.path.insert(0, root)
    os.environ['DAEDALUS_LOCAL_URL'] = base_url
    try:
        return _util.load(_util.ROOT / 'mcp_server.py',
                          'mcp_server_under_test_' + str(time.time_ns()))
    finally:
        if added_root:
            sys.path.remove(root)
        if prev is None:
            os.environ.pop('DAEDALUS_LOCAL_URL', None)
        else:
            os.environ['DAEDALUS_LOCAL_URL'] = prev


def _wait_for_mcp(port, deadline=20):
    """Wait until the live MCP listener answers — and refuse any other listener.

    A bare TCP accept proves only that SOMETHING bound the port. These tests
    reserve the MCP port and then start a bridge, so a collision between the
    two allocations used to put the bridge itself on this port; every MCP
    request then failed 'authentication' with the bridge's 400 bad-token
    answer, which reads like a security regression. Probe with an
    unauthenticated POST /mcp instead: the real MCP middleware answers 401
    'missing Bearer token', and any other answer fails the test with the
    listener's actual response as the diagnosis.
    """
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


def _load_mcp_at_port(base_url, port):
    """Load the MCP front end with one explicit listener port."""
    previous = os.environ.get('DAEDALUS_MCP_PORT')
    os.environ['DAEDALUS_MCP_PORT'] = str(port)
    try:
        return _load_mcp(base_url)
    finally:
        if previous is None:
            os.environ.pop('DAEDALUS_MCP_PORT', None)
        else:
            os.environ['DAEDALUS_MCP_PORT'] = previous


# The MCP listener binds port 0 everywhere in this suite: the kernel picks the
# number, so no drawn port exists for a concurrent process to take. The actual
# port arrives through an explicit readiness channel — the module's readiness
# event in-process, or the child's startup line on its drained stdout.


def _start_mcp_in_process(base):
    """Load and start the MCP listener on an ephemeral port; return (mod, port).

    No draw, no retry: the module announces the port it actually bound
    through its readiness event, and a startup crash through startup_error,
    so the original error is what surfaces.
    """
    mod = _load_mcp_at_port(base, 0)
    mod.start_in_thread()
    deadline = time.time() + 10
    while time.time() < deadline:
        if mod._bound.wait(timeout=0.05):
            port = mod.bound_port
            _wait_for_mcp(port)
            return mod, port
        if mod.startup_error:
            raise AssertionError(mod.startup_error)
    raise AssertionError('MCP listener did not announce its port in 10s')


def _await_mcp_line(output, timeout=10):
    """Read the child's actual MCP port off its drained stdout.

    The startup line follows the bind, so its number is the bound port
    itself. A crash line is raised with its own text — it is the original
    error, not something to translate or retry.
    """
    deadline = time.time() + timeout
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
                        f'MCP announced the configured port, not the bound one: {line!r}')
                return port
            if '[MCP] serve crashed:' in line:
                raise AssertionError(line.rstrip())
        if time.time() > deadline:
            raise AssertionError(
                'MCP listener did not announce its port in 10s:\n'
                + ''.join(output))
        time.sleep(0.05)


@contextlib.contextmanager
def _bridge_with_live_mcp(tmp, env):
    """Yield (base, mcp_port) with the bridge child's MCP listener live.

    The child binds its MCP listener to port 0 and prints the actual port on
    its stdout, which the bridge fixture's drain thread relays here. No drawn
    number, no retry: a startup crash arrives as its own line, verbatim.
    """
    output = []
    child_env = {**env, 'DAEDALUS_MCP_PORT': '0'}
    with _util.bridge(tmp, env=child_env, output=output) as (base, _docroot):
        port = _await_mcp_line(output)
        _wait_for_mcp(port)
        yield base, port


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
        'jsonrpc': '2.0',
        'id': 'initialize',
        'method': 'initialize',
        'params': {
            'protocolVersion': '2024-11-05',
            'capabilities': {},
            'clientInfo': {'name': 'security-regression', 'version': '0'},
        },
    }
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


def test_wait_for_mcp_refuses_a_non_mcp_listener(tmp):
    """A foreign listener on the MCP port must fail the probe, not authenticate.

    The readiness probe used to accept any TCP listener, so a bridge that won
    the port race made every later MCP request fail 'authentication' with the
    bridge's bad-token 400 — a misleading diagnosis for a port collision.
    """
    with _util.bridge(tmp, env={'DAEDALUS_MCP_PORT': '0'}) as (base, _docroot):
        port = int(base.rsplit(':', 1)[1])
        try:
            _wait_for_mcp(port)
        except AssertionError as failure:
            assert 'not the MCP server' in str(failure), failure
        else:
            raise AssertionError(
                'a bridge listener passed the MCP readiness probe')


def test_module_imports_and_exposes_tools(tmp):
    _need_deps()
    mod = _load_mcp('http://127.0.0.1:1')  # URL unused here
    # FastMCP's @tool() returns the original coroutine functions, so the tool
    # surface is directly callable.
    for name in ('list_tabs', 'ping', 'navigate', 'screenshot',
                 'segment_status'):
        fn = getattr(mod, name, None)
        assert callable(fn), f'mcp_server.{name} missing'


def test_local_url_derives_from_the_bridge_port(tmp):
    """The MCP bridge client follows DAEDALUS_PORT unless explicitly overridden.

    The module used to hard-default its in-process bridge URL to
    127.0.0.1:8081, so on any other documented DAEDALUS_PORT the MCP server
    started, authenticated clients, and then sent every tool call to the
    wrong local port. DAEDALUS_LOCAL_URL remains the explicit override for
    a standalone deployment.
    """
    del tmp
    _need_deps()
    saved = {key: os.environ.get(key)
             for key in ('DAEDALUS_PORT', 'DAEDALUS_LOCAL_URL')}
    root = str(_util.ROOT)
    added_root = root not in sys.path
    if added_root:
        sys.path.insert(0, root)
    try:
        def fresh(tag):
            return _util.load(_util.ROOT / 'mcp_server.py',
                              'mcp_server_url_' + tag + str(time.time_ns()))
        os.environ.pop('DAEDALUS_LOCAL_URL', None)
        os.environ['DAEDALUS_PORT'] = '54321'
        assert fresh('derived').LOCAL_URL == 'http://127.0.0.1:54321'
        os.environ['DAEDALUS_LOCAL_URL'] = 'http://127.0.0.1:9999'
        assert fresh('override').LOCAL_URL == 'http://127.0.0.1:9999'
        os.environ.pop('DAEDALUS_LOCAL_URL', None)
        os.environ.pop('DAEDALUS_PORT', None)
        assert fresh('fallback').LOCAL_URL == 'http://127.0.0.1:8081'
    finally:
        if added_root:
            sys.path.remove(root)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _module_list_tabs(mod):
    async def fetch():
        response = await mod.bridge.http_client().get(
            '/tabs', headers={'Authorization': f'Bearer {TOK}'})
        response.raise_for_status()
        return response.json()
    return asyncio.run(fetch())


def test_fresh_mcp_modules_keep_distinct_bound_transports(tmp):
    """Fresh callers share the transport class but retain their own bridges."""
    _need_deps()
    with _util.bridge(Path(tmp) / 'first') as (first_base, _first_docroot):
        _util.post_json(first_base + '/sync-tabs', {'token': TOK, 'tabs': [
            {'tabId': 'first', 'url': 'https://first.example.com',
             'title': 'first'}]})
        with _util.bridge(Path(tmp) / 'second') as (second_base, _second_docroot):
            _util.post_json(second_base + '/sync-tabs', {'token': TOK, 'tabs': [
                {'tabId': 'second', 'url': 'https://second.example.com',
                 'title': 'second'}]})
            first_mod = _load_mcp(first_base)
            second_mod = _load_mcp(second_base)
            assert first_mod.BridgeTransport is second_mod.BridgeTransport
            assert first_mod.bridge.transport is not (
                second_mod.bridge.transport)
            first_tabs = _module_list_tabs(first_mod)
            second_tabs = _module_list_tabs(second_mod)
            assert first_tabs[0]['title'] == 'first', first_tabs
            assert second_tabs[0]['title'] == 'second', second_tabs


def test_two_module_routing_regression_is_sensitive_to_url_blind_singleton(tmp):
    """The observed second marker proves the URL-blind mutant is active."""
    _need_deps()
    with _util.bridge(Path(tmp) / 'first') as (first_base, _first_docroot):
        _util.post_json(first_base + '/sync-tabs', {'token': TOK, 'tabs': [
            {'tabId': 'first', 'url': 'https://first.example.com',
             'title': 'first'}]})
        with _util.bridge(Path(tmp) / 'second') as (second_base, _second_docroot):
            _util.post_json(second_base + '/sync-tabs', {'token': TOK, 'tabs': [
                {'tabId': 'second', 'url': 'https://second.example.com',
                 'title': 'second'}]})
            first_mod = _load_mcp(first_base)
            second_mod = _load_mcp(second_base)
            old_client = {'value': None}

            def url_blind_client(_local_url=None):
                if old_client['value'] is None:
                    old_client['value'] = second_mod.bridge.transport.client()
                return old_client['value']

            original = first_mod.bridge.http_client
            first_mod.bridge.http_client = url_blind_client
            try:
                tabs = _module_list_tabs(first_mod)
            finally:
                first_mod.bridge.http_client = original
            assert tabs[0]['title'] == 'second', tabs


def test_transport_clients_are_isolated_by_event_loop(tmp):
    """A keep-alive client from one loop is not reused by another loop."""
    del tmp
    _need_deps()

    class MarkerHandler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):  # pylint: disable=invalid-name
            body = b'{"marker":"keepalive"}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), MarkerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        mod = _load_mcp(base)
        first = mod.BridgeTransport(base)
        second = mod.BridgeTransport(base)

        async def fetch(transport):
            try:
                response = await transport.client().get('/marker')
                response.raise_for_status()
                return response.json()
            finally:
                await mod.BridgeTransport.close_current_loop_clients()

        assert asyncio.run(fetch(first)) == {'marker': 'keepalive'}
        assert asyncio.run(fetch(second)) == {'marker': 'keepalive'}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_mcp_lifespan_closes_loop_clients(tmp):
    """The MCP app closes bridge clients when its own lifespan shuts down."""
    del tmp
    _need_deps()
    mod = _load_mcp_at_port('http://127.0.0.1:1', 0)
    app_box = {}
    original_factory = mod.mcp.streamable_http_app

    def capture_app(**settings):
        app_box['value'] = original_factory(**settings)
        return app_box['value']

    mod.mcp.streamable_http_app = capture_app

    class FakeConfig:
        def __init__(self, app, **_settings):
            self.app = app

    class FakeServer:
        def __init__(self, config):
            self.config = config

        def run(self, sockets=None):
            for server_socket in sockets or ():
                server_socket.close()

    fake_uvicorn = types.ModuleType('uvicorn')
    fake_uvicorn.Config = FakeConfig
    fake_uvicorn.Server = FakeServer
    previous_uvicorn = sys.modules.get('uvicorn')
    sys.modules['uvicorn'] = fake_uvicorn
    try:
        mod._serve()
    finally:
        mod.mcp.streamable_http_app = original_factory
        if previous_uvicorn is None:
            sys.modules.pop('uvicorn', None)
        else:
            sys.modules['uvicorn'] = previous_uvicorn

    assert not mod.startup_error, mod.startup_error
    app = app_box['value']

    async def drive_lifespan():
        loop = asyncio.get_running_loop()
        async with app.router.lifespan_context(app):
            mod.BridgeTransport('http://127.0.0.1:1').client()
            assert len(mod.BridgeTransport.clients[loop]) == 1
        return not mod.BridgeTransport.clients.get(loop)

    assert asyncio.run(drive_lifespan())


def test_start_in_thread_rejects_a_second_start(tmp):
    """A module-owned listener cannot be rebound to a second bridge."""
    del tmp
    _need_deps()
    mod = _load_mcp('http://127.0.0.1:1')
    mod._serve = lambda: None
    thread = mod.start_in_thread('http://127.0.0.1:1111')
    thread.join(timeout=5)
    assert mod.bridge.transport._base_url == 'http://127.0.0.1:1111'
    try:
        mod.start_in_thread('http://127.0.0.1:2222')
    except RuntimeError as exc:
        assert 'start_in_thread' in str(exc)
        assert 'more than once' in str(exc)
    else:
        raise AssertionError('a second start_in_thread call was accepted')


def _serve_crash_line(mod, failure):
    """Run _serve with its app factory raising `failure`, capturing the crash
    line through a strict-encoding stderr — the condition that used to turn
    the diagnostic itself into the traceback."""
    # The stub stands in for the real factory, which _serve calls with the
    # transport-security settings, so it has to accept what that call passes.
    def crash(**_settings):
        raise failure
    mod.mcp.streamable_http_app = crash
    buf = io.BytesIO()
    err = io.TextIOWrapper(buf, encoding='utf-8', errors='strict')
    with contextlib.redirect_stderr(err):
        mod._serve()
    err.flush()
    return buf.getvalue().decode('utf-8')


def test_serve_crash_line_survives_a_broken_str(tmp):
    """A caught exception whose __str__ fails must not suppress the diagnostic."""
    del tmp
    _need_deps()
    mod = _load_mcp('http://127.0.0.1:1')  # URL unused; the app factory is replaced

    class BrokenStr(Exception):
        def __str__(self):
            raise RuntimeError('broken __str__')

    line = _serve_crash_line(mod, BrokenStr('x'))
    assert '[MCP] serve crashed: <unprintable value>' in line, line
    # An ordinary exception message still arrives in full.
    line = _serve_crash_line(mod, Exception('bind failed'))
    assert '[MCP] serve crashed: bind failed' in line, line


def test_serve_crash_line_survives_a_surrogate_under_strict_stderr(tmp):
    """A surrogate in a caught exception must not kill the crash line either."""
    del tmp
    _need_deps()
    mod = _load_mcp('http://127.0.0.1:1')
    line = _serve_crash_line(mod, Exception('bind failed on \udcff'))
    assert '[MCP] serve crashed: bind failed on \\udcff' in line, line


def test_serve_crash_line_survives_a_hostile_decode_return(tmp):
    """A decode() returning a non-string must not reach the crash line's f-string.

    The exception's __str__ hands back a str subclass whose decode() returns
    an object with a raising __format__: pre-fix the helper returned it
    verbatim, the f-string raised RuntimeError, and the operator got zero
    stderr bytes — worse than the unguarded interpolation it replaced.
    """
    del tmp
    _need_deps()
    mod = _load_mcp('http://127.0.0.1:1')

    class BadFormat:
        def __format__(self, _spec):
            raise RuntimeError('evil format')

    class HostileChain(str):
        # The invalid shape is the point: str() hands back this subclass.
        def __str__(self):  # pylint: disable=invalid-str-returned
            return self

        def encode(self, *args, **kwargs):
            return self

        def decode(self, *args, **kwargs):
            return BadFormat()

    class ChainError(Exception):
        # __str__ hands back the hostile chain, so the crash line's helper
        # receives the decode() result, not an honest string.
        def __str__(self):  # pylint: disable=invalid-str-returned
            return HostileChain('x')

    line = _serve_crash_line(mod, ChainError('x'))
    assert '[MCP] serve crashed: <unprintable value>' in line, line


def test_a_nonpositive_mcp_timeout_admits_no_command(tmp):
    """The refusal has to land before the PUT, not after it.

    poll_result evaluates the deadline only after the command has been
    submitted, so a non-positive timeout polled zero times, raised a timeout
    for a command the browser had already been handed, and left the caller
    believing nothing ran.
    """
    _need_deps()
    with _util.bridge(tmp) as (base, docroot):
        mod = _load_mcp(base)
        mod._token.set(TOK)
        for timeout in (0, -1.0, float('nan'), float('inf')):
            try:
                asyncio.run(getattr(mod, 'exec')(
                    cmd_id='_timeout', code='1', timeout=timeout))
            except ValueError as error:
                assert 'finite positive' in str(error), (timeout, error)
            else:
                raise AssertionError(f'timeout {timeout!r} was accepted')
        for name in (f'{TOK}_extension', TOK):
            qdir = Path(docroot) / 'commands' / name
            queued = sorted(qdir.glob('*.json')) if qdir.is_dir() else []
            assert queued == [], (name, queued)


def test_mcp_numeric_settings_fail_cleanly_at_startup(tmp):
    """A bad MCP setting names itself instead of raising a bare ValueError.

    Both were parsed with bare int(), so a malformed value arrived as an
    import-time traceback and a negative body size was accepted — which made
    every non-negative Content-Length exceed the configured maximum and
    refused every request the front end received.
    """
    _need_deps()
    cases = (
        ('DAEDALUS_MCP_PORT', 'not-an-integer', 'integer from 0 to 65535'),
        ('DAEDALUS_MCP_PORT', '70000', 'integer from 0 to 65535'),
        ('DAEDALUS_MCP_MAX_BODY_SIZE', 'bad', 'non-negative integer'),
        ('DAEDALUS_MCP_MAX_BODY_SIZE', '-1', 'non-negative integer'),
    )
    failures = []
    for name, value, requirement in cases:
        env = dict(os.environ)
        env.update({
            'DAEDALUS_DIR': str(Path(tmp) / name.lower()),
            'DAEDALUS_PORT': '0',
            'PYTHONDONTWRITEBYTECODE': '1',
            name: value,
        })
        Path(env['DAEDALUS_DIR']).mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [sys.executable, '-c', 'import mcp_server'], cwd=_util.ROOT,
            env=env, capture_output=True, text=True, timeout=120)
        output = (proc.stdout + proc.stderr).strip()
        if (proc.returncode == 0 or 'Traceback' in output
                or name not in output or requirement not in output):
            failures.append(
                f'{name}={value!r}: exit={proc.returncode}, output={output!r}')
    assert not failures, '\n'.join(failures)


def test_mcp_and_bridge_config_use_one_env_parser(tmp):
    _need_deps()
    import env_config

    mod = _load_mcp('http://127.0.0.1:1')
    saved = {key: os.environ.get(key)
             for key in ('DAEDALUS_DIR', 'DAEDALUS_PORT')}
    os.environ['DAEDALUS_DIR'] = str(Path(tmp) / 'envcontract')
    os.environ['DAEDALUS_PORT'] = '0'
    try:
        bridge_config = _util.load(_util.ROOT / 'bridge_config.py')
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert mod.env_int is bridge_config.env_int is env_config.env_int
    cases = (
        ('DAEDALUS_CONTRACT_A', 5, 0, None),
        ('DAEDALUS_CONTRACT_B', 8086, 0, 65535),
    )
    for name, default, minimum, maximum in cases:
        for value in (None, '7', 'nonsense', '-1', '70000'):
            previous = os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value
            try:
                should_fail = (value == 'nonsense' or value == '-1'
                               or (maximum is not None and value == '70000'))
                try:
                    result = env_config.env_int(
                        name, default, minimum, maximum)
                except SystemExit as error:
                    assert should_fail, (name, value, error)
                else:
                    assert not should_fail, (name, value, result)
                    assert result == (default if value is None else int(value))
            finally:
                os.environ.pop(name, None)
                if previous is not None:
                    os.environ[name] = previous


def test_mcp_uses_the_shared_log_safe_function(tmp):
    """The MCP entry point must use the contract-tested shared renderer."""
    del tmp
    _need_deps()
    mod = _load_mcp('http://127.0.0.1:1')
    assert mod.log_safe is sys.modules['log_safe'].log_safe


def test_the_shared_contract_catches_a_divergent_copy(tmp):
    """Proof the anti-drift control has teeth: a divergent helper must fail it.

    The generator must keep a standalone implementation because its script
    import path excludes the repository root, so it is the surviving copy
    that can drift independently. The divergent helper below keeps the
    contract's exact shape and swaps one decode for ascii/replace, diverging
    only on ordinary non-ASCII.
    """
    del tmp
    generator = _util.load(
        _util.ROOT / 'scripts' / 'gen_gitignore.py',
        'divergent_gen_gitignore_log_safe')

    def divergent(value):
        try:
            rendered = str(value).encode(
                'utf-8', 'backslashreplace').decode('ascii', 'replace')
        except Exception:
            return '<unprintable value>'
        # Exact type, not isinstance: mirrors the contract's guard.
        if type(rendered) is not str:  # pylint: disable=unidiomatic-typecheck
            return '<unprintable value>'
        return rendered

    generator._log_safe = divergent
    try:
        for value, expected in _util.log_safe_cases():
            assert generator._log_safe(value) == expected
        assert generator._log_safe('héllo — 世界') == 'héllo — 世界'
    except AssertionError:
        return
    raise AssertionError('a divergent _log_safe passed the shared contract')


def test_list_tabs_tool_against_real_bridge(tmp):
    _need_deps()
    with _util.bridge(tmp, env={'DAEDALUS_MCP_PORT': '0'}) as (base, _docroot):
        _util.post_json(base + '/sync-tabs', {'token': TOK, 'tabs': [
            {'tabId': '7', 'url': 'https://example.com/mcp', 'title': 'M'}]})
        mod = _load_mcp(base)
        mod._token.set(TOK)  # what mcp_auth.BearerAuth does per request
        tabs = asyncio.run(mod.list_tabs())
        assert isinstance(tabs, list) and len(tabs) == 1, tabs
        assert tabs[0]['tabId'] == '7'
        assert tabs[0]['url'] == 'https://example.com/mcp'
        assert tabs[0]['title'] == 'M'


def _relative_or_synthetic(target):
    """A traversal-shaped path to `target`, or a synthetic one if impossible.

    Windows puts the temporary directory and the checkout on different
    drives, and os.path.relpath raises rather than answering across a mount
    boundary. What the caller needs is a path-shaped argument the MCP tools
    must refuse; whether it resolves to the sentinel is beside the point,
    because the refusal happens at the schema before any filesystem access.
    """
    try:
        return os.path.relpath(target, _util.ROOT)
    except ValueError:
        return os.path.join(*(['..'] * 6), *target.parts[-2:])


def test_live_mcp_has_no_server_path_authority(tmp):
    """A bearer can submit inline source but cannot make MCP read host paths.

    The schema check covers every tool that formerly accepted a path. The
    store/list round trip then exercises the original exfiltration chain with
    an absolute path, traversal, and a symlink: every call must be rejected,
    and none of the sentinel source may come back through list_hotfixes.
    """
    _need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')

    root = Path(tmp)
    absolute_secret = root / 'absolute-secret.js'
    traversal_secret = root / 'traversal-secret.js'
    symlink_secret = root / 'symlink-secret.js'
    sentinels = ('ABSOLUTE_MCP_SENTINEL', 'TRAVERSAL_MCP_SENTINEL',
                 'SYMLINK_MCP_SENTINEL')
    for path, sentinel in zip(
            (absolute_secret, traversal_secret, symlink_secret), sentinels):
        path.write_text(sentinel, encoding='utf-8')
    link = root / 'source-link.js'
    try:
        link.symlink_to(symlink_secret)
    except OSError as exc:
        _util.skip(f'symlink creation unavailable: {type(exc).__name__}')

    traversal = _relative_or_synthetic(traversal_secret)
    symlink_escape = _relative_or_synthetic(link)
    attempted_paths = (str(absolute_secret), traversal, symlink_escape)
    with _util.bridge(tmp, env={'DAEDALUS_MCP_PORT': '0'}) as (base, docroot):
        _, port = _start_mcp_in_process(base)
        session_id = _open_mcp_session(port)
        qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
        handled = set()
        stored = []
        simulator_errors = []
        stop = threading.Event()

        def extension_simulator():
            deadline = time.time() + 20
            try:
                while not stop.is_set() and time.time() < deadline:
                    for command_path in sorted(qdir.glob('*.json')):
                        if command_path.name in handled:
                            continue
                        handled.add(command_path.name)
                        command = json.loads(command_path.read_text(encoding='utf-8'))
                        if command.get('type') == 'store-hotfix':
                            stored.append({
                                'id': command['fixId'],
                                'ts': 1,
                                'code': command['code'],
                            })
                            result = {
                                'stored': command['fixId'],
                                'total': len(stored),
                                'permanent': False,
                            }
                        elif command.get('type') == 'list-hotfixes':
                            result = {'version': '0.18.0', 'fixes': stored}
                        else:
                            continue
                        status, body = _util.post_json(base + '/result', {
                            'token': TOK,
                            'tabId': 'extension',
                            'id': command['id'],
                            'result': result,
                            'error': None,
                            'ts': 1,
                            '_did': command['_did'],
                        })
                        if status != 200:
                            simulator_errors.append((status, body))
                    time.sleep(0.02)
            except Exception as exc:  # test-thread diagnosis, surfaced below
                simulator_errors.append(type(exc).__name__)

        simulator = threading.Thread(target=extension_simulator)
        simulator.start()
        try:
            replies = [
                _call_mcp_tool(
                    port, session_id, f'path-{index}', 'store_hotfix',
                    {'fix_id': f'path-{index}', 'path': path})
                for index, path in enumerate(attempted_paths)
            ]
            listed = _call_mcp_tool(
                port, session_id, 'list-after-paths', 'list_hotfixes')
            status, _unused, raw = _mcp_request(
                port,
                {'jsonrpc': '2.0', 'id': 'schemas', 'method': 'tools/list',
                 'params': {}},
                session_ids=(session_id,))
            assert status == 200, (status, raw)
            schemas = _mcp_payload(raw)['result']['tools']
        finally:
            stop.set()
            simulator.join(timeout=5)

        path_tools = {'put': 'code', 'inject_css': 'css',
                      'remove_css': 'css', 'store_hotfix': 'code'}
        by_name = {tool['name']: tool for tool in schemas}
        schema_failures = []
        for name, inline_field in path_tools.items():
            schema = by_name[name]['inputSchema']
            properties = schema.get('properties', {})
            if 'path' in properties or inline_field not in properties:
                schema_failures.append(name)
        returned = json.dumps(listed, ensure_ascii=False)
        rejected = [reply.get('result', {}).get('isError') is True
                    for reply in replies]
        leaked = [sentinel for sentinel in sentinels if sentinel in returned]
        assert simulator_errors == [], simulator_errors
        assert all(rejected) and not schema_failures and not leaked, {
            'path_calls_rejected': rejected,
            'schemas_with_path_authority': schema_failures,
            'recovered_sentinels': leaked,
        }


def test_port_zero_bridge_mcp_list_tabs_round_trip(tmp):
    """The child binds the bridge first, so MCP reaches its actual port at 0."""
    _need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    env = {'DAEDALUS_TOKEN': TOK, 'TOKEN': ''}
    with _bridge_with_live_mcp(tmp, env) as (base, port):
        status, body = _util.post_json(base + '/sync-tabs', {
            'token': TOK,
            'tabs': [{'tabId': 'ephemeral-tab',
                      'url': 'https://example.com/ephemeral',
                      'title': 'Ephemeral'}],
        })
        assert status == 200, (status, body)
        session_id = _open_mcp_session(port)
        reply = _call_mcp_tool(
            port, session_id, 'port-zero-tabs', 'list_tabs')
        text = _mcp_tool_text(reply)
        assert reply.get('result', {}).get('isError') is not True, reply
        assert 'ephemeral-tab' in text and 'example.com/ephemeral' in text, text


def test_ping_tool_round_trip(tmp):
    """ping() PUTs a command and correlates the extension's result delivery."""
    _need_deps()
    with _util.bridge(tmp, env={'DAEDALUS_MCP_PORT': '0'}) as (base, docroot):
        mod = _load_mcp(base)
        mod._token.set(TOK)

        qdir = Path(docroot) / 'commands' / TOK
        answered = set()

        def extension(world):
            # Answer only after the command has actually been enqueued, the
            # way the real extension would.
            deadline = time.time() + 15
            while time.time() < deadline:
                if qdir.is_dir() and set(qdir.glob('*.json')) - answered:
                    break
                time.sleep(0.05)
            queued = sorted(set(qdir.glob('*.json')) - answered)
            assert len(queued) == 1, queued
            answered.add(queued[0])
            command = json.loads(queued[0].read_text(encoding='utf-8'))
            status, _ = _util.post_json(base + '/result', {
                'token': TOK, 'id': command['id'], 'result': 'MCP Title',
                'error': None, 'ts': 1, 'world': world,
                '_did': command['_did']})
            assert status == 200, status

        # Distinct execution channels round-trip verbatim; neither marker says
        # whether the JavaScript value should be trusted.
        for world in ('cdp', 'page:scripting'):
            t = threading.Thread(target=extension, args=(world,))
            t.start()
            try:
                res = asyncio.run(mod.ping())
            finally:
                t.join(timeout=20)
            assert res['title'] == 'MCP Title', res
            assert res['world'] == world, res
            assert isinstance(res['ms'], int) and res['ms'] >= 0, res
        # The command really went through the bridge's queue.
        assert list(qdir.glob('*.json'))


def test_two_concurrent_mcp_callers_receive_only_their_own_results(tmp):
    """MCP waiters stay correlated when both results land before either consumes."""
    _need_deps()
    owners = ('owner-a', 'owner-b')
    with _util.bridge(tmp, env={'DAEDALUS_MCP_PORT': '0'}) as (base, docroot):
        mod = _load_mcp(base)
        qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
        release_waiters = threading.Event()
        original_poll = mod.bridge.poll_result
        held_ids, box = set(), {}

        async def gated_poll(*args, **kwargs):
            held_ids.add(kwargs['expect_delivery'])
            while not release_waiters.is_set():
                await asyncio.sleep(0.01)
            return await original_poll(*args, **kwargs)

        mod.bridge.poll_result = gated_poll

        def run_callers():
            mod._token.set(TOK)

            async def callers():
                return await asyncio.gather(*(
                    mod.bridge.ext_cmd('_cookies', 'cookies', timeout=30,
                                       domain=owner)
                    for owner in owners))

            try:
                box['values'] = asyncio.run(callers())
            except Exception as exc:  # pylint: disable=broad-except
                box['error'] = exc

        worker = threading.Thread(target=run_callers)
        try:
            worker.start()
            deadline = time.time() + 20
            while time.time() < deadline:
                files = sorted(qdir.glob('*.json')) if qdir.is_dir() else []
                if len(files) == len(held_ids) == len(owners):
                    break
                time.sleep(0.05)
            files = sorted(qdir.glob('*.json')) if qdir.is_dir() else []
            assert len(files) == len(held_ids) == len(owners), (files, box)
            commands = [json.loads(path.read_text(encoding='utf-8'))
                        for path in files]
            by_owner = {command['domain']: command for command in commands}
            for owner in owners:
                command = by_owner[owner]
                status, body = _util.post_json(base + '/result', {
                    'token': TOK, 'tabId': 'extension', 'id': command['id'],
                    'result': [{'domain': owner}], 'error': None, 'ts': 1,
                    '_did': command['_did']})
                assert status == 200 and body == {'ok': True}, (status, body)
            delivery_dir = Path(docroot) / 'results' / 'deliveries'
            files = list((delivery_dir / f'{TOK}_extension').glob('*.json'))
            assert len(files) == len(owners), files
        finally:
            release_waiters.set()
            worker.join(timeout=60)
            mod.bridge.poll_result = original_poll
        assert not worker.is_alive(), box
        if 'error' in box:
            raise box['error']
        assert box['values'] == [[{'domain': owner}] for owner in owners], box


def test_segment_status_tool_fetches_sig_and_reports_foreign_jobs(tmp):
    """segment_status obtains the job capability itself; a job owned by another
    token used to surface as a bare httpx 409 through raise_for_status."""
    _need_deps()
    with _util.bridge(tmp, env={'DAEDALUS_MCP_PORT': '0'}) as (base, _docroot):
        mod = _load_mcp(base)
        mod._token.set(TOK)
        # Own job: the tool re-fetches the minted sig and reads status back.
        status, _ = _util.post_json(base + '/segment-job',
                                    {'token': TOK, 'job': 'mcpjob'})
        assert status == 200, status
        res = asyncio.run(mod.segment_status('mcpjob'))
        assert res == {'done': [], 'count': 0, 'gaps': []}, res
        # A record left by an earlier configured token is a foreign job after
        # token rotation. Plant that persisted state directly; the live bridge
        # no longer lets an unauthorized request mint it.
        segment_root = Path(_docroot) / 'segments'
        (segment_root / 'mcpjob2').mkdir()
        (segment_root / 'mcpjob2.json').write_text(json.dumps({
            'token': 'earlierconfigured',
            'sig': 'persisted-capability',
            'max_segment_index': 10,
            'max_segment_count': 10,
            'max_bytes': 100,
        }))
        try:
            asyncio.run(mod.segment_status('mcpjob2'))
        except RuntimeError as e:
            assert 'owned by a different token' in str(e), e
        else:
            raise AssertionError('segment_status on a foreign job did not raise')


def test_screenshot_returns_the_bytes_its_own_result_named(tmp):
    """include_image must inline this capture's file, not the newest one.

    `_ss` is the default screenshot id, so two captures share an upload
    directory; the tool correlated its own result and then fetched by id,
    which answers with whichever file landed last.
    """
    _need_deps()
    with _util.bridge(tmp, env={'DAEDALUS_MCP_PORT': '0'}) as (base, docroot):
        mod = _load_mcp(base)
        mod._token.set(TOK)
        qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
        failure = []

        def extension():
            """Stand in for the extension: store this capture, let a later one
            land on top of it, then answer the command."""
            try:
                deadline = time.time() + 20
                while not (qdir.is_dir() and any(qdir.glob('*.json'))):
                    assert time.time() < deadline, 'no screenshot command'
                    time.sleep(0.05)
                command = json.loads(
                    sorted(qdir.glob('*.json'))[0].read_text(encoding='utf-8'))
                for name, payload in (('mine.png', b'this-invocation'),
                                      ('later.png', b'the-next-invocation')):
                    status, body = _util.post_json(base + '/upload', {
                        'token': TOK, 'id': '_ss', 'filename': name,
                        'data': base64.b64encode(payload).decode()})
                    assert status == 200, (status, body)
                # Stamped rather than assumed: two writes can share a
                # timestamp, and which file is newest is the whole point.
                shot_dir = Path(docroot) / 'uploads' / TOK / '_ss'
                os.utime(shot_dir / 'mine.png', (1_700_000_000, 1_700_000_000))
                os.utime(shot_dir / 'later.png', (1_700_000_100, 1_700_000_100))
                status, body = _util.post_json(base + '/result', {
                    'token': TOK, 'tabId': 'extension', 'id': '_ss',
                    'result': {'path': f'{TOK}/_ss/mine.png',
                               'size': len(b'this-invocation')},
                    'error': None, 'ts': 1, '_did': command['_did'],
                })
                assert status == 200, (status, body)
            except AssertionError as exc:
                failure.append(exc)

        responder = threading.Thread(target=extension, daemon=True)
        responder.start()
        try:
            answer = asyncio.run(mod.screenshot(include_image=True, timeout=25))
        finally:
            responder.join(timeout=30)
        assert not failure, failure
        meta, image = answer
        assert meta == {'path': f'{TOK}/_ss/mine.png',
                        'size': len(b'this-invocation')}, meta
        assert image.data == b'this-invocation', image.data


def test_an_unauthenticated_body_is_refused_before_it_is_read(tmp):
    """Credentials are decided before the body is parsed, and it is capped.

    The middleware read and JSON-parsed the whole POST body looking for
    repeated tool arguments, and only then looked at the Authorization header
    — so an unauthenticated caller could make the process materialize an
    arbitrarily large request before being told 401, and got body-level
    diagnostics it had no business seeing.

    The order is observable through that diagnostic: a body carrying a
    duplicate `job` argument answered 400 without any credentials, and now
    answers 401. Size is pinned separately, since an authenticated caller is
    the only one that ever reaches the cap.
    """
    _need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    env = {'DAEDALUS_TOKEN': TOK, 'TOKEN': '',
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

        # The same body WITH credentials still gets the duplicate refusal.
        status, body = _util.request(
            url, 'POST', body=duplicate_carrier,
            headers={'Content-Type': 'application/json',
                     'Authorization': f'Bearer {TOK}',
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
                     'Authorization': f'Bearer {TOK}',
                     'Accept': 'application/json, text/event-stream'})
        assert status == 413, (status, body)
        assert b'too large' in body, body


def test_bearer_middleware_requires_configured_token_on_live_mcp_port(tmp):
    """Only the configured bridge token may pass the live MCP middleware."""
    _need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    env = {
        'DAEDALUS_TOKEN': TOK,
        'TOKEN': '',
    }
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
            headers={'Authorization': f'Bearer {TOK}',
                     'Accept': 'application/json, text/event-stream'})
        assert status != 401, status


def test_bearer_middleware_rejects_duplicate_authorization_headers(tmp):
    """MCP authentication never selects one of two bearer credentials."""
    _need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    env = {
        'DAEDALUS_TOKEN': TOK,
        'TOKEN': '',
    }
    with _bridge_with_live_mcp(tmp, env) as (base, port):
        rpc = json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}
        }).encode()
        orders = ((f'Bearer {TOK}', 'Bearer othermcptok'),
                  ('Bearer othermcptok', f'Bearer {TOK}'))
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
            headers={'Authorization': f'Bearer {TOK}',
                     'Accept': 'application/json, text/event-stream'})
        assert status not in (400, 401), status


def test_mcp_authority_carriers_reject_blank_equal_and_scoped_duplicates(tmp):
    """MCP never selects a bearer, session, or job from repeated carriers."""
    _need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    with _util.bridge(
            tmp, env={'DAEDALUS_MCP_PORT': '0'}) as (base, docroot):
        _mod, port = _start_mcp_in_process(base)

        initialize = {
            'jsonrpc': '2.0',
            'id': 'initialize',
            'method': 'initialize',
            'params': {
                'protocolVersion': '2024-11-05',
                'capabilities': {},
                'clientInfo': {'name': 'duplicate-proof', 'version': '0'},
            },
        }
        authorization_duplicates = (
            (f'Bearer {TOK}', f'Bearer {TOK}'),
            ('', f'Bearer {TOK}'),
            (f'Bearer {TOK}', ''),
        )
        authorization_replies = []
        for values in authorization_duplicates:
            status, _session, raw = _mcp_request(
                port, initialize, authorizations=values)
            try:
                error = json.loads(raw).get('error')
            except json.JSONDecodeError:
                error = None
            authorization_replies.append((values, status, error))
        assert all(status == 400
                   and error == 'duplicate Authorization header'
                   for _values, status, error in authorization_replies), (
                       authorization_replies)

        status, session_id, raw = _mcp_request(port, initialize)
        assert status == 200 and session_id, (status, session_id, raw)
        status, _session, raw = _mcp_request(
            port, {'jsonrpc': '2.0',
                   'method': 'notifications/initialized'},
            session_ids=(session_id,))
        assert status == 202, (status, raw)

        list_tools = {
            'jsonrpc': '2.0',
            'id': 'list',
            'method': 'tools/list',
            'params': {},
        }
        wrong_session = 'f' * 32
        session_duplicates = (
            (session_id, wrong_session),
            (wrong_session, session_id),
            (session_id, session_id),
            ('', session_id),
            (session_id, ''),
        )
        session_replies = []
        for values in session_duplicates:
            status, _session, raw = _mcp_request(
                port, list_tools, session_ids=values)
            try:
                error = json.loads(raw).get('error')
            except json.JSONDecodeError:
                error = None
            session_replies.append((values, status, error))
        assert all(status == 400
                   and error == 'duplicate Mcp-Session-Id header'
                   for _values, status, error in session_replies), (
                       session_replies)

        jobs = (
            ('segment_job', 'mcp-job-a', 'mcp-job-b'),
            ('segment_job', 'mcp-job-c', 'mcp-job-c'),
            ('segment_job', '', 'mcp-job-d'),
            ('segment_job', 'mcp-job-e', ''),
            ('segment_status', 'mcp-status-a', 'mcp-status-b'),
        )
        job_replies = []
        for index, (tool, first, second) in enumerate(jobs):
            raw_call = (
                b'{"jsonrpc":"2.0","id":' + json.dumps(index).encode()
                + b',"method":"tools/call","params":{"name":'
                + json.dumps(tool).encode() + b','
                + b'"arguments":{"job":' + json.dumps(first).encode()
                + b',"job":' + json.dumps(second).encode() + b'}}}')
            status, _session, raw = _mcp_request(
                port, raw_call, session_ids=(session_id,))
            try:
                error = json.loads(raw).get('error')
            except json.JSONDecodeError:
                error = None
            job_replies.append((tool, first, second, status, error))
        assert all(status == 400 and error == 'duplicate job'
                   for _tool, _first, _second, status, error in job_replies), (
                       job_replies)

        wrapped_calls = (
            (b'{"jsonrpc":"2.0","id":"arguments","method":"tools/call",'
             b'"params":{"name":"segment_job",'
             b'"arguments":{"job":"mcp-wrapper-a"},'
             b'"arguments":{"job":"mcp-wrapper-b"}}}'),
            (b'{"jsonrpc":"2.0","id":"params","method":"tools/call",'
             b'"params":{"name":"segment_job",'
             b'"arguments":{"job":"mcp-wrapper-c"}},'
             b'"params":{"name":"segment_job",'
             b'"arguments":{"job":"mcp-wrapper-d"}}}'),
            (b'{"jsonrpc":"2.0","id":"status-arguments",'
             b'"method":"tools/call","params":{"name":"segment_status",'
             b'"arguments":{"job":"mcp-wrapper-e"},'
             b'"arguments":{"job":"mcp-wrapper-f"}}}'),
            (b'{"jsonrpc":"2.0","id":"status-params",'
             b'"method":"tools/call",'
             b'"params":{"name":"segment_status",'
             b'"arguments":{"job":"mcp-wrapper-g"}},'
             b'"params":{"name":"segment_status",'
             b'"arguments":{"job":"mcp-wrapper-h"}}}'),
        )
        wrapper_replies = []
        for raw_call in wrapped_calls:
            status, _session, raw = _mcp_request(
                port, raw_call, session_ids=(session_id,))
            try:
                error = json.loads(raw).get('error')
            except json.JSONDecodeError:
                error = None
            wrapper_replies.append((status, error))
        assert wrapper_replies == [
            (400, 'duplicate job'), (400, 'duplicate job'),
            (400, 'duplicate job'), (400, 'duplicate job')], wrapper_replies
        assert list((Path(docroot) / 'segments').iterdir()) == []

        status, _session, raw = _mcp_request(
            port, list_tools, session_ids=(session_id,))
        assert status == 200, (status, raw)


def test_mcp_host_and_origin_repeated_headers_are_rejected(tmp):
    """MCP rejects every repeated transport-security header presentation."""
    _need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    with _util.bridge(tmp, env={'DAEDALUS_MCP_PORT': '0'}) as (base, _docroot):
        _mod, port = _start_mcp_in_process(base)
        initialize = {
            'jsonrpc': '2.0',
            'id': 'transport-security-headers',
            'method': 'initialize',
            'params': {
                'protocolVersion': '2024-11-05',
                'capabilities': {},
                'clientInfo': {'name': 'header-proof', 'version': '0'},
            },
        }
        allowed_host = f'127.0.0.1:{port}'
        host_cases = (
            (allowed_host, 'example.com'),
            ('example.com', allowed_host),
            (allowed_host, allowed_host),
            (allowed_host, ''),
            ('', allowed_host),
        )
        host_replies = []
        for values in host_cases:
            status, _session, raw = _mcp_request(
                port, initialize, hosts=values)
            try:
                error = json.loads(raw).get('error')
            except json.JSONDecodeError:
                error = None
            host_replies.append((status, error))
        origin_cases = (
            ('', 'https://example.com'),
            ('https://example.com', ''),
            ('', ''),
        )
        origin_replies = []
        for values in origin_cases:
            status, _session, raw = _mcp_request(
                port, initialize, origins=values)
            try:
                error = json.loads(raw).get('error')
            except json.JSONDecodeError:
                error = None
            origin_replies.append((status, error))
        expected_hosts = [(400, 'duplicate Host header')] * len(host_cases)
        expected_origins = [
            (400, 'duplicate Origin header')] * len(origin_cases)
        assert (host_replies == expected_hosts
                and origin_replies == expected_origins), (
                    host_replies, origin_replies)


def test_mcp_initialize_accepts_nested_application_token_members(tmp):
    """Nested application members named token are not MCP credentials."""
    _need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    with _util.bridge(tmp, env={'DAEDALUS_MCP_PORT': '0'}) as (base, _docroot):
        _mod, port = _start_mcp_in_process(base)
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
        status, session_id, raw = _mcp_request(port, initialize)
        assert status == 200 and session_id, (status, session_id, raw)


def test_bearer_middleware_fails_closed_without_configured_token(tmp):
    """A missing bridge-token configuration leaves no bearer authorized."""
    _need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    env = {
        'DAEDALUS_TOKEN': '',
        'TOKEN': '',
    }
    with _bridge_with_live_mcp(tmp, env) as (_base, port):
        url = f'http://127.0.0.1:{port}/mcp'
        rpc = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}
        status, body = _util.request(
            url, 'POST', body=rpc,
            headers={'Authorization': f'Bearer {TOK}',
                     'Accept': 'application/json, text/event-stream'})
        assert status == 401, (status, body)
        assert json.loads(body)['error'] == 'unauthorized', body


def test_mcp_port_zero_announces_the_actual_bound_port(tmp):
    """DAEDALUS_MCP_PORT=0 must print the bound port, not the configured one.

    The startup line used to interpolate the configured value and print
    '127.0.0.1:0', so an operator choosing port 0 could not discover the
    listener. The line now follows the bind and names the bound socket.
    """
    del tmp
    _need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    mod = _load_mcp_at_port('http://127.0.0.1:1', 0)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        mod.start_in_thread()
        assert mod._bound.wait(timeout=10), mod.startup_error or 'never bound'
    line = out.getvalue().strip()
    assert f'127.0.0.1:{mod.bound_port}' in line, line
    assert mod.bound_port > 0, line
    _wait_for_mcp(mod.bound_port)


def test_the_mcp_fixture_ignores_a_squatted_draw(tmp):
    """A squatted drawn port cannot reach the MCP listener: it does not draw.

    Same regression shape as the bridge fixture's lost-draw test: rig the
    draw to a taken port, then start through the fixture. The listener binds
    port 0, so the taken number is never involved.
    """
    del tmp
    _need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    squatter = socket.socket()
    squatter.bind(('127.0.0.1', 0))
    squatter.listen(1)
    taken = squatter.getsockname()[1]
    real_free_port = _util.free_port
    _util.free_port = lambda: taken
    try:
        mod, port = _start_mcp_in_process('http://127.0.0.1:1')
    finally:
        _util.free_port = real_free_port
        squatter.close()
    assert port != taken, 'the listener bound the squatted port'
    _wait_for_mcp(port)
    assert mod is not None


def test_a_persistent_mcp_collision_surfaces_the_verbatim_bind_error(tmp):
    """An explicit squatted MCP port surfaces the original bind error, promptly.

    Pre-fix the child-env helper retried five times and then raised a generic
    'lost the port race' assertion tens of seconds later; the actual error —
    EADDRINUSE — never reached the operator. The crash now arrives as its own
    line on the child's drained stdout, carrying the original text.
    """
    _need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    squatter = socket.socket()
    squatter.bind(('127.0.0.1', 0))
    squatter.listen(1)
    taken = squatter.getsockname()[1]
    output = []
    started = time.time()
    try:
        with _util.bridge(
                tmp,
                env={'DAEDALUS_MCP_PORT': str(taken),
                     'DAEDALUS_TOKEN': TOK, 'TOKEN': ''},
                output=output) as (_base, _docroot):
            crash_line = None
            deadline = time.time() + 10
            while time.time() < deadline and crash_line is None:
                crash_line = next(
                    (line for line in output if 'serve crashed' in line), None)
                if crash_line is None:
                    time.sleep(0.05)
            elapsed = time.time() - started
            assert crash_line is not None, (
                f'no crash line in the child output: {output!r}')
            assert _util.is_bind_error(crash_line), crash_line
            assert elapsed < 5, f'the crash surfaced after {elapsed:.1f}s'
    finally:
        squatter.close()


def test_an_unrelated_crash_naming_the_bind_text_is_not_retried(tmp):
    """A startup crash whose text matches the bind signature surfaces once.

    The deleted retry read 'address already in use' ANYWHERE as a lost draw
    and retried it five times into a generic assertion. There is no retry
    left to fool: the crash arrives verbatim, immediately.
    """
    del tmp
    _need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    real_loader = _load_mcp_at_port

    def crashing_loader(base, port):
        mod = real_loader(base, port)

        def crash(**_settings):
            raise RuntimeError('address already in use')
        mod.mcp.streamable_http_app = crash
        return mod

    globals()['_load_mcp_at_port'] = crashing_loader
    started = time.time()
    try:
        try:
            _start_mcp_in_process('http://127.0.0.1:1')
        except AssertionError as failure:
            elapsed = time.time() - started
            assert 'serve crashed' in str(failure), failure
            assert 'address already in use' in str(failure), failure
            assert elapsed < 5, f'surfaced after {elapsed:.1f}s — a retry happened'
        else:
            raise AssertionError('a crashed MCP listener started')
    finally:
        globals()['_load_mcp_at_port'] = real_loader


def _answer_mcp_command(base, docroot, mod, call, result, tab='extension'):
    """Run one MCP tool that sends a command, and answer what it sends.

    The tool awaits a result that only an extension would post, and there is
    none here, so the answer comes from this thread once the command lands in
    the queue. Returns (what the tool returned, the payload the bridge got).
    """
    qdir = Path(docroot) / 'commands' / f'{TOK}_{tab}'
    ignored_names = clear_command_queue(qdir)
    box = {}

    def run():
        # The token is a ContextVar, and a thread starts with a fresh context:
        # setting it on the caller's thread leaves the tool answering "no token
        # in context". BearerAuth sets it per request for the same reason.
        mod._token.set(TOK)
        try:
            box['value'] = asyncio.run(call())
        except Exception as exc:  # pylint: disable=broad-except
            box['error'] = exc

    worker = threading.Thread(target=run)
    worker.start()
    try:
        queued = wait_for_command(qdir, 20, producer_alive=worker.is_alive,
                                  ignored_names=ignored_names)
        if queued is None:
            worker.join(timeout=5)
            if 'error' in box:
                raise box['error']
            raise AssertionError('the tool enqueued no command')
        status, _ = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': tab, 'id': queued['id'], 'result': result,
            'error': None, 'ts': 1, '_did': queued['_did']})
        assert status == 200, status
    finally:
        worker.join(timeout=60)
    if 'error' in box:
        raise box['error']
    return box.get('value'), queued


def test_every_mcp_command_tool_sends_its_documented_command(tmp):
    """Each MCP tool reaches the extension as the command it claims.

    The MCP surface is a second sender of the same wire protocol the CLI
    speaks, written separately, so the two can disagree about a `type` or a
    field name without anything noticing. This pins what the tools put on the
    wire, read back out of the queue the bridge routed it into.
    """
    _need_deps()
    with _util.bridge(tmp, env={'DAEDALUS_MCP_PORT': '0'}) as (base, docroot):
        mod = _load_mcp(base)
        mod._token.set(TOK)  # what mcp_auth.BearerAuth does per request
        cases = (
            (lambda: mod.open_tab('https://example.com'), 'open-tab',
             {'url': 'https://example.com'}),
            (lambda: mod.open_tabs(['https://example.com/a']), 'open-tabs',
             {'urls': ['https://example.com/a']}),
            (lambda: mod.focus_tab(7), 'focus-tab', {'tabId': 7}),
            (lambda: mod.close_tab([5]), 'close-tab', {'tabId': 5}),
            (lambda: mod.ext_navigate('https://example.com'), 'navigate',
             {'url': 'https://example.com'}),
            (mod.ext_reload, 'reload', {}),
            (lambda: mod.get_cookies(domain='example.com'), 'cookies',
             {'domain': 'example.com'}),
            (lambda: mod.set_cookie('https://example.com', 'sid', 'abc'),
             'set-cookie',
             {'url': 'https://example.com', 'name': 'sid', 'value': 'abc'}),
            (lambda: mod.remove_cookie('https://example.com', 'sid'),
             'remove-cookie', {'url': 'https://example.com', 'name': 'sid'}),
            (lambda: mod.clear_cookies(domain='example.com'), 'clear-cookies',
             {'domain': 'example.com'}),
            (lambda: mod.inject_css('a{color:red}'), 'inject-css',
             {'css': 'a{color:red}'}),
            (lambda: mod.remove_css('a{color:red}'), 'remove-css',
             {'css': 'a{color:red}'}),
            (lambda: mod.block_requests('*.example/*'), 'block-requests',
             {'pattern': '*.example/*'}),
            (mod.unblock_requests, 'unblock-requests', {}),
            (mod.list_block_rules, 'list-block-rules', {}),
            (lambda: mod.store_hotfix('fix1', 'console.log(1)'),
             'store-hotfix', {'fixId': 'fix1', 'code': 'console.log(1)'}),
            (lambda: mod.clear_hotfix('fix1'), 'clear-hotfix',
             {'fixId': 'fix1'}),
            (mod.clear_hotfixes, 'clear-all-hotfixes', {}),
            (mod.list_hotfixes, 'list-hotfixes', {}),
            (lambda: mod.set_permanent('fix1', True), 'set-permanent',
             {'fixId': 'fix1', 'permanent': True}),
            (mod.net_capture, 'net-capture', {}),
            (mod.net_capture_stop, 'net-capture-stop', {}),
            (mod.net_capture_get, 'net-capture-get', {}),
            (lambda: mod.cdp('Page.enable'), 'cdp',
             {'method': 'Page.enable', 'params': {}}),
            (mod.fetch_timings, 'fetch-timings', {}),
            (mod.ext_self_reload, 'ext-reload', {}),
        )
        for call, cmd_type, fields in cases:
            _value, queued = _answer_mcp_command(base, docroot, mod, call, {})
            assert queued.get('type') == cmd_type, (cmd_type, queued)
            # Routing is consumed by the bridge when it enqueues, so what
            # proves the command addressed the extension worker is the queue
            # it was read from.
            assert 'tab' not in queued, (cmd_type, queued)
            for key, value in fields.items():
                assert queued.get(key) == value, (cmd_type, key, queued)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
