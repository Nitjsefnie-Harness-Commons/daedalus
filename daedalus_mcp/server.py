"""Daedalus MCP server — exposes the extension command surface as MCP tools.

Runs in-process alongside server.py as a daemon thread on 127.0.0.1:8086 by
default (override with DAEDALUS_MCP_PORT), fronted by a reverse proxy at /mcp.
Tool handlers reach the bridge over HTTP rather than sharing its state, which
is the same indirection the CLI uses.

The Bearer token is compared with the bridge token resolved by the CLI's
existing configuration path before it enters the _token ContextVar and is
forwarded to the local bridge. Missing configuration fails closed.
"""
import asyncio
import contextlib
import os, socket, sys, threading, time
from email.utils import formatdate
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

if __name__ == '__main__' and __package__ in (None, ''):
    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from daedalus_mcp import auth
from daedalus_mcp import request_guard
from daedalus_mcp import tools_cookies
from daedalus_mcp import tools_css
from daedalus_mcp import tools_eval
from daedalus_mcp import tools_hotfixes
from daedalus_mcp import tools_media
from daedalus_mcp import tools_network
from daedalus_mcp import tools_tabs
from daedalus_mcp.transport import BridgeSession, BridgeTransport
from daedalus_cli.output import configure_stdio
from daedalus_bridge.env_config import env_int
from daedalus_bridge.log_safe import log_safe

# Same reason as the bridge: this process prints crash lines carrying values
# it did not choose. See server.py.
configure_stdio()

# The standalone MCP entry point derives its bridge URL from DAEDALUS_PORT.
# The in-process server passes the bridge's actual bound URL to
# start_in_thread, which matters when DAEDALUS_PORT=0. DAEDALUS_LOCAL_URL
# remains the explicit override for a standalone MCP deployment fronting
# a bridge that runs elsewhere.
LOCAL_URL = os.environ.get(
    'DAEDALUS_LOCAL_URL',
    f'http://127.0.0.1:{os.environ.get("DAEDALUS_PORT", "8081")}')


MCP_PORT = env_int('DAEDALUS_MCP_PORT', 8086, 0, 65535)
# Mirrors the bridge's own DAEDALUS_MAX_BODY_SIZE, default and bound alike.
# The front end had no bound at all, so one unauthenticated request could
# make the process hold whatever it chose to send.
MAX_BODY_SIZE = env_int(
    'DAEDALUS_MCP_MAX_BODY_SIZE', 64 * 1024 * 1024, 0)
_token = request_guard.request_token
# The app auto-enables DNS rebinding protection for a localhost bind only when
# it is given no settings of its own; these are passed explicitly, so the list
# has to include the public hostname the reverse proxy fronts us with or
# proxied requests are rejected with a 421.
ALLOWED_HOSTS = [h.strip() for h in os.environ.get(
    'DAEDALUS_MCP_ALLOWED_HOSTS',
    '127.0.0.1:*,localhost:*'
).split(',') if h.strip()]

bridge = BridgeSession(LOCAL_URL, _token)

mcp = MCPServer('daedalus')
tool_module_inventory = []


def _register_tool_module(module):
    """Bind nested tool closures to this composition's bridge.

    Returning the map exposes the exact callables registered with MCP.
    """
    tools = module.register(mcp, bridge)
    tool_module_inventory.append((module, tools))
    return tools


tabs_tools = _register_tool_module(tools_tabs)
list_tabs = tabs_tools['list_tabs']
open_tab = tabs_tools['open_tab']
open_tabs = tabs_tools['open_tabs']
focus_tab = tabs_tools['focus_tab']
close_tab = tabs_tools['close_tab']
ext_navigate = tabs_tools['ext_navigate']
ext_reload = tabs_tools['ext_reload']

eval_tools = _register_tool_module(tools_eval)
exec = eval_tools['exec']
put = eval_tools['put']
result = eval_tools['result']
ping = eval_tools['ping']
navigate = eval_tools['navigate']
reload = eval_tools['reload']
title = eval_tools['title']
url = eval_tools['url']
ext_self_reload = eval_tools['ext_self_reload']

media_tools = _register_tool_module(tools_media)
screenshot = media_tools['screenshot']
uploads = media_tools['uploads']
delete_upload = media_tools['delete_upload']
segment_job = media_tools['segment_job']
segment_status = media_tools['segment_status']
allow_segment_origin = media_tools['allow_segment_origin']
revoke_segment_origin = media_tools['revoke_segment_origin']
list_segment_origins = media_tools['list_segment_origins']

