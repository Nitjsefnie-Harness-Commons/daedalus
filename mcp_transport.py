"""Shared HTTP clients with a bridge-bound view for each MCP caller."""
import asyncio
from contextvars import ContextVar
import math
import os
import threading
from typing import Any
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


class BridgeSession:
    """Own one MCP module instance's bridge binding and credentials."""

    def __init__(self, local_url: str, token_var: ContextVar[str]):
        self._local_url = local_url
        self._token_var = token_var
        self._started_local_url: str | None = None
        self._transport = BridgeTransport(self.resolved_local_url())

    @property
    def transport(self) -> BridgeTransport:
        return self._transport

    def resolved_local_url(self, local_url: str | None = None) -> str:
        """Resolve the bridge URL for this client lookup.

        The caller's bridge URL is resolved before its transport facade is bound.
        The URL supplied after the bridge binds wins over the port-derived
        fallback, while the explicit standalone override remains strongest.
        ``LOCAL_URL`` is the import-time fallback retained for callers that load
        this module by path and restore the environment afterwards.
        """
        override = os.environ.get('DAEDALUS_LOCAL_URL')
        if override:
            return override
        if local_url:
            return local_url
        if self._started_local_url:
            return self._started_local_url
        if 'DAEDALUS_PORT' in os.environ:
            return f'http://127.0.0.1:{os.environ["DAEDALUS_PORT"]}'
        return self._local_url

    def rebind(self, local_url: str | None = None):
        if local_url is not None:
            self._started_local_url = local_url
        self._transport = BridgeTransport(self.resolved_local_url())

    def http_client(self, local_url: str | None = None) -> httpx.AsyncClient:
        """Return this caller's client, or a compatibility client for a URL."""
        if local_url is not None:
            return BridgeTransport(self.resolved_local_url(local_url)).client()
        return self._transport.client()

    def token(self) -> str:
        t = self._token_var.get()
        if not t:
            raise RuntimeError('no token in context')
        return t

    def auth(self) -> dict[str, str]:
        """The bridge's pre-body credential carrier.

        The bridge settles credentials before it reads a request body, so a body
        token alone caps what this client may send at the unauthenticated window.
        It is also what keeps a reusable credential out of a request target, which
        a reverse-proxy access log retains and a query parameter cannot avoid.
        Same header, same value the MCP listener itself required to get here.
        """
        return {'Authorization': f'Bearer {self.token()}'}

    async def get(self, path: str, **params) -> Any:
        r = await self.http_client().get(
            path, params=params, headers=self.auth())
        r.raise_for_status()
        return r.json()

    async def put(self, path: str, body: dict) -> dict:
        body = {**body, 'token': self.token()}
        r = await self.http_client().put(
            path, json=body, headers=self.auth())
        r.raise_for_status()
        return r.json()

    async def post(self, path: str, body: dict) -> dict:
        body = {**body, 'token': self.token()}
        r = await self.http_client().post(
            path, json=body, headers=self.auth())
        r.raise_for_status()
        return r.json()

    async def delete(self, path: str, body: dict) -> dict:
        body = {**body, 'token': self.token()}
        r = await self.http_client().request(
            'DELETE', path, json=body, headers=self.auth())
        r.raise_for_status()
        return r.json()

    async def get_raw(self, route: str, **params) -> bytes:
        """Fetch one bridge route as raw bytes. The route is named `route` rather
        than `path` so a caller can pass a `path` query parameter, which the
        screenshot download does."""
        r = await self.http_client().get(
            route, params=params, headers=self.auth())
        r.raise_for_status()
        return r.content

    async def poll_result(self, tab: str, timeout: float,
                          interval: float = 0.5,
                          expect_id: str | None = None,
                          expect_delivery: str | None = None) -> dict:
        """Poll until the named command delivery is conditionally consumed.

        The delivery id rejects stale results from an earlier invocation even when
        its command id is reused. The result generation makes peek then consume
        safe when another caller replaces the shared slot between those requests.

        The wait ramps 20ms -> `interval` instead of sleeping a flat `interval` up
        front: most commands finish in tens of milliseconds, and the fixed first
        sleep was adding half a second of dead time to every single tool call."""
        import time
        peek = {}
        if tab:
            peek['tab'] = tab
        if expect_delivery:
            peek['delivery'] = expect_delivery
        auth = self.auth()
        deadline = time.time() + timeout
        wait = 0.02
        while time.time() < deadline:
            await asyncio.sleep(wait)
            wait = min(wait * 2, interval)
            r = await self.http_client().get(
                '/result', params=peek, headers=auth)
            r.raise_for_status()
            data = r.json()
            if data.get('pending'):
                continue
            if (expect_id is not None and data.get('id') != expect_id
                    or expect_delivery is not None
                    and data.get('deliveryId') != expect_delivery):
                # Someone else's result. Leave it where it is for them.
                continue
            generation = data.get('resultGeneration')
            if not generation:
                continue
            take = {**peek, 'consume': '1', 'expected': generation}
            consumed = await self.http_client().get(
                '/result', params=take, headers=auth)
            consumed.raise_for_status()
            receipt = consumed.json()
            if (receipt.get('consumed') is not True
                    or receipt.get('resultGeneration') != generation):
                continue
            return data
        raise TimeoutError(f'no result within {timeout}s')

    @staticmethod
    def checked_timeout(timeout: float) -> float:
        """Refuse a wait that cannot wait, BEFORE the command is submitted.

        The command is PUT first and the deadline evaluated afterwards, so a
        non-positive or non-finite timeout polls zero times and raises a timeout
        for a command the browser has already been handed. The caller is told
        nothing ran; retrying then runs the side effect a second time.
        """
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError(f'timeout must be a finite positive number of seconds; got {timeout!r}')
        return timeout

    async def ext_cmd(self, cmd_id: str, cmd_type: str,
                      timeout: float = 10.0,
                      include_roundtrip: bool = False, **fields) -> Any:
        """Send a typed extension command (tab=extension) and return result.result.

        The server computes `roundtrip_ms` (enqueue -> result arrival) as a sibling of
        `result` in the body, so returning result.result alone drops it.
        include_roundtrip merges it back in, for tools where how long the extension
        took is part of the answer."""
        self.checked_timeout(timeout)
        payload = {
            'id': cmd_id, 'type': cmd_type, 'tab': 'extension', **fields}
        sent = await self.put('/command', payload)
        res = await self.poll_result(
            'extension', timeout, expect_id=cmd_id,
            expect_delivery=sent.get('did'))
        if res.get('error'):
            raise RuntimeError(f'ext {cmd_type}: {res["error"]}')
        out = res.get('result', {})
        if (include_roundtrip and isinstance(out, dict)
                and 'roundtrip_ms' in res):
            out = {**out, 'roundtrip_ms': res['roundtrip_ms']}
        return out
