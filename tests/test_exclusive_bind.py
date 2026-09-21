#!/usr/bin/env python3
"""The listeners refuse to share a port a live listener already holds.

Windows reads SO_REUSEADDR as consent to share the port with any socket
that asks, so a second bridge with a different data root could bind the
port a live bridge holds — uncovered by the data-root lock, which only
covers one root. The MCP front end sets the same flag on its own socket.
On Windows both listeners take SO_EXCLUSIVEADDRUSE instead; on every other
platform the SO_REUSEADDR bind is kept byte-for-byte, because POSIX needs
it for a quick restart through TIME_WAIT.
"""
import importlib.util
import socket
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _daedalus_env  # noqa: E402
import _mcp_load  # noqa: E402
import _util  # noqa: E402

DEPS = all(importlib.util.find_spec(name) is not None
           for name in ('httpx', 'mcp', 'starlette'))


def _need_deps():
    if not DEPS:
        _util.skip(
            'daedalus_mcp.server dependencies (httpx/mcp/starlette) not '
            'installed')


class _StubSocket:
    """Records the calls a bind path makes, without an OS behind it."""

    def __init__(self, created):
        self.events = []
        self.created = created
        self.bound = ('127.0.0.1', 0)
        created.append(self)

    def setsockopt(self, level, option, value):
        self.events.append(('setsockopt', level, option, value))

    def bind(self, address):
        self.events.append(('bind', address))
        self.bound = address

    def getsockname(self):
        return self.bound


class _StubSocketModule:
    """The socket names the win32 branches read, minus the OS behind them.

    SO_EXCLUSIVEADDRUSE exists only in Windows CPython, so a test that
    drives the win32 arm on another platform supplies its own name. The
    value never reaches an operating system here; only that the branch read
    this name and no other is what the assertions pin.
    """

    AF_INET = socket.AF_INET
    SOCK_STREAM = socket.SOCK_STREAM
    SOL_SOCKET = socket.SOL_SOCKET
    SO_EXCLUSIVEADDRUSE = -5

    def __init__(self, created):
        self.created = created
        self.socket = lambda _family, _type: _StubSocket(created)


def _exclusive_events():
    return [('setsockopt', socket.SOL_SOCKET,
             _StubSocketModule.SO_EXCLUSIVEADDRUSE, 1)]


def _reuse_events(port):
    return [('setsockopt', socket.SOL_SOCKET, socket.SO_REUSEADDR, 1),
            ('bind', ('127.0.0.1', port))]


class _FakeUvicornServer:
    def __init__(self, config, handed=None):
        self.config = config
        self.handed = handed

    def run(self, sockets=None):
        self.handed.extend(sockets or ())


def _serve_with_fake_uvicorn(mod):
    """Run _serve to completion without its real front end.

    The fake stands where uvicorn.Config and uvicorn.Server stand in the
    real _serve, and records the sockets it was handed instead of serving
    them; the caller closes any real socket in that list.
    """
    handed = []
    fake = types.ModuleType('uvicorn')
    fake.Config = lambda app, **settings: types.SimpleNamespace(
        app=app, settings=settings)
    fake.Server = lambda config: _FakeUvicornServer(config, handed)
    previous = sys.modules.get('uvicorn')
    sys.modules['uvicorn'] = fake
    try:
        mod._serve()
    finally:
        if previous is None:
            sys.modules.pop('uvicorn', None)
        else:
            sys.modules['uvicorn'] = previous
    return handed


def _load_server(tmp):
    with _daedalus_env.isolated({
            'DAEDALUS_DIR': str(tmp), 'DAEDALUS_PORT': '0'}):
        return _util.load(_util.ROOT / 'server.py', 'server_exclusive_bind')


def _bridge_instance(mod, created, address):
    server = mod.ThreadingHTTPServer.__new__(mod.ThreadingHTTPServer)
    server.socket = _StubSocket(created)
    server.server_address = address
    return server


def test_the_windows_bridge_arm_excludes_the_port(tmp):
    """The win32 bind disables reuse and takes the exclusive option first.

    The module's own platform name drives the arm, so a Linux or macOS
    runner takes the branch a Windows bridge takes.
    """
    mod = _load_server(Path(tmp) / 'win32-bridge')
    created = []
    mod.socket = _StubSocketModule(created)
    mod.WIN32 = True
    address = ('127.0.0.1', 64738)
    server = _bridge_instance(mod, created, address)
    server.server_bind()
    assert not server.allow_reuse_address
    assert server.socket.events == _exclusive_events() + [
        ('bind', address)], server.socket.events
    assert server.server_address == address
    assert server.server_name == '127.0.0.1'
    assert server.server_port == 64738


