"""Bring the optional MCP front end up without holding back readiness.

Importing daedalus_mcp costs over a second — mcp, pydantic, httpx,
opentelemetry — and every caller waiting on the bridge's Listening line paid
it. That listener already binds on a thread of its own and announces itself
on its own [MCP] line, so readiness never meant the front end was up.

An import that never returns is what this costs: the main thread blocks on
the module lock this one holds, so the interpreter stops answering SIGINT.
"""
import os
import sys
import threading

from daedalus_bridge.log_safe import log_safe

# What startup reached, read by state(): the serve thread and the Event its
# module sets at bind, recorded once start_in_thread returns, or the import
# failure. state() computes the string on read so a crash is visible
# whenever it lands, with nothing watching for it.
_record = {'thread': None, 'bound': None, 'import_failed': False}


def state():
    """The front end's startup outcome, as the /health payload spells it.

    'starting' until the listener binds, 'up' from bind until the serve
    thread ends, 'down' once it has — under the bridge the caught crash's
    return is the only thing that ends that thread — or when the import
    failed outright. The state is all the payload carries: /health is
    unauthenticated, so the verbatim bind error stays on stderr.
    """
    if _record['import_failed']:
        return 'down'
    t = _record['thread']
    if t is None:
        return 'starting'
    if not t.is_alive():
        return 'down'
    if _record['bound'].is_set():
        return 'up'
    return 'starting'


def _bootstrap(local_url):
    try:
        from daedalus_mcp import server as mcp_server
        t = mcp_server.start_in_thread(local_url)
    except SystemExit as e:
        # How the front end's settings parser refuses a value, and on the
        # main thread it stopped the bridge naming the setting. A thread's
        # SystemExit is discarded instead, so the stop is made here.
        print(f'[Daedalus] {log_safe(e)}', file=sys.stderr, flush=True)
        os._exit(1)
    except Exception as e:
        # ASCII only, and it names the install: without the optional
        # dependencies the bridge otherwise starts normally and /mcp simply
        # is not there, which reads as a client problem rather than a
        # missing extra.
        _record['import_failed'] = True
        print('[Daedalus] MCP bootstrap failed, so /mcp is not served - '
              'install its dependencies with: pip install ".[mcp]" - '
              f'{log_safe(e)}', flush=True)
        return
    # bound before thread, so a reader that sees the thread always has the
    # event the state reads.
    _record['bound'] = mcp_server._bound
    _record['thread'] = t


def start(bridge_port):
    """Start the front end against the bridge's bound port, and don't wait."""
    threading.Thread(
        target=_bootstrap, args=(f'http://127.0.0.1:{bridge_port}',),
        name='mcp-bootstrap', daemon=True).start()
