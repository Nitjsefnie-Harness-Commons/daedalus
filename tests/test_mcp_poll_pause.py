#!/usr/bin/env python3
"""The poll's ramp pause, pinned to the remaining window on the clock."""
import asyncio
import importlib.util
import sys
import time
from contextvars import ContextVar
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _mcp_transport_probes import ClientProbe, clock_script  # noqa: E402

DEPS = importlib.util.find_spec('httpx') is not None


def _transport():
    if not DEPS:
        _util.skip('daedalus_mcp.transport dependency (httpx) not installed')
    return _util.load(
        _util.ROOT / 'daedalus_mcp' / 'transport.py',
        'mcp_poll_pause_' + str(time.time_ns()))


def _session(transport, token='mcptok', url='http://127.0.0.1:18001'):
    token_var = ContextVar(
        'mcp_poll_pause_token_' + str(time.time_ns()),
        default=token)
    return transport.BridgeSession(url, token_var)


def _capture(coroutine):
    try:
        return asyncio.run(coroutine)
    except Exception as failure:  # noqa: BLE001
        return f'raised {type(failure).__name__}: {failure}'


def test_poll_caps_the_ramp_pause_at_the_remaining_window(tmp):
    """Every ramp pause asks for no more than what is left of the wait.

    The pause seam is recorded beside the scripted clock, so a loop
    that sleeps its uncapped ramp or bypasses the seam fails on the
    recorded durations rather than on real elapsed time.
    """
    del tmp
    transport = _transport()
    session = _session(transport)
    client = ClientProbe(({'pending': True},) * 2)
    session.http_client = lambda: client
    pauses = []

    async def pause(seconds):
        pauses.append(seconds)

    session.pause = pause
    # Deadline read, then both admitted iterations read 100.0499 at
    # entry and after the sleep: 0.0001 left at every pause request.
    session.monotonic = clock_script(
        100.0, 100.0499, 100.0499, 100.0499, 100.0499, 100.5)

    result = _capture(session.poll_result(
        '', 0.05, expect_id='command', expect_delivery='wanted'))

    expected = 'raised TimeoutError: no result within 0.05s'
    assert result == expected, (result, expected)
    peeks = [call for call in client.calls if call[1] == '/result']
    assert len(peeks) == 2, client.calls
    assert len(pauses) == len(peeks), (pauses, peeks)
    expected_pause = 100.0 + 0.05 - 100.0499
    assert pauses == [expected_pause, expected_pause], pauses


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='mcppollpause_')


if __name__ == '__main__':
    raise SystemExit(main())
