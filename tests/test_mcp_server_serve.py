#!/usr/bin/env python3
"""Starting and serving the MCP front end's in-process
listeners.

Split from tests/test_mcp_server.py.
"""
import asyncio
import contextlib
import http.client
import io
import json
import os
import importlib.util
import re
import socket
import sys
import types
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


def test_a_poisoned_shell_cannot_redirect_start_in_thread(tmp):
    del tmp
    _mcp_load._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    poison = {'DAEDALUS_LOCAL_URL': 'http://127.0.0.1:9',
              'DAEDALUS_PORT': '9'}
    saved = {key: os.environ.get(key) for key in poison}
    os.environ.update(poison)
    snapshot = dict(os.environ)
    try:
        mod = _mcp_load._load_mcp('http://127.0.0.1:1')
        setattr(mod, '_serve', lambda: None)
        thread = _mcp_load._start_in_thread(mod, 'http://127.0.0.1:1111')
        thread.join(timeout=5)
        assert mod.bridge.transport._base_url == (
            'http://127.0.0.1:1111'), mod.bridge.transport._base_url

        mod = _mcp_load._load_mcp('http://127.0.0.1:1')
        setattr(mod, '_serve', lambda: None)
        thread = _mcp_load._start_in_thread(mod)
        thread.join(timeout=5)
        assert mod.bridge.transport._base_url == (
            'http://127.0.0.1:1'), mod.bridge.transport._base_url

        mod, _port = _mcp_load._start_mcp_in_process('http://127.0.0.1:1')
        assert mod.bridge.transport._base_url == (
            'http://127.0.0.1:1'), mod.bridge.transport._base_url
        assert dict(os.environ) == snapshot
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_start_in_thread_rejects_a_second_start(tmp):
    """A module-owned listener cannot be rebound to a second bridge."""
    del tmp
    _mcp_load._need_deps()
    mod = _mcp_load._load_mcp('http://127.0.0.1:1')
    setattr(mod, '_serve', lambda: None)
    thread = _mcp_load._start_in_thread(mod, 'http://127.0.0.1:1111')
    thread.join(timeout=5)
    assert mod.bridge.transport._base_url == 'http://127.0.0.1:1111'
    try:
        _mcp_load._start_in_thread(mod, 'http://127.0.0.1:2222')
    except RuntimeError as exc:
        assert 'start_in_thread' in str(exc)
        assert 'more than once' in str(exc)
    else:
        raise AssertionError('a second start_in_thread call was accepted')


def _serve_crash_line(mod, failure):
    """Run _serve with its app factory raising `failure`, capturing the crash
    line through a strict-encoding stderr."""
    # The stub takes what _serve passes the real factory.
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
    """A caught exception whose __str__ fails must not
    suppress the diagnostic."""
    del tmp
    _mcp_load._need_deps()
    mod = _mcp_load._load_mcp(
        'http://127.0.0.1:1')  # URL unused; the app factory is replaced

    class BrokenStr(Exception):
        def __str__(self):
            raise RuntimeError('broken __str__')

    line = _serve_crash_line(mod, BrokenStr('x'))
    assert '[MCP] serve crashed: <unprintable value>' in line, line
    line = _serve_crash_line(mod, Exception('bind failed'))
    assert '[MCP] serve crashed: bind failed' in line, line


def test_serve_crash_line_survives_a_surrogate_under_strict_stderr(tmp):
    """A surrogate in a caught exception must not kill the
    crash line either."""
    del tmp
    _mcp_load._need_deps()
    mod = _mcp_load._load_mcp('http://127.0.0.1:1')
    line = _serve_crash_line(mod, Exception('bind failed on \udcff'))
    assert '[MCP] serve crashed: bind failed on \\udcff' in line, line


def test_serve_crash_line_survives_a_hostile_decode_return(tmp):
    """A decode() returning a non-string must not reach the crash line's
    f-string: pre-fix the operator got zero stderr bytes."""
    del tmp
    _mcp_load._need_deps()
    mod = _mcp_load._load_mcp('http://127.0.0.1:1')

    class BadFormat:
        def __format__(self, _spec):
            raise RuntimeError('evil format')

    class HostileChain(str):
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


def test_mcp_port_zero_announces_the_actual_bound_port(tmp):
    del tmp
    _mcp_load._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    mod = _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 0)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        _mcp_load._start_in_thread(mod)
        assert mod._bound.wait(timeout=10), mod.startup_error or 'never bound'
        deadline = time.time() + 10
        while (time.time() < deadline and not out.getvalue().strip()
               and not mod.startup_error):
            time.sleep(0.05)
    line = out.getvalue().strip()
    assert f'127.0.0.1:{mod.bound_port}' in line, line
    assert mod.bound_port > 0, line
    assert mod.bridge.transport._base_url == (
        'http://127.0.0.1:1'), mod.bridge.transport._base_url
    _mcp_load._wait_for_mcp(mod.bound_port)


