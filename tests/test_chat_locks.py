from __future__ import annotations

import asyncio
import time

from zero.chat_locks import ChatLockMap


def test_two_chats_do_not_serialize_behind_one_lock():
    locks = ChatLockMap()
    order: list[str] = []

    async def hold(chat_id: int, name: str, delay: float) -> None:
        lock = await locks.for_chat(chat_id)
        async with lock:
            order.append(f"{name}-start")
            await asyncio.sleep(delay)
            order.append(f"{name}-end")

    async def run() -> float:
        started = time.monotonic()
        await asyncio.gather(hold(-1, "a", 0.15), hold(-2, "b", 0.15))
        return time.monotonic() - started

    elapsed = asyncio.run(run())
    assert elapsed < 0.28
    assert "a-start" in order and "b-start" in order


def test_same_chat_is_serialized():
    locks = ChatLockMap()
    overlapping = []

    async def hold() -> None:
        lock = await locks.for_chat(42)
        async with lock:
            overlapping.append(1)
            await asyncio.sleep(0.05)
            overlapping.append(-1)

    async def run() -> None:
        await asyncio.gather(hold(), hold())

    asyncio.run(run())
    assert overlapping == [1, -1, 1, -1]
