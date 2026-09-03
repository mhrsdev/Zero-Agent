"""One shared sample per interval instead of one per connected client.

The panel's SSE endpoints poll on a 2s/5s timer *per browser tab*, and each tick
used to rebuild the whole payload from scratch: the listener pid file, procfs,
disk usage, and a 1 MiB tail of four log files. N open tabs therefore paid N
times for identical data, all of it on the event loop.

:class:`SharedSnapshot` collapses that to one refresh per TTL:

* a value younger than ``ttl`` is returned as it stands;
* callers that arrive while a refresh is in flight await that same refresh, so a
  burst of reconnecting tabs samples the host once;
* the refresh itself runs in a worker thread, because every producer here is
  blocking file I/O; and
* a cancelled waiter does not cancel the shared refresh (``shield``), while
  cancellation still propagates to the waiter -- panel shutdown depends on it.

The value is deliberately *not* refreshed in the background: with no client
connected the panel does no work at all.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

_MISSING = object()


class SharedSnapshot:
    """A single-flight, TTL-bounded sample of one blocking producer."""

    def __init__(self, producer: Callable[[], Any], *, ttl: float, clock: Callable[[], float] = time.monotonic):
        self._producer = producer
        self._ttl = ttl
        self._clock = clock
        self._value: Any = _MISSING
        self._sampled_at = 0.0
        self._refresh: asyncio.Future[Any] | None = None

    @property
    def pending(self) -> bool:
        """Whether a refresh is still running (shutdown must not leave one)."""
        return self._refresh is not None and not self._refresh.done()

    async def get(self) -> Any:
        if self._value is not _MISSING and self._clock() - self._sampled_at < self._ttl:
            return self._value
        refresh = self._refresh
        if refresh is None or refresh.done():
            refresh = self._refresh = asyncio.ensure_future(self._sample())
        return await asyncio.shield(refresh)

    async def _sample(self) -> Any:
        value = await asyncio.to_thread(self._producer)
        self._value, self._sampled_at = value, self._clock()
        return value

    async def close(self) -> None:
        """Cancel and reap an in-flight refresh so shutdown leaves no task behind."""
        refresh, self._refresh = self._refresh, None
        if refresh is None:
            return
        if not refresh.done():
            refresh.cancel()
            await asyncio.wait({refresh})
        if not refresh.cancelled():
            refresh.exception()  # retrieved so a failed refresh is not reported at exit
