from __future__ import annotations

import asyncio
import http.client
import queue
import socket
import ssl
import threading
from collections import defaultdict
from urllib.parse import urlsplit


class ConnectionPoolTransport:
    """Small stdlib HTTP/HTTPS connection pool with async, bounded access."""

    def __init__(
        self, max_connections_per_host: int = 4, user_agent: str = 'Zero-WebSearch/2.0',
        *, allowed_private_endpoints: set[tuple[str, str, int]] | None = None,
    ):
        self.max_connections_per_host = max(1, int(max_connections_per_host))
        self.user_agent = user_agent
        self.allowed_private_endpoints = allowed_private_endpoints or set()
        self._idle: dict[tuple[str, str, int], queue.LifoQueue] = defaultdict(queue.LifoQueue)
        self._semaphores: dict[tuple[str, str, int], asyncio.Semaphore] = {}
        self._lock = threading.Lock()
        self._closed = False

    @property
    def pool_size(self) -> int:
        return sum(pool.qsize() for pool in self._idle.values())

    async def get_text(self, url: str, timeout: float, max_bytes: int) -> str:
        key = self._key(url)
        semaphore = self._semaphores.setdefault(key, asyncio.Semaphore(self.max_connections_per_host))
        async with semaphore:
            return await asyncio.to_thread(self._get_text_sync, url, timeout, max_bytes, key)

    def _get_text_sync(self, url: str, timeout: float, max_bytes: int, key) -> str:
        if self._closed:
            raise RuntimeError('transport is closed')
        parts = urlsplit(url)
        path = parts.path or '/'
        if parts.query:
            path += '?' + parts.query
        pool = self._idle[key]
        try:
            connection = pool.get_nowait()
        except queue.Empty:
            connection = self._new_connection(key, timeout)
        reusable = False
        try:
            connection.timeout = timeout
            connection.request('GET', path, headers={'User-Agent': self.user_agent, 'Accept': 'application/json,text/html,application/xml,text/xml,*/*;q=0.5', 'Connection': 'keep-alive'})
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise RuntimeError(f'HTTP redirect rejected: {response.status}')
            body = response.read(max_bytes + 1)
            if response.status >= 400:
                raise RuntimeError(f'HTTP {response.status}')
            reusable = len(body) <= max_bytes and not response.will_close
            charset = response.headers.get_content_charset() or 'utf-8'
            return body[:max_bytes].decode(charset, 'replace')
        finally:
            if reusable and not self._closed:
                pool.put(connection)
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
                raise ValueError('private or unresolved destination rejected')
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
        for pool in self._idle.values():
            while True:
                try:
                    pool.get_nowait().close()
                except queue.Empty:
                    break
