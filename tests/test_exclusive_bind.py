#!/usr/bin/env python3
"""The listeners refuse to share a port a live listener already holds.

Windows reads SO_REUSEADDR as consent to share the port with any socket
that asks, so a second bridge with a different data root could bind the
port a live bridge holds — uncovered by the data-root lock, which only
covers one root. The MCP front end sets the same flag on its own socket.
On Windows both listeners take SO_EXCLUSIVEADDRUSE instead; on every other
platform the SO_REUSEADDR bind is kept byte-for-byte, because POSIX needs
it for a quick restart through TIME_WAIT.

The rest of this suite covers the MCP front end's serve loop. Its
`Server` subclass arms no timer while nothing is asked of it, so the work
uvicorn's 0.1 s loop used to do — refreshing the cached `Date` header, and
consulting `limit_max_requests` and `callback_notify` — runs per request
instead, and a refusal from the auth middleware or from uvicorn's own
parser reads that cached list too. The controls pin where the refresh
lands, when it lands relative to the app, and that the loop it replaced
armed nothing.
"""
import asyncio
import socket
import sys
import types
from email.utils import formatdate
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _daedalus_env  # noqa: E402
import _mcp_load  # noqa: E402
import _util  # noqa: E402

# A fixed instant, so a Date assertion compares bytes instead of carrying a
# margin. 2001-09-09T01:46:40Z, far enough from any plausible boundary
# that a header read back in local time cannot land on the same second.
FROZEN = 1000000000.0


def _frozen_date(at):
    return formatdate(at, usegmt=True).encode()


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
    handed, banner, _built = _mcp_load._serve_with_fake_uvicorn(mod)
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
    handed, banner, _built = _mcp_load._serve_with_fake_uvicorn(mod)
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


def _idle_front_end():
    """The MCP front end module, loaded with the real uvicorn importable."""
    _mcp_load._need_deps()
    return _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 0)


def _spin(turns):
    """Advance the running loop `turns` times without waiting on wall time.
    Every wait in these tests is a hand-off the loop already schedules — an
    Event the loop is about to deliver, a coroutine's first step — so a
    fixed number of turns says "the loop had every chance" and never "the
    machine was fast enough"."""
    async def spin():
        for _ in range(turns):
            await asyncio.sleep(0)
    return spin()


def _send_http(app, headers=(), path='/mcp'):
    """Drive one HTTP request through an ASGI app, returning what it sent."""
    sent = []
    scope = {
        'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
        'method': 'POST', 'scheme': 'http', 'path': path,
        'raw_path': path.encode(), 'query_string': b'', 'root_path': '',
        'headers': [(b'content-length', b'0')] + list(headers),
    }

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        sent.append(message)

    async def drive():
        await app(scope, receive, send)
        return sent

    return drive()


def _armed_timer_delays(build):
    """Run `build(armed)` on a loop that records every timer it arms. `await
    wait_for(x, 0.1)` arms a timeout the moment it is entered and a wait on
    an Event arms none; `call_at` is where both spellings go through, and
    `call_soon` touches neither. Recorded on the loop itself, so the
    assertion is on a property and never on how fast the machine was.
    Returns the coroutine's result beside the recording."""
    loop = asyncio.new_event_loop()
    armed = []
    original = loop.call_at

    def call_at(when, callback, *args, context=None):
        armed.append(round(when - loop.time(), 3))
        return original(when, callback, *args, context=context)

    loop.call_at = call_at
    try:
        return loop.run_until_complete(build(armed)), armed
    finally:
        loop.close()


class _WaitCounter:
    """Stands where the serve loop's Event stands, counting its waits.

    A loop that wakes itself calls `wait` again on every wake, and a loop
    that spins on `sleep(0)` never calls it at all. Counting the waits
    tells those apart without a clock, and without depending on the
    recorder below being honest.
    """

    def __init__(self, event):
        self._event = event
        self.waits = 0

    def clear(self):
        return self._event.clear()

    def set(self):
        return self._event.set()

    def wait(self):
        self.waits += 1
        return self._event.wait()


def _freeze_the_front_end_clock(mod, at=FROZEN):
    """Pin the front end's clock so a Date assertion needs no margin.
    `daedalus_mcp.server` reads `time.time()` and nothing else out of the
    `time` module, so replacing the module is enough. The header is then
    compared byte for byte against `formatdate(at, usegmt=True)`, which is
    what makes `usegmt=False` observable: under `TZ=UTC` a local-time
    rendering of the same instant is byte-identical to the GMT one."""
    mod.time = types.SimpleNamespace(time=lambda: at)
    return at


