"""Loading the MCP server with its bridge replaced by a recording probe.

Not a suite itself — run_tests.py only loads `test_*.py`.

`daedalus_mcp.server` binds its transport at import, so a case that wants to
drive a tool against a bridge it controls has to load the module with
`BridgeSession` and `MCPServer` already patched. That patch, the registry it
installs and the probe that replaces the bridge are one inseparable unit, so
they live together here: a second copy of the probe is a second bridge, and
two bridges answering the same queue is the exact class of defect this
relocation exists to remove.
"""
import importlib
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _daedalus_env  # noqa: E402
import _util  # noqa: E402

INTERACTIONS = []


class ToolRegistry:
    def __init__(self, *_args, **_kwargs):
        self.registered = {}

    def tool(self):
        def decorate(fn):
            self.registered[fn.__name__] = fn
            return fn

        return decorate


class HTTPResponseProbe:
    def __init__(self, status, body):
        self.status_code = status
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class HTTPClientProbe:
    def __init__(self, bridge):
        self.bridge = bridge

    async def get(self, path, **kwargs):
        self.bridge.record(
            'http_client.get', path=path, params=kwargs.get('params', {}),
            headers=kwargs.get('headers', {}))
        status, body = self.bridge.http_bodies.get(path, (200, {'done': []}))
        return HTTPResponseProbe(status, body)


class BridgeProbe:
    def __init__(self, marker):
        self.marker = marker
        self.calls = []
        self.transport = object()
        # A case sets these to drive a branch the default bodies cannot reach.
        self.get_bodies = {'/upload': []}
        self.http_bodies = {'/segment-job': (200, {'sig': self.marker})}
        self.ext_bodies = {}
        self.poll_body = None

    def record(self, surface, **details):
        self.calls.append((surface, details))
        INTERACTIONS.append((self.marker, surface, details))

    def http_client(self):
        self.record('http_client')
        return HTTPClientProbe(self)

    def auth(self):
        self.record('auth')
        return {'Authorization': self.marker}

    def checked_timeout(self, timeout):
        self.record('checked_timeout', timeout=timeout)

    async def get(self, path, **params):
        self.record('get', path=path, params=params)
        if path in self.get_bodies:
            return self.get_bodies[path]
        if path == '/tabs':
            return [{'title': self.marker}]
        return {'result': self.marker}

    async def get_raw(self, endpoint, **params):
        self.record('get_raw', endpoint=endpoint, params=params)
        return b'image'

    async def post(self, path, body):
        self.record('post', path=path, body=body)
        return {'bridge': self.marker}

    async def delete(self, path, body):
        self.record('delete', path=path, body=body)
        return {'bridge': self.marker}

    async def ext_cmd(self, *args, **kwargs):
        self.record('ext_cmd', args=args, kwargs=kwargs)
        if len(args) > 1 and args[1] == 'screenshot':
            return self.ext_bodies.get('screenshot', {
                'path': f'{self.marker}/shot.png',
                'size': len(self.marker),
            })
        return {'bridge': self.marker}

    async def put(self, path, payload):
        self.record('put', path=path, payload=payload)
        return {'did': self.marker}

    async def poll_result(self, *args, **kwargs):
        self.record('poll_result', args=args, kwargs=kwargs)
        if self.poll_body is not None:
            return self.poll_body
        return {'result': self.marker, 'error': None, 'world': self.marker}


def _load_composition(marker):
    """The MCP server loaded with the bridge behind it replaced by a probe."""
    mcpserver = importlib.import_module('mcp.server.mcpserver')
    mcp_transport = importlib.import_module('daedalus_mcp.transport')

    def bridge_session(*_args, **_kwargs):
        return BridgeProbe(marker)

    with mock.patch.object(mcpserver, 'MCPServer', ToolRegistry), \
            mock.patch.object(
                mcp_transport, 'BridgeSession', bridge_session):
        with _daedalus_env.isolated({}):
            return _util.load(_util.ROOT / 'daedalus_mcp' / 'server.py',
                              f'mcp_server_tools_{marker}')
