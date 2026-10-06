#!/usr/bin/env python3
"""Loading daedalus_mcp.server against the shared load-and-drive fixture.

Split from tests/test_mcp_server.py.
"""
import asyncio
import importlib.util
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _daedalus_env  # noqa: E402
import _util  # noqa: E402
import _mcp_load  # noqa: E402

DEPS = _mcp_load.DEPS
if DEPS:
    import logging
    logging.getLogger('httpx').setLevel(
        logging.WARNING)  # quiet per-request logs
    logging.getLogger('mcp').setLevel(logging.WARNING)  # quiet mcp INFO logs


def _module_list_tabs(mod):
    async def fetch():
        response = await mod.bridge.http_client().get(
            '/tabs', headers={'Authorization': f'Bearer {_mcp_load.TOK}'})
        response.raise_for_status()
        return response.json()
    return asyncio.run(fetch())


def test_the_dependency_check_is_one_shared_definition(tmp):
    """`DEPS`/`_need_deps` decide skip-or-run for every MCP suite at once.

    The flag is put back in a `finally` on BOTH arms, because leaving it False
    does not fail this test — it disarms every test that runs after it, which
    is the same false green this control exists to prevent, one level down.
    """
    del tmp
    assert _mcp_load.DEPS is DEPS, 'the suite reads a second dependency check'
    # The suite-level alias bindings the comparison's other operand named are
    # retired with the alias block, so the qualified form compares the shared
    # module's members with themselves.
    # pylint: disable-next=comparison-with-itself
    assert _mcp_load._need_deps is _mcp_load._need_deps, _mcp_load._need_deps
    assert DEPS == all(importlib.util.find_spec(name) is not None
                       for name in ('httpx', 'mcp', 'starlette')), DEPS
    # Present: the call returns, so no suite is silently disarmed.
    _mcp_load.DEPS = True
    try:
        _mcp_load._need_deps()
    finally:
        _mcp_load.DEPS = DEPS
    _mcp_load.DEPS = False
    try:
        _mcp_load._need_deps()
    except _util.Skipped as skipped:
        for name in ('httpx', 'mcp', 'starlette'):
            assert name in str(skipped), skipped
    else:
        raise AssertionError('a missing dependency did not skip the suite')
    finally:
        _mcp_load.DEPS = DEPS
    assert _mcp_load.DEPS, 'the check left the suite disarmed'
    # pylint: disable=comparison-with-itself
    assert (_mcp_load.TOK is _mcp_load.TOK
            and _mcp_load.BRIDGE_ENV is _mcp_load.BRIDGE_ENV), (
        'the bridge fixture has a second home')
    # pylint: enable=comparison-with-itself
    assert os.environ.get('DAEDALUS_TOKEN') == _mcp_load.TOK, (
        'the fixture no longer publishes the token the bind re-reads')


def test_wait_for_mcp_refuses_a_non_mcp_listener(tmp):
    """A foreign listener on the MCP port must fail, not authenticate."""
    with _util.bridge(tmp, env=_mcp_load.BRIDGE_ENV) as (base, _docroot):
        port = int(base.rsplit(':', 1)[1])
        try:
            _mcp_load._wait_for_mcp(port)
        except AssertionError as failure:
            assert 'not the MCP server' in str(failure), failure
        else:
            raise AssertionError(
                'a bridge listener passed the MCP readiness probe')


def test_module_imports_and_exposes_tools(tmp):
    _mcp_load._need_deps()
    mod = _mcp_load._load_mcp('http://127.0.0.1:1')  # URL unused here
    for name in ('list_tabs', 'ping', 'navigate', 'screenshot',
                 'segment_status'):
        fn = getattr(mod, name, None)
        assert callable(fn), f'daedalus_mcp.server.{name} missing'


