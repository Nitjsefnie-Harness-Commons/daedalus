#!/usr/bin/env python3
"""Authentication and request-carrier coverage for the MCP middleware."""
import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import test_mcp_server  # noqa: E402


def _auth_module():
    test_mcp_server._need_deps()
    return _util.load(
        _util.ROOT / 'daedalus_mcp' / 'auth.py',
        'mcp_auth_under_test_' + str(time.time_ns()))


def _start_listener(base, max_body_size):
    return test_mcp_server._start_mcp_in_process(
        base, max_body_size=max_body_size)


def _initialize_body(padding=0):
    body = {
        'jsonrpc': '2.0',
        'id': 'initialize',
        'method': 'initialize',
        'params': {
            'protocolVersion': '2024-11-05',
            'capabilities': {},
            'clientInfo': {'name': 'auth-isolation', 'version': '0'},
        },
    }
    return json.dumps(body).encode() + b' ' * padding


def _drive_undeclared_post(body_chunks, max_body_size):
    """Drive BearerAuth with a POST that declares no Content-Length.

    Returns a dict of the response status and payload, the body the inner
    app assembled (inner), and the body bytes the middleware pulled from
    the ASGI receive channel (pulled).
    """
    auth = _auth_module()
    pulled = [0]
    inner = []
    messages = [
        {'type': 'http.request', 'body': chunk,
         'more_body': index < len(body_chunks) - 1}
        for index, chunk in enumerate(body_chunks)]
    outbound = []

    async def receive():
        message = messages.pop(0)
        pulled[0] += len(message['body'])
        return message

    async def send(message):
        outbound.append(message)

    async def inner_app(_scope, read_body, send_response):
        seen = []
        while True:
            message = await read_body()
            if message['type'] != 'http.request':
                break
            seen.append(message.get('body', b''))
            if not message.get('more_body', False):
                break
        inner.append(b''.join(seen))
        await send_response({
            'type': 'http.response.start',
            'status': 204,
            'headers': [],
        })
        await send_response({
            'type': 'http.response.body',
            'body': b'',
            'more_body': False,
        })

    scope = {
        'type': 'http',
        'asgi': {'version': '3.0'},
        'http_version': '1.1',
        'method': 'POST',
        'scheme': 'http',
        'path': '/mcp',
        'raw_path': b'/mcp',
        'query_string': b'',
        'headers': [
            (b'authorization',
             f'Bearer {test_mcp_server.TOK}'.encode()),
        ],
        'client': ('127.0.0.1', 12345),
        'server': ('127.0.0.1', 8086),
    }
    middleware = auth.BearerAuth(inner_app, max_body_size=max_body_size)
    asyncio.run(middleware(scope, receive, send))

    start = next(
        message for message in outbound
        if message['type'] == 'http.response.start')
    payload = b''.join(
        message.get('body', b'') for message in outbound
        if message['type'] == 'http.response.body')
    return {
        'status': start['status'],
        'payload': payload,
        'inner': inner,
        'pulled': pulled[0],
    }


def test_oversized_chunked_body_is_refused_inside_the_read(tmp):
    """An undeclared body is bounded while received, not after buffering.

    The pulled-bytes bound is the assertion that separates this from the
    old behavior, which read every chunk before comparing the total against
    the limit.
    """
    del tmp
    result = _drive_undeclared_post([b'x'] * 64, max_body_size=16)
    payload = json.loads(result['payload'])
    assert result['status'] == 413, (result['status'], payload)
    assert payload == {'error': 'request body too large'}, payload
    assert result['pulled'] <= 17, result['pulled']
    assert result['inner'] == [], result['inner']


def test_chunked_body_at_the_limit_reaches_the_inner_app(tmp):
    del tmp
    body = b'0123456789abcdef'
    result = _drive_undeclared_post(
        [body[0:5], body[5:10], body[10:16]], max_body_size=16)
    assert result['status'] == 204, result['status']
    assert result['inner'] == [body], result['inner']