cookies_tools = _register_tool_module(tools_cookies)
get_cookies = cookies_tools['get_cookies']
set_cookie = cookies_tools['set_cookie']
remove_cookie = cookies_tools['remove_cookie']
clear_cookies = cookies_tools['clear_cookies']

css_tools = _register_tool_module(tools_css)
inject_css = css_tools['inject_css']
remove_css = css_tools['remove_css']
block_requests = css_tools['block_requests']
unblock_requests = css_tools['unblock_requests']
list_block_rules = css_tools['list_block_rules']

hotfix_tools = _register_tool_module(tools_hotfixes)
store_hotfix = hotfix_tools['store_hotfix']
clear_hotfix = hotfix_tools['clear_hotfix']
clear_hotfixes = hotfix_tools['clear_hotfixes']
list_hotfixes = hotfix_tools['list_hotfixes']
set_permanent = hotfix_tools['set_permanent']

network_tools = _register_tool_module(tools_network)
net_capture = network_tools['net_capture']
net_capture_stop = network_tools['net_capture_stop']
net_capture_get = network_tools['net_capture_get']
cdp = network_tools['cdp']
fetch_timings = network_tools['fetch_timings']


# A cell rather than a rebound global: this flag is read and written only
# inside start_in_thread, and a module global written there reads as dead.
_start_state = {'started': False}

# The listener's actual port, for whoever started it: with DAEDALUS_MCP_PORT=0
# the kernel picks, so anything printed or probed must come from the bound
# socket, never from the configured value. _bound/_serve set these for
# in-process callers; the child-process variant travels on the startup line.
bound_port = 0
startup_error = ''
_bound = threading.Event()


# The bridge's listener makes the same Windows exclusion, and a bare
# SO_REUSEADDR here would let a second front end share a live port the same
# way. Read once so the decision is one patchable name.
WIN32 = sys.platform == 'win32'


def _default_headers(config):
    """The headers every response carries, as uvicorn's tick builds."""
    if config.date_header:
        date = [(b'date', formatdate(time.time(), usegmt=True).encode())]
    else:
        date = []
    return date + config.encoded_headers


