"""Per-chat asyncio locks so one busy group cannot stall every other group."""
from __future__ import annotations

import asyncio


class ChatLockMap:
    """One lock per chat id; creation is serialised, holding is not global."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def for_chat(self, chat_id: int) -> asyncio.Lock:
        key = int(chat_id)
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock
