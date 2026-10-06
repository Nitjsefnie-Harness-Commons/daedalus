#!/usr/bin/env python3
"""Starting and serving the MCP front end in process.

Split from tests/test_mcp_server.py: the start_in_thread contract, the
serve crash line's hostile-str survival, the port announcement and the
collision surfaces.
"""
import asyncio
import contextlib
import io
import os
import importlib.util
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


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
