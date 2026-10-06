#!/usr/bin/env python3
"""The MCP transport: credentials, URL precedence, ext_cmd's wait, and closing.

Absorbs tests/test_mcp_transport_close.py.
"""
import asyncio
import importlib.util
import os
import sys
import time
from contextvars import ContextVar
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_load  # noqa: E402
import _util  # noqa: E402


DEPS = importlib.util.find_spec('httpx') is not None


def _transport():
    if not DEPS:
        _util.skip('daedalus_mcp.transport dependency (httpx) not installed')
    return _util.load(
        _util.ROOT / 'daedalus_mcp' / 'transport.py',
        'mcp_transport_under_test_' + str(time.time_ns()))


def _session(transport, token='mcptok', url='http://127.0.0.1:18001'):
    token_var = ContextVar(
        'mcp_transport_token_' + str(time.time_ns()), default=token)
    return transport.BridgeSession(url, token_var)


def _capture(coroutine):
    """Run one coroutine; with `transport`, close the run's real clients."""
    try:
        return asyncio.run(coroutine)
    except Exception as failure:  # noqa: BLE001
        return f'raised {type(failure).__name__}: {failure}'


def test_sessions_keep_distinct_token_contexts(tmp):
    del tmp
    transport = _transport()
    first_token = ContextVar('first_mcp_token', default='')
    second_token = ContextVar('second_mcp_token', default='')
    first_token.set('one-token')
    second_token.set('two-token')

    first = transport.BridgeSession('http://127.0.0.1:11001', first_token)
    second = transport.BridgeSession('http://127.0.0.1:11002', second_token)

    assert first.auth() == {'Authorization': 'Bearer one-token'}
    assert second.auth() == {'Authorization': 'Bearer two-token'}


def test_url_resolution_preserves_the_full_precedence_order(tmp):
    del tmp
    transport = _transport()
    cases = (
        ('environment', 'http://127.0.0.1:12001',
         'http://127.0.0.1:12002', 'http://127.0.0.1:12003', '12004',
         'http://127.0.0.1:12005', 'http://127.0.0.1:12001'),
        ('explicit', '', 'http://127.0.0.1:12012',
         'http://127.0.0.1:12013', '12014',
         'http://127.0.0.1:12015', 'http://127.0.0.1:12012'),
        ('started', '', '', 'http://127.0.0.1:12023', '12024',
         'http://127.0.0.1:12025', 'http://127.0.0.1:12023'),
        ('port', '', '', '', '12034', 'http://127.0.0.1:12035',
         'http://127.0.0.1:12034'),
        ('fallback', '', '', '', '', 'http://127.0.0.1:12045',
         'http://127.0.0.1:12045'),
    )
    saved = {name: os.environ.get(name)
             for name in ('DAEDALUS_LOCAL_URL', 'DAEDALUS_PORT')}
    try:
        for (name, override, explicit, started, port, fallback,
             expected) in cases:
            if port:
                os.environ['DAEDALUS_PORT'] = port
            else:
                os.environ.pop('DAEDALUS_PORT', None)
            os.environ.pop('DAEDALUS_LOCAL_URL', None)
            session = transport.BridgeSession(
                fallback, ContextVar(f'{name}_token', default='token'))
            session.rebind(started)
            if override:
                os.environ['DAEDALUS_LOCAL_URL'] = override
            actual = session.resolved_local_url(explicit)
            assert actual == expected, (name, actual, expected)
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_an_extension_command_answers_on_the_terms_its_wait_set(tmp):
    """Both sides of `wait`, against the real `ext_cmd`.

    The probe the pinned table drives replaces this method wholesale, so a
    case written there cannot see a change to it — and this is what the
    table's own no-wait cases lean on. Unwaited it reports the command and
    neither polls nor checks a timeout it would never use, because
    `checked_timeout` exists to refuse a wait that cannot wait BEFORE the
    browser is handed the command. Waited it answers the polled body
    unchanged: `result.result`, the `roundtrip_ms` graft only when asked
    for, and the empty default for a body with no result.
    """
    del tmp
    transport = _transport()
    session = _session(transport)
    polls, sent = [], []

    async def put(_path, payload):
        sent.append(payload)
        return {'did': 'delivery', 'command': {**payload, '_did': 'delivery'}}

    async def poll_result(tab, timeout, **_kwargs):
        polls.append((tab, timeout))
        return {'error': None, 'result': {'path': '_ss/shot.png', 'size': 3},
                'roundtrip_ms': 41}

    session.put = put
    session.poll_result = poll_result

    unwaited = _capture(session.ext_cmd(
        '_ss', 'screenshot', timeout=-1, wait=False))
    assert unwaited == {'command': {
        'id': '_ss', 'type': 'screenshot', 'tab': 'extension',
        '_did': 'delivery'}}, unwaited
    assert polls == [] and len(sent) == 1, (polls, sent)

    plain = _capture(session.ext_cmd('_ss', 'screenshot'))
    assert plain == {'path': '_ss/shot.png', 'size': 3}, plain
    grafted = _capture(session.ext_cmd(
        '_ss', 'screenshot', include_roundtrip=True))
    assert grafted == {
        'path': '_ss/shot.png', 'size': 3, 'roundtrip_ms': 41}, grafted
    assert polls == [('extension', 10.0), ('extension', 10.0)], polls

    async def no_result(*_args, **_kwargs):
        return {'error': None}

    session.poll_result = no_result
    assert _capture(session.ext_cmd('_ss', 'screenshot')) == {}

    # The waited limb still refuses, and refuses before the PUT.
    before = len(sent)
    refused = _capture(session.ext_cmd('_ss', 'screenshot', timeout=-1))
    assert refused == (
        "raised ValueError: timeout must be a finite positive number of "
        "seconds; got -1"), refused
    assert len(sent) == before, sent


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='mcptransport_')