def test_serve_hands_uvicorn_a_derived_server(tmp):
    """`_serve` must hand `run` a subclass, not uvicorn's own Server. Watching
    `run` be called with the bound socket passes on a tree that polls ten
    times a second forever, so the derived class itself is the thing pinned
    here: issue 1444 is the base class's serve loop, and the fake base
    carries neither of the two members the derived one owns."""
    del tmp
    _mcp_load._need_deps()
    mod = _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 59983)
    handed, _banner, built = _mcp_load._serve_with_fake_uvicorn(mod)
    assert not mod.startup_error, mod.startup_error
    assert len(built) == 1, built
    derived = type(built[0])
    assert issubclass(derived, _mcp_load._FakeUvicornServer), derived
    assert hasattr(derived, 'main_loop'), derived
    assert hasattr(derived, 'request_tick'), derived
    assert len(handed) == 1, handed
    handed[0].close()


def _idle_server(mod, config=None, date_header=True):
    """A real IdleServer on a real uvicorn config, ready to be driven."""
    import uvicorn
    if config is None:
        config = uvicorn.Config(mod.mcp.streamable_http_app(),
                                log_level='warning',
                                date_header=date_header)
    config.load()
    server = mod._idle_server_class()(config)
    server.refresh_default_headers()
    return server


def _config_with_probe_headers():
    import uvicorn
    from starlette.applications import Starlette
    return uvicorn.Config(Starlette(), log_level='warning',
                          headers=[('x-probe', '1')])


def test_the_tick_omits_the_date_when_the_config_disables_it(tmp):
    """`date_header=False` means no Date, as uvicorn's own tick reads it. The
    other arm of `_default_headers`; every other case runs with the header
    on."""
    del tmp
    server = _idle_server(_idle_front_end(), date_header=False)
    headers = dict(server.server_state.default_headers)
    assert set(headers) == {b'server'}, headers


def test_the_serve_loop_parks_on_one_wait_and_arms_no_timer(tmp):
    """No timer while idle, one wait, and a return on either exit. A poll
    needs wall time to reach its first tick, so counting ticks cannot
    separate the two loops; what the loop does while it waits can. A
    re-waiting loop counts more than one wait and a sleep(0) spin counts
    none. The exit arrives after entry in the first drive and before it in
    the second, which the clear at entry would otherwise swallow."""
    del tmp
    mod = _idle_front_end()

    async def drive_after(armed):
        server = _idle_server(mod)
        counter = _WaitCounter(server._wake)
        server._wake = counter
        task = asyncio.ensure_future(server.main_loop())
        await _spin(4)
        assert not task.done(), 'the loop returned before should_exit'
        idle = list(armed)
        server.should_exit = True
        await _spin(4)
        return idle, counter.waits, task.done()

    async def drive_first(_armed):
        server = _idle_server(mod)
        server.should_exit = True
        task = asyncio.ensure_future(server.main_loop())
        await _spin(4)
        return task.done()

    (idle, waits, after), _armed = _armed_timer_delays(drive_after)
    assert idle == [], ('the loop armed a timer while idle', idle)
    assert waits == 1, waits
    assert after, 'the loop did not return once should_exit was set'
    first, _armed = _armed_timer_delays(drive_first)
    assert first, 'the loop waited on an exit that was already set'


def test_the_timer_recorder_sees_a_timer_when_one_is_armed(tmp):
    """The recorder's own positive control: arm one and be seen. Without it
        the idle assertion passes against a recorder that records nothing.
        the idle assertion passes against a recorder that records nothing."""
    del tmp

    async def drive(_armed):
        await asyncio.sleep(0.1)

    _result, armed = _armed_timer_delays(drive)
    assert armed == [0.1], armed


def test_a_request_refreshes_the_date_header_the_cycle_already_holds(tmp):
    """A served request re-derives the Date on the list the cycle captured.
        captured below is that object, taken the way a cycle takes it: the
        protocols concatenate what they got when the response starts, so a
        refresh that rebinds never reaches it.
        refresh that rebinds never reaches it."""
    del tmp
    from starlette.applications import Starlette
    mod = _idle_front_end()
    frozen = _freeze_the_front_end_clock(mod)
    server = _idle_server(mod, _config_with_probe_headers())
    app = Starlette()
    app.state.server = server
    app.add_middleware(mod._TickMiddleware)
    server.server_state.default_headers[:] = [(b'server', b'stale')]
    captured = server.server_state.default_headers

    asyncio.run(_send_http(app))
    assert server.server_state.default_headers is captured, (
        'the refresh rebound the attribute instead of updating it')
    headers = dict(captured)
    assert set(headers) == {b'date', b'server', b'x-probe'}, headers
    assert headers[b'server'] == b'uvicorn', headers
    assert headers[b'x-probe'] == b'1', headers
    assert headers[b'date'] == _frozen_date(frozen), headers


