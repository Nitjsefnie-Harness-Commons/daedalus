"""Shared HTTP clients with a bridge-bound view for each MCP caller."""
import asyncio
import threading
import weakref

import httpx


class BridgeTransport:
    """Own one caller's bridge URL while sharing loop-keyed client caches."""

    clients: weakref.WeakKeyDictionary[
        asyncio.AbstractEventLoop, dict[str, httpx.AsyncClient]
    ] = weakref.WeakKeyDictionary()
    lock = threading.Lock()

    def __init__(self, base_url: str):
        self._base_url = base_url

    @classmethod
    async def close_current_loop_clients(cls):
        loop = asyncio.get_running_loop()
        with cls.lock:
            loop_clients = cls.clients.pop(loop, None)
            clients = (() if loop_clients is None
                       else tuple(loop_clients.values()))
        # The entry is gone before the first close, so a client that raises
        # would be unreachable and every later one left open. Close them all,
        # then report the first failure.
        outcomes = await asyncio.gather(
            *(client.aclose() for client in clients), return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome

    def client(self) -> httpx.AsyncClient:
        """Return this caller's cached client without accepting a new URL."""
        loop = asyncio.get_running_loop()
        normalized_url = str(httpx.URL(self._base_url))
        with self.lock:
            loop_clients = self.clients.get(loop)
            if loop_clients is None:
                loop_clients = {}
                self.clients[loop] = loop_clients
            client = loop_clients.get(normalized_url)
            if client is None:
                client = httpx.AsyncClient(
                    base_url=self._base_url, timeout=30.0)
                loop_clients[normalized_url] = client
            return client
