"""Deterministic ASGI contract for early MCP request refusals."""
import asyncio
import importlib.util
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


DEPS = all(importlib.util.find_spec(name) is not None
           for name in ('httpx', 'mcp', 'starlette'))
TOK = 'mcptok'
REFUSED_PAYLOAD = bytes(range(256)) * 32
os.environ['TOKEN'] = ''
os.environ['DAEDALUS_TOKEN'] = TOK


def _need_deps():
    if not DEPS:
        _util.skip(
            'mcp_server dependencies (httpx/mcp/starlette) not installed')


def _request_contains_payload(request, payload):
    seen = set()

    def contains(value):
        if isinstance(value, (bytes, bytearray, memoryview)):
            return payload in bytes(value)
        if not isinstance(value, (list, tuple, set, dict)):
            return False
        marker = id(value)
        if marker in seen:
            return False
        seen.add(marker)
        if isinstance(value, dict):
            return any(
                contains(key) or contains(item)
                for key, item in value.items())
        return any(contains(item) for item in value)

    state_values = tuple(request.scope.setdefault('state', {}).values())
    request_values = tuple(request.__dict__.values())
    return any(contains(value) for value in state_values + request_values)


def test_payload_search_reaches_deep_state_containers(tmp):
    del tmp
    _need_deps()
    from starlette.requests import Request

    request = Request({'type': 'http'})
    request.state.nested = [[[[[REFUSED_PAYLOAD]]]]]

    assert _request_contains_payload(request, REFUSED_PAYLOAD)


def test_payload_search_visits_dictionary_keys(tmp):
    del tmp
    _need_deps()
    from starlette.requests import Request

    request = Request({'type': 'http'})
    request.state.mapping = {REFUSED_PAYLOAD: None}

    assert _request_contains_payload(request, REFUSED_PAYLOAD)


def test_payload_search_reads_memoryviews(tmp):
    del tmp
    _need_deps()
    from starlette.requests import Request

    request = Request({'type': 'http'})
    request.state.view = memoryview(REFUSED_PAYLOAD)

    assert _request_contains_payload(request, REFUSED_PAYLOAD)


def test_unrelated_request_bytes_do_not_look_like_refused_body(tmp):
    del tmp
    _need_deps()
    mod = _load_mcp(max_body_size=4096)
    captured = []
    outbound = []
    inbound = [{
        'type': 'http.request',
        'body': REFUSED_PAYLOAD,
        'more_body': False,
    }]

    async def receive():
        return inbound.pop(0)

    async def send(message):
        outbound.append(message)

    async def accepted(_scope, _receive, _send):
        raise AssertionError('an early refusal reached the MCP app')

    class CapturingBearerAuth(mod._BearerAuth):
        async def dispatch(self, request, call_next):
            request.state.trace_metadata = b'ordinary-state-' * 40
            captured.append(request)
            return await super().dispatch(request, call_next)

    scope = {
        'type': 'http',
        'asgi': {'version': '3.0'},
        'http_version': '1.1',
        'method': 'POST',
        'scheme': 'http',
        'path': '/mcp',
        'raw_path': b'/mcp',
        'query_string': b'',
        'headers': [(b'x-metadata', b'ordinary-header-' * 128)],
        'client': ('127.0.0.1', 12345),
        'server': ('127.0.0.1', 8086),
    }
    asyncio.run(CapturingBearerAuth(accepted)(scope, receive, send))

    assert outbound[0]['status'] == 401
    assert not _request_contains_payload(captured[0], REFUSED_PAYLOAD)


def _load_mcp(max_body_size=None):
    setting = 'DAEDALUS_MCP_MAX_BODY_SIZE'
    previous = os.environ.get(setting)
    if max_body_size is not None:
        os.environ[setting] = str(max_body_size)
    try:
        return _util.load(
            _util.ROOT / 'mcp_server.py',
            'mcp_refusal_drain_' + str(time.time_ns()))
    finally:
        if previous is None:
            os.environ.pop(setting, None)
        else:
            os.environ[setting] = previous


def test_request_token_is_public_guard_state(tmp):
    """The guard publishes the same request token state MCP tools consume."""
    del tmp
    _need_deps()
    mod = _load_mcp()

    assert mod._token is mod.mcp_request_guard.request_token