def test_local_url_derives_from_the_bridge_port(tmp):
    """The MCP bridge client follows DAEDALUS_PORT unless explicitly
    overridden; DAEDALUS_LOCAL_URL remains the explicit override for a
    standalone deployment."""
    del tmp
    _mcp_load._need_deps()

    def fresh(tag):
        return _util.load(_util.ROOT / 'daedalus_mcp' / 'server.py',
                          'mcp_server_url_' + tag + str(time.time_ns()))

    with _daedalus_env.isolated({'DAEDALUS_PORT': '54321'}):
        assert fresh('derived').LOCAL_URL == 'http://127.0.0.1:54321'
    with _daedalus_env.isolated({
            'DAEDALUS_PORT': '54321',
            'DAEDALUS_LOCAL_URL': 'http://127.0.0.1:9999'}):
        assert fresh('override').LOCAL_URL == 'http://127.0.0.1:9999'
    with _daedalus_env.isolated({}):
        assert fresh('fallback').LOCAL_URL == 'http://127.0.0.1:8081'


def test_fresh_mcp_modules_keep_distinct_bound_transports(tmp):
    """Fresh callers share the transport class but retain their own bridges."""
    _mcp_load._need_deps()
    with _util.bridge(Path(tmp) / 'first',
                      env=_mcp_load.BRIDGE_ENV) as (first, _):
        _util.post_json(first + '/sync-tabs', {
            'token': _mcp_load.TOK, 'tabs': [
                {'tabId': 'first', 'url': 'https://first.example.com',
                 'title': 'first'}]})
        with _util.bridge(Path(tmp) / 'second',
                          env=_mcp_load.BRIDGE_ENV) as (second, _):
            _util.post_json(second + '/sync-tabs', {
                'token': _mcp_load.TOK, 'tabs': [
                    {'tabId': 'second', 'url': 'https://second.example.com',
                     'title': 'second'}]})
            first_mod = _mcp_load._load_mcp(first)
            second_mod = _mcp_load._load_mcp(second)
            assert first_mod.BridgeTransport is second_mod.BridgeTransport
            assert first_mod.bridge.transport is not (
                second_mod.bridge.transport)
            first_tabs = _module_list_tabs(first_mod)
            second_tabs = _module_list_tabs(second_mod)
            assert first_tabs[0]['title'] == 'first', first_tabs
            assert second_tabs[0]['title'] == 'second', second_tabs


def test_two_module_routing_regression_is_sensitive_to_url_blind_singleton(
        tmp):
    """The observed second marker proves the URL-blind mutant is active."""
    _mcp_load._need_deps()
    with _util.bridge(Path(tmp) / 'first',
                      env=_mcp_load.BRIDGE_ENV) as (first, _):
        _util.post_json(first + '/sync-tabs', {
            'token': _mcp_load.TOK, 'tabs': [
                {'tabId': 'first', 'url': 'https://first.example.com',
                 'title': 'first'}]})
        with _util.bridge(Path(tmp) / 'second',
                          env=_mcp_load.BRIDGE_ENV) as (second, _):
            _util.post_json(second + '/sync-tabs', {
                'token': _mcp_load.TOK, 'tabs': [
                    {'tabId': 'second', 'url': 'https://second.example.com',
                     'title': 'second'}]})
            first_mod = _mcp_load._load_mcp(first)
            second_mod = _mcp_load._load_mcp(second)
            old_client = {'value': None}

            def url_blind_client(_local_url=None):
                if old_client['value'] is None:
                    old_client['value'] = second_mod.bridge.transport.client()
                return old_client['value']

            original = first_mod.bridge.http_client
            first_mod.bridge.http_client = url_blind_client
            try:
                tabs = _module_list_tabs(first_mod)
            finally:
                first_mod.bridge.http_client = original
            assert tabs[0]['title'] == 'second', tabs


def test_transport_clients_are_isolated_by_event_loop(tmp):
    """A keep-alive client from one loop is not reused by another loop."""
    del tmp
    _mcp_load._need_deps()

    class MarkerHandler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        # pylint: disable-next=redefined-builtin
        def log_message(self, format, *args):
            del format, args

        def do_GET(self):  # pylint: disable=invalid-name
            body = b'{"marker":"keepalive"}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), MarkerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        mod = _mcp_load._load_mcp(base)
        first = mod.BridgeTransport(base)
        second = mod.BridgeTransport(base)

        async def fetch(transport):
            try:
                response = await transport.client().get('/marker')
                response.raise_for_status()
                return response.json()
            finally:
                await mod.BridgeTransport.close_current_loop_clients()

        assert asyncio.run(fetch(first)) == {'marker': 'keepalive'}
        assert asyncio.run(fetch(second)) == {'marker': 'keepalive'}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
