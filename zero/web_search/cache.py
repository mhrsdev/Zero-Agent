from __future__ import annotations

import copy
import time
from collections import OrderedDict
from typing import Generic, TypeVar

T = TypeVar('T')


class TTLCache(Generic[T]):
    def __init__(self, ttl_seconds: float, max_entries: int = 200, clock=time.monotonic):
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self._clock = clock
        self._items: OrderedDict[str, tuple[float, T]] = OrderedDict()

    def get(self, key: str) -> T | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, value = item
        if self._clock() >= expires_at:
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return copy.deepcopy(value)

    def set(self, key: str, value: T) -> None:
        self._items[key] = (self._clock() + self.ttl_seconds, copy.deepcopy(value))
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._items.clear()
        else:
            self._items.pop(key, None)