def test_the_tick_runs_before_the_app_reads_the_headers(tmp):
    """The refresh lands ahead of the app, not behind it. The response is
    assembled from the cached list as the app sends its start message, so a
    tick after the app hands back the staleness the in-place refresh exists
    to remove. The app below reads the list at the moment it is called and
    answers with what it saw — which is the only subject that can tell the
    two orders apart."""
    del tmp
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    mod = _idle_front_end()
    frozen = _freeze_the_front_end_clock(mod)
    server = _idle_server(mod, _config_with_probe_headers())
    seen = []

    async def endpoint(_request):
        seen.append(dict(server.server_state.default_headers))
        return PlainTextResponse('ok')

    app = Starlette(routes=[Route('/probe', endpoint, methods=['POST'])])
    app.state.server = server
    app.add_middleware(mod._TickMiddleware)
    server.server_state.default_headers[:] = [(b'server', b'stale')]

    asyncio.run(_send_http(app, path='/probe'))
    assert len(seen) == 1, seen
    assert seen[0][b'date'] == _frozen_date(frozen), seen


def test_the_request_tick_keeps_the_list_the_cycles_captured(tmp):
    """Eleven ticks in, the bound list is still the one the cycles hold.
        uvicorn's own on_tick REBINDS default_headers on every tenth
        counter, orphaning the object every in-flight cycle captured.
        counter, orphaning the object every in-flight cycle captured."""
    del tmp
    mod = _idle_front_end()
    frozen = _freeze_the_front_end_clock(mod)
    server = _idle_server(mod, _config_with_probe_headers())
    captured = server.server_state.default_headers

    async def drive():
        for _ in range(12):
            await server.request_tick()

    asyncio.run(drive())
    assert server.server_state.default_headers is captured, (
        'a tick rebound the list the in-flight cycles hold')
    headers = dict(captured)
    assert set(headers) == {b'date', b'server', b'x-probe'}, headers
    assert headers[b'date'] == _frozen_date(frozen), headers


def test_a_connection_refreshes_the_headers_a_parser_refusal_answers(tmp):
    """A connection refreshes before uvicorn's parser can refuse a request.
        A malformed request line is answered by the protocol itself, from
        the cached list read live, never reaching the middleware at all.
        the cached list read live, never reaching the middleware at all."""
    del tmp
    _mcp_load._need_deps()
    mod = _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 59985)
    handed, _banner, built = _mcp_load._serve_with_fake_uvicorn(mod)
    assert not mod.startup_error, mod.startup_error
    server = built[0]
    frozen = _freeze_the_front_end_clock(mod)
    protocol = server.config.http_protocol_class
    assert issubclass(protocol, _mcp_load._FakeProtocol), protocol
    assert protocol is not _mcp_load._FakeProtocol, protocol
    server.server_state.default_headers[:] = [(b'server', b'stale')]
    captured = server.server_state.default_headers

    connection = protocol(
        config=server.config, server_state=server.server_state)
    transport = type('T', (), {
        'get_extra_info': lambda _s, _n, default=None: default})()
    connection.connection_made(transport)

    assert connection.connections == 1, connection.connections
    assert server.server_state.default_headers is captured
    headers = dict(captured)
    assert headers[b'date'] == _frozen_date(frozen), headers
    handed[0].close()


def test_the_serve_loop_honours_the_max_requests_limit(tmp):
    """`limit_max_requests` still ends the serve, now per request. uvicorn
    decides it inside `on_tick`, so a request tick that stopped consulting
    `on_tick` would silently drop the limit. The request that takes the
    served total to the limit is the one that sets `should_exit`, and the
    ones after it keep it set."""
    del tmp
    import uvicorn
    from starlette.applications import Starlette
    mod = _idle_front_end()
    config = uvicorn.Config(
        Starlette(), log_level='warning', limit_max_requests=2,
        limit_max_requests_jitter=0)
    server = _idle_server(mod, config)
    assert server.limit_max_requests == 2, server.limit_max_requests

    async def drive():
        seen = []
        for total in range(3):
            server.server_state.total_requests = total
            await server.request_tick()
            seen.append(server.should_exit)
        return seen

    assert asyncio.run(drive()) == [False, False, True]


