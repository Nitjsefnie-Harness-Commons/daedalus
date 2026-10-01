#!/usr/bin/env python3
"""The MCP transport: credentials, URL precedence, and ext_cmd's wait."""
import asyncio
import importlib.util
import os
import sys
import time
from contextvars import ContextVar
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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


if __name__ == '__main__':
    raise SystemExit(main())