def test_chunked_body_one_past_the_limit_is_refused(tmp):
    del tmp
    result = _drive_undeclared_post(
        [b'a' * 8, b'b' * 9], max_body_size=16)
    payload = json.loads(result['payload'])
    assert result['status'] == 413, (result['status'], payload)
    assert payload == {'error': 'request body too large'}, payload
    assert result['inner'] == [], result['inner']


def test_chunked_duplicate_job_carrier_still_answers_duplicate_job(tmp):
    del tmp
    raw = (
        '{"jsonrpc": "2.0", "id": 7, "method": "segment_job", '
        '"params": {"name": "segment_job", "arguments": '
        '{"job": "first", "job": "second"}}}').encode()
    result = _drive_undeclared_post(
        [raw[0:40], raw[40:80], raw[80:]], max_body_size=256)
    payload = json.loads(result['payload'])
    assert result['status'] == 400, (result['status'], payload)
    assert payload == {'error': 'duplicate job'}, payload
    assert result['inner'] == [], result['inner']


def test_top_level_json_scalar_has_no_job_carrier(tmp):
    del tmp
    auth = _auth_module()
    try:
        result = auth.ambiguous_json_carrier(b'0')
    except Exception as failure:  # noqa: BLE001
        result = f'raised {type(failure).__name__}: {failure}'
    assert result is None, result


def test_malformed_json_is_not_a_duplicate_carrier(tmp):
    """Malformed JSON remains the MCP transport's validation concern."""
    del tmp
    auth = _auth_module()
    try:
        result = auth.ambiguous_json_carrier(b'{')
    except Exception as failure:  # noqa: BLE001
        result = f'raised {type(failure).__name__}: {failure}'
    assert result is None, result


def test_carrier_json_object_equality_reflects_duplicate_keys(tmp):
    """Two bodies collapsing to the same dict must stay distinguishable when
    only one of them repeated a key — that repeat is otherwise invisible."""
    del tmp
    auth = _auth_module()
    single = json.loads(
        '{"job": "b"}', object_pairs_hook=auth.CarrierJSONObject)
    duplicated = json.loads(
        '{"job": "a", "job": "b"}', object_pairs_hook=auth.CarrierJSONObject)
    assert dict(single) == dict(duplicated), (dict(single), dict(duplicated))
    assert single != duplicated
    assert duplicated == {'job': 'b'}, dict(duplicated)


def test_carrier_json_object_inequality_against_a_non_dict_stays_true(tmp):
    """`__eq__` against a non-dict falls through to `dict.__eq__`, which
    returns `NotImplemented` rather than `False` -- and `NotImplemented` is
    truthy, so a `__ne__` that just negated it would report these as equal."""
    del tmp
    auth = _auth_module()
    carrier = json.loads(
        '{"job": "b"}', object_pairs_hook=auth.CarrierJSONObject)
    for other in (5, None, 'job', [1]):
        assert carrier != other, (carrier, other)
        assert not (carrier == other), (carrier, other)


def test_live_listeners_keep_auth_state_and_body_limits_separate(tmp):
    test_mcp_server._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')

    first_tmp = Path(tmp) / 'first'
    second_tmp = Path(tmp) / 'second'
    with _util.bridge(first_tmp, env=test_mcp_server.BRIDGE_ENV) as (
            first_base, _first_docroot):
        status, body = _util.post_json(first_base + '/sync-tabs', {
            'token': test_mcp_server.TOK,
            'tabs': [{'tabId': 'first', 'url': 'https://first.example.com',
                      'title': 'first'}],
        })
        assert status == 200, (status, body)
        with _util.bridge(second_tmp, env=test_mcp_server.BRIDGE_ENV) as (
                second_base, _second_docroot):
            status, body = _util.post_json(second_base + '/sync-tabs', {
                'token': test_mcp_server.TOK,
                'tabs': [{'tabId': 'second',
                          'url': 'https://second.example.com',
                          'title': 'second'}],
            })
            assert status == 200, (status, body)

            _first_mod, first_port = _start_listener(first_base, 256)
            _second_mod, second_port = _start_listener(second_base, 100000)

            first_session = test_mcp_server._open_mcp_session(first_port)
            reply = test_mcp_server._call_mcp_tool(
                first_port, first_session, 'first-tabs', 'list_tabs')
            text = test_mcp_server._mcp_tool_text(reply)
            assert reply.get('result', {}).get('isError') is not True, reply
            assert 'first' in text and 'second' not in text, text

            oversized = _initialize_body(padding=2048)
            status, _session_id, raw = test_mcp_server._mcp_request(
                first_port, oversized)
            assert status == 413, (status, raw)
            assert b'request body too large' in raw, raw

            status, session_id, raw = test_mcp_server._mcp_request(
                second_port, oversized)
            assert status == 200 and session_id, (status, session_id, raw)


