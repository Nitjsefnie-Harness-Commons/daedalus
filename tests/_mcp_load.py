"""Shell settings must not override fixture-selected bridge URLs."""
import http.client
import json
import time

import _daedalus_env
import _util

TOK = 'mcptok'
BRIDGE_ENV = {'DAEDALUS_TOKEN': TOK, 'TOKEN': ''}


def _load_mcp(base_url, mcp_port=None, max_body_size=None):
    applied = dict(BRIDGE_ENV, DAEDALUS_LOCAL_URL=base_url)
    if mcp_port is not None:
        applied['DAEDALUS_MCP_PORT'] = str(mcp_port)
    if max_body_size is not None:
        applied['DAEDALUS_MCP_MAX_BODY_SIZE'] = str(max_body_size)
    with _daedalus_env.isolated(applied):
        return _util.load(_util.ROOT / 'daedalus_mcp' / 'server.py',
                          'mcp_server_under_test_' + str(time.time_ns()))


def _start_in_thread(mod, local_url=None):
    # Rebinding reads the environment again after import isolation has ended.
    with _daedalus_env.isolated({}):
        return mod.start_in_thread(local_url)


def _wait_for_mcp(port, deadline=20):
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
    return _load_mcp(base_url, mcp_port=port, max_body_size=max_body_size)


def _start_mcp_in_process(base, max_body_size=None):
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