def test_the_serve_loop_runs_the_configured_callback_notify(tmp):
    """`callback_notify` still fires, on uvicorn's own once-per-ten gate.
    `on_tick` only reaches the callback on a counter divisible by ten, so
    the counter the request tick advances is load-bearing: a tick that
    passed a constant would either never notify or notify on every request."""
    del tmp
    import uvicorn
    from starlette.applications import Starlette
    mod = _idle_front_end()
    notified = []

    async def callback_notify():
        notified.append(1)

    config = uvicorn.Config(Starlette(), log_level='warning',
                            callback_notify=callback_notify,
                            timeout_notify=0)
    server = _idle_server(mod, config)

    async def drive():
        for _ in range(10):
            await server.request_tick()

    asyncio.run(drive())
    assert notified, 'callback_notify never ran'
    assert len(notified) == 1, notified


def test_startup_populates_the_headers_a_below_asgi_refusal_sends(tmp):
    """`startup` fills the cache before any request can be answered. A request
    uvicorn refuses below ASGI never reaches the middleware, so the cached
    headers it answers with have to be in place from startup."""
    del tmp
    import uvicorn
    mod = _idle_front_end()
    frozen = _freeze_the_front_end_clock(mod)
    server = _idle_server(mod, _config_with_probe_headers())
    server.server_state.default_headers[:] = []
    reached = []

    async def base_startup(_self, sockets=None):
        reached.append(sockets)

    original = uvicorn.Server.startup
    uvicorn.Server.startup = base_startup
    try:
        asyncio.run(server.startup(sockets=None))
    finally:
        uvicorn.Server.startup = original
    assert reached == [None], reached
    headers = dict(server.server_state.default_headers)
    assert set(headers) == {b'date', b'server', b'x-probe'}, headers
    assert headers[b'date'] == _frozen_date(frozen), headers


def test_the_tick_middleware_skips_a_non_http_scope(tmp):
    """Only an HTTP request ticks. Starlette routes every scope type through
        the stack, so the lifespan startup and shutdown would each tick.
        the stack, so the lifespan startup and shutdown would each tick."""
    del tmp
    from starlette.applications import Starlette
    mod = _idle_front_end()
    server = _idle_server(mod, _config_with_probe_headers())
    calls = []

    async def spy():
        calls.append(1)

    server.request_tick = spy
    app = Starlette()
    app.state.server = server
    app.add_middleware(mod._TickMiddleware)
    down = []

    async def receive():
        return {'type': 'lifespan.startup'}

    async def send(message):
        down.append(message['type'])

    asyncio.run(app({'type': 'lifespan', 'asgi': {'version': '3.0'}},
                    receive, send))
    assert calls == [], calls
    assert 'lifespan.startup.complete' in down, down


def test_the_tick_middleware_sits_outside_the_bearer_auth_middleware(tmp):
    """The tick is on the outside of the auth middleware, and must be. Read
    off the stack `_serve` built, not off the order the two are added in.
    in."""
    del tmp
    _mcp_load._need_deps()
    mod = _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 59984)
    handed, _banner, built = _mcp_load._serve_with_fake_uvicorn(mod)
    assert not mod.startup_error, mod.startup_error
    names, node = [], built[0].config.app.build_middleware_stack()
    while node is not None:
        names.append(type(node).__name__)
        node = getattr(node, 'app', None)
    assert names.index('_TickMiddleware') < names.index('BearerAuth'), names
    handed[0].close()


def test_a_refused_request_still_refreshes_the_date_header(tmp):
    """A request refused on auth answers with the Date it just refreshed. The
        clock moves between building the app and driving the refusal, so a
        tick that does not run there leaves the previous instant's date.
        tick that does not run there leaves the previous instant's date."""
    del tmp
    _mcp_load._need_deps()
    mod = _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 59986)
    handed, _banner, built = _mcp_load._serve_with_fake_uvicorn(mod)
    assert not mod.startup_error, mod.startup_error
    app = built[0].config.app
    server = built[0]
    stale = _freeze_the_front_end_clock(mod)
    server.server_state.default_headers[:] = [(b'server', b'stale')]
    captured = server.server_state.default_headers
    fresh = _freeze_the_front_end_clock(mod, FROZEN + 3600)

    sent = asyncio.run(_send_http(app))

    assert sent and sent[0]['status'] == 401, sent
    assert server.server_state.default_headers is captured
    headers = dict(captured)
    assert headers[b'date'] == _frozen_date(fresh), headers
    assert headers[b'date'] != _frozen_date(stale), headers
    handed[0].close()


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='exclusivebind_')


if __name__ == '__main__':
    raise SystemExit(main())