def test_mcp_announces_the_port_once_the_front_end_serves(tmp):
    """The port announcement waits for serve startup; bind time is not
    ready: the journey harness tears its baseline down on this line, and
    a line printed at bind never pays uvicorn's serve startup.
    """
    del tmp
    _mcp_load._need_deps()
    banner, order, handed = io.StringIO(), [], []

    class Server(_mcp_load._FakeUvicornServer):
        def run(self, sockets=None):
            handed.extend(sockets or ())

            async def serve():
                order.append(
                    ('run-begin', 'streamable-http' in banner.getvalue()))
                await self.startup(sockets)
                self.should_exit = True
                order.append(
                    ('startup-done', 'streamable-http' in banner.getvalue()))
            asyncio.run(serve())

    mod = _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 0)
    fake = types.ModuleType('uvicorn')
    fake.__dict__.update({'Config': _mcp_load._FakeConfig,
                          'Server': Server,
                          'Protocol': _mcp_load._FakeProtocol})
    previous = sys.modules.get('uvicorn')
    sys.modules['uvicorn'] = fake
    try:
        with contextlib.redirect_stdout(banner):
            mod._serve()
    finally:
        if previous is None:
            sys.modules.pop('uvicorn', None)
        else:
            sys.modules['uvicorn'] = previous
        for server_socket in handed:
            server_socket.close()
    assert not mod.startup_error, mod.startup_error
    assert order == [('run-begin', False), ('startup-done', True)], order


def test_the_mcp_fixture_ignores_a_squatted_draw(tmp):
    del tmp
    _mcp_load._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    squatter = socket.socket()
    squatter.bind(('127.0.0.1', 0))
    squatter.listen(1)
    taken = squatter.getsockname()[1]
    real_free_port = _util.free_port
    _util.free_port = lambda: taken
    try:
        mod, port = _mcp_load._start_mcp_in_process('http://127.0.0.1:1')
    finally:
        _util.free_port = real_free_port
        squatter.close()
    assert port != taken, 'the listener bound the squatted port'
    _mcp_load._wait_for_mcp(port)
    assert mod is not None


def test_a_persistent_mcp_collision_surfaces_the_verbatim_bind_error(tmp):
    """An explicit squatted MCP port surfaces the original bind error itself.

    What is pinned is the OS bind text surfacing in the child's drained
    output, never how soon it arrives (issue 503). The deadline is a
    reporting bound, not a margin: the child outlives its crashed serve, so
    expiry turns a lost crash line into a failure carrying the output.
    """
    _mcp_load._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    squatter = socket.socket()
    squatter.bind(('127.0.0.1', 0))
    squatter.listen(1)
    taken = squatter.getsockname()[1]
    output = []
    try:
        with _util.bridge(
                tmp,
                env={'DAEDALUS_MCP_PORT': str(taken),
                     'DAEDALUS_TOKEN': _mcp_load.TOK, 'TOKEN': ''},
                output=output) as (_base, _docroot):
            crash_line = None
            deadline = time.time() + 300
            while time.time() < deadline and crash_line is None:
                crash_line = next(
                    (line for line in output if 'serve crashed' in line), None)
                if crash_line is None:
                    time.sleep(0.05)
            assert crash_line is not None, (
                f"no '[MCP] serve crashed' line in the drained output: "
                f'{output!r}')
            assert _util.is_bind_error(crash_line), crash_line
    finally:
        squatter.close()


def test_an_unrelated_crash_naming_the_bind_text_is_not_retried(tmp):
    """A startup crash naming the bind text surfaces that text itself.

    No retry remains to fool: the raised failure carries the crash prefix
    and the bind text verbatim — what it says is the pin.
    """
    del tmp
    _mcp_load._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')
    real_loader = _mcp_load._load_mcp_at_port  # Patch the fixture's globals.

    def crashing_loader(base, port, **kwargs):
        mod = real_loader(base, port, **kwargs)

        def crash(**_settings):
            raise RuntimeError('address already in use')
        mod.mcp.streamable_http_app = crash
        return mod

    captured = []
    _mcp_load._load_mcp_at_port = crashing_loader
    try:
        try:
            _mcp_load._start_mcp_in_process(
                'http://127.0.0.1:1', diagnostics=captured)
        except AssertionError as failure:
            assert 'serve crashed' in str(failure), failure
            assert 'address already in use' in str(failure), failure
        else:
            raise AssertionError('a crashed MCP listener started')
        err_text = captured[0][1]
        assert 'serve crashed: address already in use' in err_text, captured
    finally:
        _mcp_load._load_mcp_at_port = real_loader


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
