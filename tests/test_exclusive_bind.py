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
import asyncio
import contextlib
import io
import socket
import sys
import time
import types
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _daedalus_env  # noqa: E402
import _mcp_load  # noqa: E402
import _util  # noqa: E402


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
    """Stands where uvicorn.Server stands, minus the OS behind it.

    `handed` and `built` are class state so the front end can be subclassed
    the way it subclasses the real one: `_serve` derives its own Server from
    whatever `uvicorn.Server` names, and a lambda is not a base class.
    """
    handed = []
    built = []

    def __init__(self, config):
        self.config = config
        type(self).built.append(self)

    def run(self, sockets=None):
        type(self).handed.extend(sockets or ())


def _serve_with_fake_uvicorn(mod):
    """Run _serve to completion without its real front end.

    The fake stands where uvicorn.Config and uvicorn.Server stand in the
    real _serve, and records the sockets it was handed instead of serving
    them; the caller closes any real socket in that list. The bound-port
    banner `_serve` prints is captured and returned beside `handed`, so the
    suite's streams stay verdict-only. `built` is the Server instances the
    front end constructed, so a caller can tell a derived class from the
    base it was derived from.
    """
    handed = []
    built = []
    banner = io.StringIO()
    fake = types.ModuleType('uvicorn')
    fake.Config = lambda app, **settings: types.SimpleNamespace(
        app=app, settings=settings)
    fake.Server = type('Server', (_FakeUvicornServer,),
                       {'handed': handed, 'built': built})
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
    return handed, banner.getvalue(), built


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
    _mcp_load._need_deps()
    mod = _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 59981)
    created = []
    mod.socket = _StubSocketModule(created)
    mod.WIN32 = True
    handed, banner, _built = _serve_with_fake_uvicorn(mod)
    assert not mod.startup_error, mod.startup_error
    assert '[MCP] streamable-http on 127.0.0.1:59981' in banner, banner
    assert handed == [created[0]], (handed, created)
    assert created[0].events == _exclusive_events() + [
        ('bind', ('127.0.0.1', 59981))], created[0].events


def test_the_posix_mcp_arm_keeps_the_reuse_path(tmp):
    """Darwin reports the flag as the option's bit value (4), Linux as 1;
    zero is unset on both.
    """
    del tmp
    _mcp_load._need_deps()
    mod = _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 0)
    mod.WIN32 = False
    handed, banner, _built = _serve_with_fake_uvicorn(mod)
    assert not mod.startup_error, mod.startup_error
    assert f'127.0.0.1:{mod.bound_port}' in banner, banner
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
    _mcp_load._need_deps()
    mod = _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 59980)
    expected = sys.platform == 'win32'
    assert mod.WIN32 == expected, (mod.WIN32, sys.platform)


def _load_front_end():
    """Load the MCP front end with the real uvicorn importable."""
    _mcp_load._need_deps()
    return _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 0)


def _spin(turns):
    """Advance the running loop `turns` times without waiting on wall time.

    Every wait in these tests is a hand-off the loop already schedules — an
    Event the loop is about to deliver, a coroutine's first step — so a fixed
    number of turns says "the loop had every chance" and never "the machine
    was fast enough".
    """
    async def spin():
        for _ in range(turns):
            await asyncio.sleep(0)
    return spin()


def _send_http(app, headers=()):
    """Drive one HTTP request through an ASGI app to completion."""
    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(_message):
        return None

    scope = {
        'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
        'method': 'POST', 'scheme': 'http', 'path': '/mcp',
        'raw_path': b'/mcp', 'query_string': b'', 'root_path': '',
        'headers': [(b'content-length', b'0')] + list(headers),
    }
    return app(scope, receive, send)


def _stack_of(app):
    """The middleware classes around `app`, outermost first."""
    names = []
    node = app.build_middleware_stack()
    while node is not None:
        names.append(type(node).__name__)
        node = getattr(node, 'app', None)
    return names