def test_disconnect_during_drain_preserves_refusal(tmp):
    """A peer disconnect cannot replace an already-decided refusal."""
    del tmp
    _need_deps()
    from starlette.requests import ClientDisconnect

    mod = _load_mcp()
    inbound = [
        {'type': 'http.request', 'body': b'x', 'more_body': True},
        {'type': 'http.disconnect'},
    ]
    outbound = []

    async def receive():
        return inbound.pop(0)

    async def send(message):
        outbound.append(message)

    async def accepted(_scope, _receive, _send):
        raise AssertionError('a refused request reached the MCP app')

    scope = {
        'type': 'http',
        'asgi': {'version': '3.0'},
        'http_version': '1.1',
        'method': 'POST',
        'scheme': 'http',
        'path': '/mcp',
        'raw_path': b'/mcp',
        'query_string': b'',
        'headers': [],
        'client': ('127.0.0.1', 12345),
        'server': ('127.0.0.1', 8086),
    }
    try:
        asyncio.run(mod._BearerAuth(accepted)(scope, receive, send))
    except ClientDisconnect as exc:
        raise AssertionError(
            f'{type(exc).__name__} escaped refusal drain') from exc

    assert inbound == []
    assert outbound[0]['type'] == 'http.response.start'
    assert outbound[0]['status'] == 401
    assert outbound[-1]['type'] == 'http.response.body'
    assert not outbound[-1].get('more_body', False)


def test_every_early_refusal_discards_a_bounded_body_after_deciding(tmp):
    """Header-decided refusals drain without materializing the request body."""
    del tmp
    _need_deps()
    mod = _load_mcp(max_body_size=4096)
    assert mod.MAX_BODY_SIZE + 1 < mod.mcp_request_guard.REFUSED_BODY_DRAIN
    valid_auth = (b'authorization', f'Bearer {TOK}'.encode())
    cases = (
        ('duplicate authorization',
         (valid_auth, valid_auth)),
        ('duplicate session',
         ((b'mcp-session-id', b'a'), (b'mcp-session-id', b'b'))),
        ('duplicate host',
         ((b'host', b'a.example.com'), (b'host', b'b.example.com'))),
        ('duplicate origin',
         ((b'origin', b'https://a.example.com'),
          (b'origin', b'https://b.example.com'))),
        ('missing bearer', ()),
        ('bad bearer', ((b'authorization', b'Bearer a/b'),)),
        ('unauthorized bearer',
         ((b'authorization', b'Bearer othermcptok'),)),
        ('noninteger content length',
         (valid_auth, (b'content-length', b'bad'))),
        ('negative content length',
         (valid_auth, (b'content-length', b'-1'))),
        ('oversized content length',
         (valid_auth,
          (b'content-length', str(mod.MAX_BODY_SIZE + 1).encode()))),
    )

    async def exercise(headers):
        events = []
        chunks = [REFUSED_PAYLOAD for _unused in range(9)]
        received = []
        request_seen = []
        real_response = mod.mcp_request_guard.JSONResponse

        def deciding_response(*args, **kwargs):
            events.append('decision')
            return real_response(*args, **kwargs)

        async def receive():
            chunk = chunks.pop(0)
            received.append(chunk)
            events.append('receive')
            return {
                'type': 'http.request',
                'body': chunk,
                'more_body': bool(chunks),
            }

        async def send(message):
            if (message['type'] == 'http.response.body'
                    and not message.get('more_body', False)):
                events.append('response-complete')

        async def accepted(_scope, _receive, _send):
            raise AssertionError('an early refusal reached the MCP app')

        class CapturingBearerAuth(mod._BearerAuth):
            async def dispatch(self, request, call_next):
                request_seen.append(request)
                return await super().dispatch(request, call_next)

        scope = {
            'type': 'http',
            'asgi': {'version': '3.0'},
            'http_version': '1.1',
            'method': 'POST',
            'scheme': 'http',
            'path': '/mcp',
            'raw_path': b'/mcp',
            'query_string': b'',
            'headers': list(headers),
            'client': ('127.0.0.1', 12345),
            'server': ('127.0.0.1', 8086),
        }
        mod.mcp_request_guard.JSONResponse = deciding_response
        try:
            await CapturingBearerAuth(accepted)(scope, receive, send)
        finally:
            mod.mcp_request_guard.JSONResponse = real_response
        return events, received, chunks, request_seen[0]

    for name, headers in cases:
        events, received, remaining, request = asyncio.run(
            exercise(headers))
        assert len(received) == 8, (name, events, len(received))
        assert len(remaining) == 1, (name, len(remaining))
        assert events[0] == 'decision', (name, events)
        assert events[-1] == 'response-complete', (name, events)
        assert all(event == 'receive' for event in events[1:-1]), (
            name, events)
        assert not hasattr(request, '_body'), name
        assert not _request_contains_payload(
            request, REFUSED_PAYLOAD), name


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