def test_the_posix_bridge_arm_keeps_the_reuse_path(tmp):
    """The non-win32 bind stays byte-for-byte the stdlib reuse bind.

    Both arms are driven by the same name the Windows arm uses, so the
    assertion holds on a Windows runner too.
    """
    mod = _load_server(Path(tmp) / 'posix-bridge')
    created = []
    mod.WIN32 = False
    address = ('127.0.0.1', 64738)
    server = _bridge_instance(mod, created, address)
    server.server_bind()
    assert bool(server.allow_reuse_address) is True
    assert server.socket.events == _reuse_events(64738), server.socket.events


def test_the_posix_bridge_listener_keeps_reuse_address(tmp):
    """getsockopt on the bound socket is the only proof that survives the
    factory: the flag is applied by the standard library inside the bind
    the bridge overrides, so no call record of the override's own shows
    it. Darwin reports the flag as the option's bit value (4), Linux as
    1; zero is unset on both.
    """
    if sys.platform == 'win32':
        _util.skip('the Windows arm is pinned by the recorded binds')
    with _daedalus_env.isolated({
            'DAEDALUS_DIR': str(tmp), 'DAEDALUS_PORT': '0'}):
        mod = _util.load(_util.ROOT / 'server.py', 'server_exclusive_live')
    server = mod.ThreadingHTTPServer(('127.0.0.1', 0), mod.Handler)
    try:
        observed = server.socket.getsockopt(
            socket.SOL_SOCKET, socket.SO_REUSEADDR)
        assert observed != 0, (
            sys.platform, observed, socket.SO_REUSEADDR,
            socket.SOL_SOCKET, server.allow_reuse_address)
    finally:
        server.server_close()


def test_the_windows_mcp_arm_excludes_the_port(tmp):
    del tmp
    _need_deps()
    mod = _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 59981)
    created = []
    mod.socket = _StubSocketModule(created)
    mod.WIN32 = True
    handed = _serve_with_fake_uvicorn(mod)
    assert not mod.startup_error, mod.startup_error
    assert handed == [created[0]], (handed, created)
    assert created[0].events == _exclusive_events() + [
        ('bind', ('127.0.0.1', 59981))], created[0].events


def test_the_posix_mcp_arm_keeps_the_reuse_path(tmp):
    """Darwin reports the flag as the option's bit value (4), Linux as 1;
    zero is unset on both.
    """
    del tmp
    _need_deps()
    mod = _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 0)
    mod.WIN32 = False
    handed = _serve_with_fake_uvicorn(mod)
    assert not mod.startup_error, mod.startup_error
    assert len(handed) == 1, handed
    sock = handed[0]
    assert mod.bound_port == sock.getsockname()[1]
    assert mod.bound_port > 0
    observed = sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
    assert observed != 0, (
        sys.platform, observed, socket.SO_REUSEADDR, socket.SOL_SOCKET)
    sock.close()


def test_the_armed_bridge_platform_value_matches_the_host(tmp):
    """The bridge's platform read must arm on Windows and stay off elsewhere.

    Every other test here overwrites the platform name, so a corrupted
    read — an inverted comparison, a hoisted False — would pass this whole
    suite on Linux while silently reintroducing the defect on Windows.
    This pin loads the module fresh and holds the read against the host,
    which is what bites on the Windows legs. It needs no MCP dependencies,
    so it stands on its own.
    """
    mod = _load_server(Path(tmp) / 'armed-bridge')
    expected = sys.platform == 'win32'
    assert mod.WIN32 == expected, (mod.WIN32, sys.platform)


def test_the_armed_mcp_platform_value_matches_the_host(tmp):
    """The front end's platform read, held to the host the same way."""
    del tmp
    _need_deps()
    mod = _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 59980)
    expected = sys.platform == 'win32'
    assert mod.WIN32 == expected, (mod.WIN32, sys.platform)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='exclusivebind_')


if __name__ == '__main__':
    raise SystemExit(main())