def test_serve_hands_uvicorn_a_derived_server(tmp):
    """`_serve` must hand `run` a subclass, not uvicorn's own Server.

    Watching `run` be called with the bound socket passes on a tree that
    polls ten times a second forever, so the derived class itself is the
    thing pinned here: issue 1444 is the base class's serve loop, and the
    fake base carries neither of the two members the derived one owns.
    """
    del tmp
    _mcp_load._need_deps()
    mod = _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 59983)
    handed, _banner, built = _serve_with_fake_uvicorn(mod)
    assert not mod.startup_error, mod.startup_error
    assert len(built) == 1, built
    derived = type(built[0])
    assert issubclass(derived, _FakeUvicornServer), derived
    assert hasattr(derived, 'main_loop'), derived
    assert hasattr(derived, 'request_tick'), derived
    assert len(handed) == 1, handed
    handed[0].close()


def test_the_serve_loop_arms_no_timer_while_it_is_idle(tmp):
    """The serve loop polls nothing, and returns the moment it is told to.

    Stock uvicorn's `main_loop` wakes every 0.1 s and calls `on_tick` on
    each wake, which is ~3M instructions a second on an idle bridge for the
    life of the process (issue 1444). Asserting that `on_tick` is never
    called is what a poll loop cannot survive, and it needs no wall clock:
    the stock loop calls it once before it first sleeps.
    """
    del tmp
    import uvicorn
    mod = _load_front_end()
    server = mod._idle_server_class()(
        uvicorn.Config(mod.mcp.streamable_http_app(), log_level='warning'))
    ticks = []

    async def counting(counter):
        ticks.append(counter)
        return False

    server.on_tick = counting

    async def drive():
        task = asyncio.ensure_future(server.main_loop())
        await _spin(4)
        assert not task.done(), 'the loop returned while should_exit was False'
        assert ticks == [], ticks
        server.should_exit = True
        await _spin(4)
        return task.done()

    assert asyncio.run(drive())


def test_a_request_refreshes_the_date_header(tmp):
    """A served request re-derives the Date header the loop used to cache.

    The cached `default_headers` is what every response carries, so the
    per-request tick has to keep it as current as uvicorn's once-a-second
    refresh did. The Date is compared against the clock either side of the
    request, which is a value assertion and not a duration.
    """
    del tmp
    import uvicorn
    from starlette.applications import Starlette
    mod = _load_front_end()
    config = uvicorn.Config(Starlette(), log_level='warning',
                            headers=[('x-probe', '1')])
    config.load()
    server = mod._idle_server_class()(config)
    holder = {'server': server}
    app = Starlette()
    app.add_middleware(mod._TickMiddleware, holder=holder)
    server.server_state.default_headers = [(b'server', b'stale')]

    async def drive():
        before = time.time()
        await _send_http(app)
        return before, time.time()

    before, after = asyncio.run(drive())
    headers = dict(server.server_state.default_headers)
    assert set(headers) == {b'date', b'server', b'x-probe'}, headers
    assert headers[b'server'] == b'uvicorn', headers
    assert headers[b'x-probe'] == b'1', headers
    sent = parsedate_to_datetime(headers[b'date'].decode()).timestamp()
    assert before - 5 <= sent <= after + 5, (headers, before, after)


def test_the_tick_middleware_sits_inside_the_bearer_auth_middleware(tmp):
    """A request refused on auth must cost no per-request work.

    The tick is the only thing this front end does per request, and the
    refusals most of its traffic draws are auth refusals. The order is read
    off the stack `_serve` actually built, and confirmed by driving one
    refused request through it.
    """
    del tmp
    _mcp_load._need_deps()
    mod = _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 59984)
    handed, _banner, built = _serve_with_fake_uvicorn(mod)
    assert not mod.startup_error, mod.startup_error
    app = built[0].config.app
    names = _stack_of(app)
    assert names.index('BearerAuth') < names.index('_TickMiddleware'), names
    calls = []

    async def spy():
        calls.append(1)

    built[0].request_tick = spy
    asyncio.run(_send_http(app))
    assert calls == [], calls
    handed[0].close()


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='exclusivebind_')


if __name__ == '__main__':
    raise SystemExit(main())