class ClosingClient:
    """A cached client whose clean close yields before it records. A
    raising close never yields: the test can only see an early propagation
    while the raiser stays ahead of the clean close."""

    def __init__(self, base_url, failure=None, closed=None, **_kwargs):
        self.base_url = base_url
        self.failure = failure
        self.closed = closed if closed is not None else []

    async def aclose(self):
        if self.failure is None:
            for _ in range(3):
                await asyncio.sleep(0)
        self.closed.append(self.base_url)
        if self.failure is not None:
            raise self.failure


def _close_registered_clients(transport, failures):
    urls = [f'http://127.0.0.1:{18100 + index}'
            for index in range(len(failures))]
    closed = []

    async def exercise():
        loop = asyncio.get_running_loop()
        factories = dict(zip(urls, failures))
        with mock.patch.object(
                transport.httpx, 'AsyncClient',
                lambda base_url, **kw: ClosingClient(
                    base_url, factories[base_url], closed, **kw)):
            for url in urls:
                transport.BridgeTransport(url).client()
        # CancelledError is what gather hands back for a cancelled close,
        # so it is admitted by name; a bare BaseException catch would also
        # swallow a runner abort.
        try:
            await transport.BridgeTransport.close_current_loop_clients()
        except (Exception, asyncio.CancelledError) as failure:  # noqa: BLE001
            raised = failure
        else:
            raised = None
        return (list(closed), raised,
                loop in transport.BridgeTransport.clients)

    closed_on_return, raised, entry_kept = asyncio.run(exercise())
    return closed_on_return, raised, entry_kept, urls


def test_closing_reports_the_first_client_close_failure_after_closing_all(
        tmp):
    del tmp
    transport = _transport()
    first = RuntimeError('first close failed')
    last = RuntimeError('last close failed')
    closed, raised, entry_kept, urls = _close_registered_clients(
        transport, [first, None, last])
    assert sorted(closed) == urls, (closed, urls)
    assert raised is first, (raised, first)
    assert not entry_kept


def test_closing_reports_a_cancelled_client_close(tmp):
    """gather hands back a fresh CancelledError, which is no Exception."""
    del tmp
    transport = _transport()
    closed, raised, entry_kept, urls = _close_registered_clients(
        transport, [asyncio.CancelledError(), None])
    assert sorted(closed) == urls, (closed, urls)
    assert isinstance(raised, asyncio.CancelledError), raised
    assert not entry_kept


def test_closing_every_client_cleanly_returns_normally(tmp):
    del tmp
    transport = _transport()
    closed, raised, entry_kept, urls = _close_registered_clients(
        transport, [None, None])
    assert sorted(closed) == urls, (closed, urls)
    assert raised is None, raised
    assert not entry_kept


def test_mcp_lifespan_closes_loop_clients(tmp):
    """The MCP app closes bridge clients when its own lifespan shuts down."""
    del tmp
    _mcp_load._need_deps()
    mod = _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 0)
    app_box = {}
    original_factory = mod.mcp.streamable_http_app

    def capture_app(**settings):
        app_box['value'] = original_factory(**settings)
        return app_box['value']

    mod.mcp.streamable_http_app = capture_app

    handed, banner, _built = _mcp_load._serve_with_fake_uvicorn(mod)
    for server_socket in handed:
        server_socket.close()
    mod.mcp.streamable_http_app = original_factory

    assert not mod.startup_error, mod.startup_error
    assert f'127.0.0.1:{mod.bound_port}' in banner, banner
    app = app_box['value']

    async def drive_lifespan():
        loop = asyncio.get_running_loop()
        async with app.router.lifespan_context(app):
            mod.BridgeTransport('http://127.0.0.1:1').client()
            assert len(mod.BridgeTransport.clients[loop]) == 1
        return not mod.BridgeTransport.clients.get(loop)

    assert asyncio.run(drive_lifespan())


if __name__ == '__main__':
    raise SystemExit(main())
