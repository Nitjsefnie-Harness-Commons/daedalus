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
    state_values = tuple(request.scope.get('state', {}).values())
    request_values = tuple(request.__dict__.values())
    pending = list(state_values + request_values)

    while pending:
        value = pending.pop()
        if isinstance(value, (bytes, bytearray, memoryview)):
            try:
                if payload in bytes(value):
                    return True
            except ValueError:
                # Released views expose no byte content to inspect.
                pass
            continue
        if not isinstance(value, (list, tuple, set, dict)):
            continue
        marker = id(value)
        if marker in seen:
            continue
        seen.add(marker)
        if isinstance(value, dict):
            pending.extend(value)
            pending.extend(value.values())
        else:
            pending.extend(value)
    return False


def test_payload_search_does_not_create_request_state(tmp):
    del tmp
    _need_deps()
    from starlette.requests import Request

    request = Request({'type': 'http'})

    assert not _request_contains_payload(request, REFUSED_PAYLOAD)
    assert 'state' not in request.scope


def test_payload_search_has_no_recursion_limit(tmp):
    del tmp
    _need_deps()
    from starlette.requests import Request

    request = Request({'type': 'http'})
    nested = REFUSED_PAYLOAD
    for _unused in range(1500):
        nested = [nested]
    request.state.nested = nested

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


def test_payload_search_ignores_released_memoryviews(tmp):
    del tmp
    _need_deps()
    from starlette.requests import Request

    request = Request({'type': 'http'})
    view = memoryview(REFUSED_PAYLOAD)
    view.release()
    request.state.view = view

    assert not _request_contains_payload(request, REFUSED_PAYLOAD)


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

    class CapturingBearerAuth(mod.mcp_auth.BearerAuth):
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
    middleware = CapturingBearerAuth(
        accepted, token_var=mod._token, max_body_size=mod.MAX_BODY_SIZE)
    asyncio.run(middleware(scope, receive, send))

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
    del tmp
    _need_deps()
    mod = _load_mcp()

    assert mod._token is mod.mcp_request_guard.request_token


def test_disconnect_during_drain_preserves_refusal(tmp):
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
        middleware = mod.mcp_auth.BearerAuth(
            accepted, token_var=mod._token,
            max_body_size=mod.MAX_BODY_SIZE)
        asyncio.run(middleware(scope, receive, send))
    except ClientDisconnect as exc:
        raise AssertionError(
            f'{type(exc).__name__} escaped refusal drain') from exc

    assert inbound == []
    assert outbound[0]['type'] == 'http.response.start'
    assert outbound[0]['status'] == 401
    assert outbound[-1]['type'] == 'http.response.body'
    assert not outbound[-1].get('more_body', False)


def test_every_early_refusal_discards_a_bounded_body_after_deciding(tmp):
    del tmp
    _need_deps()
    mod = _load_mcp(max_body_size=4096)
    assert mod.MAX_BODY_SIZE + 1 < mod.mcp_request_guard.REFUSED_BODY_DRAIN
    valid_auth = (b'authorization', f'Bearer {TOK}'.encode())
    cases = (
        ('duplicate authorization', 'POST',
         (valid_auth, valid_auth)),
        ('duplicate session', 'POST',
         ((b'mcp-session-id', b'a'), (b'mcp-session-id', b'b'))),
        ('duplicate host', 'POST',
         ((b'host', b'a.example.com'), (b'host', b'b.example.com'))),
        ('duplicate origin', 'POST',
         ((b'origin', b'https://a.example.com'),
          (b'origin', b'https://b.example.com'))),
        ('missing bearer', 'POST', ()),
        ('bad bearer', 'POST',
         ((b'authorization', b'Bearer a/b'),)),
        ('unauthorized bearer', 'POST',
         ((b'authorization', b'Bearer othermcptok'),)),
        ('noninteger content length', 'POST',
         (valid_auth, (b'content-length', b'bad'))),
        ('negative content length', 'POST',
         (valid_auth, (b'content-length', b'-1'))),
        ('oversized content length', 'POST',
         (valid_auth,
          (b'content-length', str(mod.MAX_BODY_SIZE + 1).encode()))),
        ('delete missing bearer', 'DELETE', ()),
    )

    async def exercise(method, headers):
        events = []
        chunk = REFUSED_PAYLOAD + bytes(10000 - len(REFUSED_PAYLOAD))
        chunks = [chunk for _unused in range(8)]
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

        class CapturingBearerAuth(mod.mcp_auth.BearerAuth):
            async def dispatch(self, request, call_next):
                request_seen.append(request)
                return await super().dispatch(request, call_next)

        scope = {
            'type': 'http',
            'asgi': {'version': '3.0'},
            'http_version': '1.1',
            'method': method,
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
            middleware = CapturingBearerAuth(
                accepted, token_var=mod._token,
                max_body_size=mod.MAX_BODY_SIZE)
            await middleware(scope, receive, send)
        finally:
            mod.mcp_request_guard.JSONResponse = real_response
        return events, received, request_seen[0]

    for name, method, headers in cases:
        events, received, request = asyncio.run(exercise(method, headers))
        assert len(received) == 7, (name, events, len(received))
        drained_before_last = sum(len(chunk) for chunk in received[:-1])
        drained = sum(len(chunk) for chunk in received)
        assert drained_before_last < mod.mcp_request_guard.REFUSED_BODY_DRAIN
        assert drained >= mod.mcp_request_guard.REFUSED_BODY_DRAIN
        assert events[0] == 'decision', (name, events)
        assert events[-1] == 'response-complete', (name, events)
        assert all(event == 'receive' for event in events[1:-1]), (
            name, events)
        assert not hasattr(request, '_body'), name
        assert not _request_contains_payload(
            request, REFUSED_PAYLOAD), name


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
