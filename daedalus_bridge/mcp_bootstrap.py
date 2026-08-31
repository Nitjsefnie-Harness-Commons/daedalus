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


def _bootstrap(local_url):
    try:
        from daedalus_mcp import server as mcp_server
        mcp_server.start_in_thread(local_url)
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
        print('[Daedalus] MCP bootstrap failed, so /mcp is not served - '
              'install its dependencies with: pip install ".[mcp]" - '
              f'{log_safe(e)}', flush=True)


def start(bridge_port):
    """Start the front end against the bridge's bound port, and don't wait."""
    threading.Thread(
        target=_bootstrap, args=(f'http://127.0.0.1:{bridge_port}',),
        name='mcp-bootstrap', daemon=True).start()