def test_sdk_body_limit_follows_configured_value(tmp):
    test_mcp_server._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')

    configured_limit = 6 * 1024 * 1024
    with _util.bridge(tmp, env=test_mcp_server.BRIDGE_ENV) as (
            base, _docroot):
        mod, _port = _start_listener(base, configured_limit)
        assert mod.MAX_BODY_SIZE == configured_limit, mod.MAX_BODY_SIZE
        actual = mod.mcp.session_manager.max_request_body_size
        assert actual == mod.MAX_BODY_SIZE, (actual, configured_limit)


def test_live_body_limit_accepts_above_sdk_default(tmp):
    test_mcp_server._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')

    configured_limit = 6 * 1024 * 1024
    with _util.bridge(tmp, env=test_mcp_server.BRIDGE_ENV) as (
            base, _docroot):
        _mod, port = _start_listener(base, configured_limit)
        accepted = _initialize_body(padding=5 * 1024 * 1024)
        assert 4 * 1024 * 1024 < len(accepted) < configured_limit
        status, session_id, raw = test_mcp_server._mcp_request(
            port, accepted)
        assert b'Request body too large' not in raw, (status, raw)
        assert status == 200 and session_id, (status, session_id, raw)

        refused = _initialize_body(padding=configured_limit)
        assert len(refused) > configured_limit
        status, _session_id, raw = test_mcp_server._mcp_request(
            port, refused)
        assert status == 413, (status, raw)
        assert json.loads(raw) == {'error': 'request body too large'}, raw


def test_live_body_limit_distinguishes_two_configured_values(tmp):
    test_mcp_server._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')

    lower_limit = 6 * 1024 * 1024
    higher_limit = 10 * 1024 * 1024
    body = _initialize_body(padding=9 * 1024 * 1024)
    assert lower_limit < len(body) < higher_limit
    with _util.bridge(tmp, env=test_mcp_server.BRIDGE_ENV) as (
            base, _docroot):
        _lower_mod, lower_port = _start_listener(base, lower_limit)
        _higher_mod, higher_port = _start_listener(base, higher_limit)
        status, _session_id, raw = test_mcp_server._mcp_request(
            lower_port, body)
        assert status == 413, (status, raw)
        assert json.loads(raw) == {'error': 'request body too large'}, raw

        status, session_id, raw = test_mcp_server._mcp_request(
            higher_port, body)
        assert b'Request body too large' not in raw, (status, raw)
        assert status == 200 and session_id, (status, session_id, raw)


def test_zero_body_limit_starts_and_refuses_nonempty_body(tmp):
    test_mcp_server._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')

    with _util.bridge(tmp, env=test_mcp_server.BRIDGE_ENV) as (
            base, _docroot):
        mod, port = _start_listener(base, 0)
        assert mod.bound_port == port and port > 0, (mod.bound_port, port)
        assert not mod.startup_error, mod.startup_error
        status, _session_id, raw = test_mcp_server._mcp_request(port, b'x')
        assert status == 413, (status, raw)
        assert json.loads(raw) == {'error': 'request body too large'}, raw


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='mcpauth_'))