class _TickMiddleware:
    """The per-request tick, ahead of every HTTP request.

    Outermost, and it has to stay there: a refusal answers like any other
    response, and its Date comes from the cached list a tick inside auth
    would leave as old as the last accepted request.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Ahead of the app, not behind it: the response is assembled from
        # the cached list as the app sends its start message, so a refresh
        # after that hands back the staleness the refresh exists to remove.
        if scope['type'] == 'http':
            await scope['app'].state.server.request_tick()
        await self.app(scope, receive, send)


def _refreshing_protocol_class(base):
    """`base` with the cached response headers refreshed per connection.

    A request uvicorn refuses below ASGI — a malformed request line, which
    `httptools_impl.send_400_response` answers by iterating
    `server_state.default_headers` live — never reaches the middleware, so
    without this its Date is as old as the last request. One refresh per
    connection bounds that by connection rate rather than by wall time, and
    leaves the httptools-or-h11 choice exactly as uvicorn made it.
    """

    class Refreshing(base):
        def connection_made(self, transport):
            self.server_state.default_headers[:] = _default_headers(
                self.config)
            super().connection_made(transport)

    return Refreshing


def _idle_server_class():
    """uvicorn.Server whose serve loop arms no timer while nothing is asked.

    Stock, that loop wakes every 0.1 s for the life of the process and calls
    on_tick on each wake, for nothing. This one waits on an Event that only
    a request, a connection or a shutdown wakes.

    Built here rather than at module scope because the suites drive `_serve`
    with a fake `uvicorn` in sys.modules, and a class defined at import time
    would bind the real one before they can.

    Four uvicorn names carry rename risk, and each fails its own way.
    `main_loop` and `startup` are OVERRIDDEN, and an override cannot raise
    on a rename of the name it overrides: uvicorn dispatches through `self`,
    so the rename makes ours dead code and its own method runs. A renamed
    `main_loop` is that 0.1 s poll back, with no error and no traceback. A
    renamed `startup` skips refresh_default_headers, and ServerState starts
    with an empty header list, so every request uvicorn refuses below ASGI
    ships with no Date and no server header. `should_exit` is an attribute
    this subclass replaces with a property, so a rename to a different
    attribute leaves the property in place and permanently False: on_tick's
    `if self.should_exit` never reads true and a graceful exit silently
    never happens. `on_tick` we CALL rather than override, so its rename is
    an AttributeError on the first request — the good case.
    """
    import uvicorn

    class IdleServer(uvicorn.Server):
        # Server.__init__ assigns should_exit, so the setter's two members
        # have to exist before it runs.
        def __init__(self, config):
            self._should_exit = False
            self._wake = asyncio.Event()
            self._counter = 0
            super().__init__(config)

        @property
        def should_exit(self):
            return self._should_exit

        @should_exit.setter
        def should_exit(self, value):
            self._should_exit = value
            self._wake.set()

        async def main_loop(self):
            # No timeout: a timer here is the cost again. The check comes
            # before the clear, which drops what the constructor's
            # assignment already set.
            if self.should_exit:
                return
            self._wake.clear()
            await self._wake.wait()

        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            self.refresh_default_headers()

        def refresh_default_headers(self):
            """Rebuild `default_headers` the way uvicorn's own tick does.

            Through a slice, with `request_tick` putting the identity
            back: the protocols capture this list at RequestReceived and
            concatenate the object they captured when the response starts,
            so a rebind outside that restore orphans the response.
            """
            self.server_state.default_headers[:] = _default_headers(
                self.config)

        async def request_tick(self):
            """The work uvicorn's on_tick did ten times a second.

            The counter advances once per request, so on_tick's
            once-per-ten branch — and with it max-requests,
            callback_notify and the exit decision — still happens. It is
            not wrapped the way uvicorn wraps it because nothing reads the
            difference: on_tick sees `counter % 10`, and 864000 % 10 == 0.
            """
            self._counter += 1
            pinned = self.server_state.default_headers
            self.refresh_default_headers()
            try:
                if await self.on_tick(self._counter):
                    self.should_exit = True
            finally:
                # on_tick rebinds that attribute on every tenth tick, and
                # an on_tick that raised would leave it rebound: either way
                # the in-flight cycles hold an orphan.
                if self.server_state.default_headers is not pinned:
                    pinned[:] = self.server_state.default_headers
                    self.server_state.default_headers = pinned

    return IdleServer


def _serve():
    global bound_port, startup_error
    try:
        app = mcp.streamable_http_app(
            # SDK needs >0; auth enforces 0.
            max_request_body_size=max(MAX_BODY_SIZE, 1),
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=ALLOWED_HOSTS,
            ),
        )
        app.add_middleware(
            auth.BearerAuth,
            max_body_size=MAX_BODY_SIZE,
        )
        # Added last, so Starlette's user-middleware list leaves it on the
        # outside: the refusal's Date comes from the same cached list.
        app.add_middleware(_TickMiddleware)

        inner_lifespan = app.router.lifespan_context

        @contextlib.asynccontextmanager
        async def lifespan_context(_app):
            try:
                async with inner_lifespan(_app):
                    yield
            finally:
                await BridgeTransport.close_current_loop_clients()

        app.router.lifespan_context = lifespan_context
        import uvicorn
        # Bind ourselves and hand the socket over: the actual port is known
        # synchronously (0 included), and a collision raises here — where the
        # catch below can report it — instead of inside uvicorn, which logs
        # and returns silently on bind failure.
        config = uvicorn.Config(
            app, host='127.0.0.1', port=MCP_PORT, log_level='warning')
        # load() resolves `http`, and startup builds its protocol off the
        # result, so the refresh is derived in between.
        config.load()
        config.http_protocol_class = _refreshing_protocol_class(
            config.http_protocol_class)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if WIN32:
            sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', MCP_PORT))
        bound_port = sock.getsockname()[1]
        _bound.set()
        print(f'[MCP] streamable-http on 127.0.0.1:{bound_port}', flush=True)
        # The middleware reads the server from `scope['app']`, so publish
        # it there before `run` hands the socket over.
        app.state.server = server = _idle_server_class()(config)
        server.run(sockets=[sock])
    except Exception as e:
        startup_error = f'[MCP] serve crashed: {log_safe(e)}'
        print(startup_error, file=sys.stderr, flush=True)


def start_in_thread(local_url: str | None = None) -> threading.Thread:
    if _start_state['started']:
        raise RuntimeError(
            'start_in_thread called more than once for this module')
    _start_state['started'] = True
    bridge.rebind(local_url)
    t = threading.Thread(target=_serve, daemon=True, name='mcp-server')
    t.start()
    return t


if __name__ == '__main__':
    start_in_thread().join()
