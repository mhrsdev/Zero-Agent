from __future__ import annotations

import asyncio
from typing import Any

from ..memory_v3 import MemoryV3Service


class MemoryService:
    """Stable active-memory boundary backed exclusively by Memory V3."""

    backend_name = "memory-v3"

    def __init__(self, backend: MemoryV3Service):
        self.backend = backend

    async def context(self, message: Any, *, target_user_id: int | None = None, target_user_ids: tuple[int, ...] = ()):
        return await self.backend.context(message, target_user_id=target_user_id, target_user_ids=target_user_ids)

    async def put(self, item: Any) -> str:
        return await self.backend.put(item)

    async def observe(self, message: Any, reply_text: str = "") -> None:
        await self.backend.observe(message, reply_text)

    async def record_message(self, message: Any, role: str = "user") -> None:
        await self.backend.record_message(message, role=role)

    async def metric(self, trace_id: str, kind: str, payload: dict[str, Any]) -> None:
        await self.backend.metric(trace_id, kind, payload)

    async def thread_context(self, message: Any, **kwargs: Any):
        return await self.backend.thread_context(message, **kwargs)

    def observe_sync_for_test(self, message: Any) -> None:
        asyncio.run(self.observe(message))

    def context_sync_for_test(self, message: Any):
        return asyncio.run(self.context(message))
