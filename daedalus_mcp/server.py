"""Daedalus MCP server — exposes the extension command surface as MCP tools.

Runs in-process alongside server.py as a daemon thread on 127.0.0.1:8086 by
default (override with DAEDALUS_MCP_PORT), fronted by a reverse proxy at /mcp.
Tool handlers reach the bridge over HTTP rather than sharing its state, which
is the same indirection the CLI uses.

The Bearer token is compared with the bridge token resolved by the CLI's
existing configuration path before it enters the _token ContextVar and is
forwarded to the local bridge. Missing configuration fails closed.
"""
import contextlib
import os, socket, sys, threading
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
# The in-process server passes the bridge's actual bound URL to start_in_thread,
# which matters when DAEDALUS_PORT=0. DAEDALUS_LOCAL_URL remains the explicit
# override for a standalone MCP deployment fronting a bridge that runs elsewhere.
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


def _serve():
    global bound_port, startup_error
    try:
        app = mcp.streamable_http_app(
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=ALLOWED_HOSTS,
            ),
        )
        app.add_middleware(
            auth.BearerAuth,
            max_body_size=MAX_BODY_SIZE,
        )

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
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', MCP_PORT))
        bound_port = sock.getsockname()[1]
        _bound.set()
        print(f'[MCP] streamable-http on 127.0.0.1:{bound_port}', flush=True)
        uvicorn.Server(config).run(sockets=[sock])
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
