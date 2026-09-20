"""Isolated MCP module loading and in-process startup for test suites."""
import contextlib
import http.client
import json
import os
import time

import _util
from _util import ROOT

TOK = 'mcptok'
BRIDGE_ENV = {'DAEDALUS_TOKEN': TOK, 'TOKEN': ''}


@contextlib.contextmanager
def _shield_environment(**seeds):
    saved = {key: value for key, value in os.environ.items()
             if key.startswith('DAEDALUS_')}
    for key in saved:
        del os.environ[key]
    try:
        os.environ.update(seeds)
        yield
    finally:
        for key in tuple(os.environ):
            if key.startswith('DAEDALUS_'):
                del os.environ[key]
        os.environ.update(saved)


def _load_mcp(base_url, mcp_port=None, max_body_size=None):
    """Load daedalus_mcp/server.py seeing only the caller's own settings."""
    applied = dict(BRIDGE_ENV, DAEDALUS_LOCAL_URL=base_url)
    if mcp_port is not None:
        applied['DAEDALUS_MCP_PORT'] = str(mcp_port)
    if max_body_size is not None:
        applied['DAEDALUS_MCP_MAX_BODY_SIZE'] = str(max_body_size)
    with _shield_environment(**applied):
        return _util.load(ROOT / 'daedalus_mcp' / 'server.py',
                          'mcp_server_under_test_' + str(time.time_ns()))


def _start_in_thread(mod, local_url=None):
    # Rebinding reads the environment again after the load shield has ended.
    with _shield_environment():
        return mod.start_in_thread(local_url)


def _wait_for_mcp(port, deadline=20):
    """Require an MCP authentication refusal, not just a TCP listener."""
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


def _load_mcp_at_port(base_url, port, max_body_size=None):
    """Load the MCP front end with one explicit listener port."""
    return _load_mcp(base_url, mcp_port=port, max_body_size=max_body_size)


def _start_mcp_in_process(base, max_body_size=None):
    """Return (mod, bound port), surfacing startup crashes without retries."""
    mod = _load_mcp_at_port(base, 0, max_body_size=max_body_size)
    _start_in_thread(mod)
    deadline = time.time() + 10
    while time.time() < deadline:
        if mod._bound.wait(timeout=0.05):
            port = mod.bound_port
            _wait_for_mcp(port)
            return mod, port
        if mod.startup_error:
            raise AssertionError(mod.startup_error)
    raise AssertionError('MCP listener did not announce its port in 10s')
