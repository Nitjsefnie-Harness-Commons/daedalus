#!/usr/bin/env python3
"""A peek whose body the poll does not expect is left for its owner."""
import importlib.util
import sys
import time
from contextvars import ContextVar
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _mcp_poll_outcome import _poll_outcome  # noqa: E402
from _mcp_transport_probes import (  # noqa: E402
    ClientProbe, clock_script)

DEPS = importlib.util.find_spec('httpx') is not None


def _transport():
    if not DEPS:
        _util.skip('daedalus_mcp.transport dependency (httpx) not installed')
    return _util.load(
        _util.ROOT / 'daedalus_mcp' / 'transport.py',
        'mcp_poll_matching_' + str(time.time_ns()))


def _session(transport, token='mcptok', url='http://127.0.0.1:18001'):
    token_var = ContextVar(
        'mcp_poll_matching_token_' + str(time.time_ns()),
        default=token)
    return transport.BridgeSession(url, token_var)


def test_poll_leaves_every_unmatched_delivery_in_place(tmp):
    """One row per rejection axis: the consume after the one peek the clock
    admits would succeed, so only the matching rule stops the hand-over."""
    del tmp
    transport = _transport()
    for label, command, delivery, expect_delivery in (
            ('no delivery id', 'command', None, None),
            ('empty delivery id', 'command', '', None),
            ('foreign command id', 'other', 'wanted', 'wanted'),
            ('foreign delivery id', 'command', 'stale', 'wanted'),
            # '' is where a truthiness check and `is None` disagree.
            ('empty expectation', 'command', '', '')):
        body = {'id': command, 'resultGeneration': 'generation-1',
                'result': {'value': 1}}
        if delivery is not None:
            body['deliveryId'] = delivery
        client = ClientProbe((
            body, {'consumed': True, 'resultGeneration': 'generation-1'}))
        session = _session(transport)
        session.http_client = lambda probe=client: probe
        session.monotonic = clock_script(100.0, 100.0, 100.0, 100.5)

        result = _poll_outcome(session.poll_result(
            '', 0.001, interval=0, expect_id='command',
            expect_delivery=expect_delivery))

        peeks = [call for call in client.calls if call[1] == '/result']
        assert len(peeks) == 1, (label, client.calls)
        expected = 'raised TimeoutError: no result within 0.001s'
        assert result == expected, (label, result)


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='mcppollmatching_')


if __name__ == '__main__':
    raise SystemExit(main())
