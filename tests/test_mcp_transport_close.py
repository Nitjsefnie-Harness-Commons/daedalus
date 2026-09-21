#!/usr/bin/env python3
"""Closing the MCP transport's cached clients for the running loop."""
import asyncio
import importlib.util
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


DEPS = importlib.util.find_spec('httpx') is not None


def _transport():
    if not DEPS:
        _util.skip('daedalus_mcp.transport dependency (httpx) not installed')
    return _util.load(
        _util.ROOT / 'daedalus_mcp' / 'transport.py',
        'mcp_transport_close_' + str(time.time_ns()))


class ClosingClient:
    """A cached client whose clean close yields before it records. A
    raising close never yields: the test can only see an early propagation
    while the raiser stays ahead of the clean close."""

    def __init__(self, base_url, failure=None, closed=None, **_kwargs):
        self.base_url = base_url
        self.failure = failure
        self.closed = closed

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


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='mcptransportclose_')


if __name__ == '__main__':
    raise SystemExit(main())
