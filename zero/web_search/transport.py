from __future__ import annotations

import asyncio
import http.client
import json
import queue
import socket
import ssl
import threading
from collections import OrderedDict
from urllib.parse import urlsplit

# Upper bound on how many distinct hosts may hold idle keep-alive sockets. The
# idle pool was keyed by (scheme, host, port) with no eviction, so a listener
# that searches thousands of distinct result hosts held an open socket to every
# one of them until the peer reset it, walking towards the process FD limit.
_MAX_IDLE_HOSTS = 32


class TransportError(RuntimeError):
    """Base for transport failures.

    Every failure used to be a bare ``RuntimeError``, and the pipeline logs only
    the exception type — so a 403 from a SearXNG instance with the JSON format
    disabled, a 400 from a bad provider payload, a 502, a rejected redirect and a
    blocked private destination were all recorded as
    ``exception_type=RuntimeError``, which is unactionable. These types exist so
    the log names the actual class of failure.
    """


class HttpStatusError(TransportError):
    def __init__(self, status: int) -> None:
        super().__init__(f'HTTP {status}')
        self.status = status


class HttpRedirectRejected(TransportError):
    """A 3xx the transport refuses to follow.

    Redirects are not followed on purpose: following one re-points the request at
    a host the SSRF guard never resolved. Carrying the location lets a caller
    decide whether to re-request it through the guard.
    """

    def __init__(self, status: int, location: str = '') -> None:
        super().__init__(f'HTTP redirect rejected: {status}')
        self.status = status
        self.location = location


class DestinationRejected(TransportError, ValueError):
    """The target resolved to a private, link-local or unresolvable address.

    Also a ``ValueError`` so the existing contract test — and any caller that
    treats a bad destination as a bad argument — keeps working.
    """


class ConnectionPoolTransport:
    """Small stdlib HTTP/HTTPS connection pool with async, bounded access."""

    def __init__(
        self, max_connections_per_host: int = 4, user_agent: str = 'Zero-WebSearch/2.0',
        *, allowed_private_endpoints: set[tuple[str, str, int]] | None = None,
        max_idle_hosts: int = _MAX_IDLE_HOSTS,
    ):
        self.max_connections_per_host = max(1, int(max_connections_per_host))
        self.user_agent = user_agent
        self.allowed_private_endpoints = allowed_private_endpoints or set()
        self.max_idle_hosts = max(1, int(max_idle_hosts))
        # LRU by host: least-recently-used entries are closed once the bound is
        # exceeded. Only idle sockets live here, so eviction can never close a
        # connection that a request is still using.
        self._idle: OrderedDict[tuple[str, str, int], queue.LifoQueue] = OrderedDict()
        self._semaphores: dict[tuple[str, str, int], asyncio.Semaphore] = {}
        # Requests run in worker threads, so the pool map needs real mutual
        # exclusion; this lock was previously created and never used.
        self._lock = threading.Lock()
        self._closed = False

    @property
    def pool_size(self) -> int:
        with self._lock:
            return sum(pool.qsize() for pool in self._idle.values())

    def _acquire_pool(self, key) -> queue.LifoQueue:
        """Return the idle pool for ``key``, evicting the oldest hosts if needed."""
        with self._lock:
            pool = self._idle.get(key)
            if pool is None:
                pool = queue.LifoQueue(maxsize=self.max_connections_per_host)
                self._idle[key] = pool
            self._idle.move_to_end(key)
            evicted = []
            while len(self._idle) > self.max_idle_hosts:
                _, stale = self._idle.popitem(last=False)
                evicted.append(stale)
        for stale in evicted:
            _drain(stale)
        return pool

    async def get_text(self, url: str, timeout: float, max_bytes: int) -> str:
        return await self._request_text("GET", url, timeout, max_bytes)

    async def post_json(
        self,
        url: str,
        payload: dict,
        timeout: float,
        max_bytes: int,
        *,
        headers: dict[str, str] | None = None,
    ) -> str:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request_headers = dict(headers or {})
        request_headers["Content-Type"] = "application/json"
        return await self._request_text(
            "POST", url, timeout, max_bytes, body=body, headers=request_headers,
        )

    async def _request_text(
        self,
        method: str,
        url: str,
        timeout: float,
        max_bytes: int,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        key = self._key(url)
        semaphore = self._semaphores.setdefault(key, asyncio.Semaphore(self.max_connections_per_host))
        async with semaphore:
            return await asyncio.to_thread(
                self._request_text_sync,
                method,
                url,
                timeout,
                max_bytes,
                key,
                body,
                headers or {},
            )

    def _request_text_sync(
        self,
        method: str,
        url: str,
        timeout: float,
        max_bytes: int,
        key,
        body: bytes | None,
        headers: dict[str, str],
    ) -> str:
        if self._closed:
            raise RuntimeError('transport is closed')
        parts = urlsplit(url)
        path = parts.path or '/'
        if parts.query:
            path += '?' + parts.query
        pool = self._acquire_pool(key)
        try:
            connection = pool.get_nowait()
        except queue.Empty:
            connection = self._new_connection(key, timeout)
        reusable = False
        request_headers = {
            'User-Agent': self.user_agent,
            'Accept': 'application/json,text/html,application/xml,text/xml,*/*;q=0.5',
            'Connection': 'keep-alive',
            **headers,
        }
        try:
            connection.timeout = timeout
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise HttpRedirectRejected(response.status, response.getheader('Location') or '')
            response_body = response.read(max_bytes + 1)
            if response.status >= 400:
                raise HttpStatusError(response.status)
            reusable = len(response_body) <= max_bytes and not response.will_close
            charset = response.headers.get_content_charset() or 'utf-8'
            return response_body[:max_bytes].decode(charset, 'replace')
        finally:
            if reusable and not self._closed:
                try:
                    pool.put_nowait(connection)
                except queue.Full:
                    # Bounded pool: never hold more idle sockets per host than
                    # the concurrency limit allows.
                    connection.close()
            else:
                connection.close()

    def _new_connection(self, key, timeout):
        scheme, host, port = key
        from .extraction import _resolved_public_addresses
        if key in self.allowed_private_endpoints:
            pinned_ip = host
        else:
            addresses = _resolved_public_addresses(host, port)
            if not addresses:
                raise DestinationRejected('private or unresolved destination rejected')
            pinned_ip = addresses[0]
        if scheme == 'https':
            connection = http.client.HTTPSConnection(host, port, timeout=timeout, context=ssl.create_default_context())
        else:
            connection = http.client.HTTPConnection(host, port, timeout=timeout)
        if key not in self.allowed_private_endpoints:
            connection._create_connection = lambda _address, timeout=None, source_address=None: socket.create_connection((pinned_ip, port), timeout, source_address)
        return connection

    @staticmethod
    def _key(url: str) -> tuple[str, str, int]:
        parts = urlsplit(url)
        if parts.scheme not in {'http', 'https'} or not parts.hostname:
            raise ValueError('only absolute HTTP(S) URLs are supported')
        return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80)

    def close(self) -> None:
        self._closed = True
        with self._lock:
            pools = list(self._idle.values())
            self._idle.clear()
            self._semaphores.clear()
        for pool in pools:
            _drain(pool)


def _drain(pool: queue.LifoQueue) -> None:
    """Close every idle connection held by ``pool``."""
    while True:
        try:
            connection = pool.get_nowait()
        except queue.Empty:
            return
        try:
            connection.close()
        except OSError:
            pass
