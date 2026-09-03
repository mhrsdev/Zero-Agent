"""FloodWait backoff for Telegram sends. Never spins a login loop."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")
logger = logging.getLogger("zero.listener")


async def with_flood_wait(factory: Callable[[], Awaitable[T]], *, enabled: bool = True, cap_seconds: int = 120) -> T:
    try:
        return await factory()
    except Exception as exc:
        if enabled and type(exc).__name__ == "FloodWaitError":
            delay = min(int(getattr(exc, "seconds", 1) or 1), cap_seconds)
            logger.warning("FLOOD_WAIT_BACKOFF seconds=%s", delay)
            await asyncio.sleep(delay)
            return await factory()
        raise
