#!/usr/bin/env python3
"""Per-session credentials and bridge URL precedence."""
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
        _util.skip('mcp_transport dependency (httpx) not installed')
    return _util.load(
        _util.ROOT / 'mcp_transport.py',
        'mcp_transport_under_test_' + str(time.time_ns()))


def test_sessions_keep_distinct_token_contexts(tmp):
    """A later session cannot replace an earlier session's token context."""
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
    """Each winning URL source defeats every weaker source at once."""
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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='mcptransport_')


if __name__ == '__main__':
    raise SystemExit(main())
