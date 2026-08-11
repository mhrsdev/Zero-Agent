from __future__ import annotations

import asyncio
from typing import Any

from ..memory_v3 import MemoryV3Service
from ..tenancy import Permission, Scope, ScopeViolation


class MemoryService:
    """Stable active-memory boundary backed exclusively by Memory V3.

    A service may be bound to a :class:`~zero.tenancy.Scope`. A bound service
    refuses any message whose chat does not belong to that scope's group, and
    refuses reads and writes when the scope's principal lacks the corresponding
    memory permission. Binding is what makes memory multi-tenant: without it the
    only thing separating two groups is that callers remember to pass the right
    ``chat_id``.

    An unbound service keeps the previous single-tenant behaviour so existing
    composition roots continue to work while they are migrated.
    """

    backend_name = "memory-v3"

    def __init__(
        self,
        backend: MemoryV3Service,
        *,
        scope: Scope | None = None,
        registry: Any | None = None,
    ):
        self.backend = backend
        self.scope = scope
        self.registry = registry

    def bind(self, scope: Scope, registry: Any | None = None) -> "MemoryService":
        """Return a service pinned to one tenant."""
        return MemoryService(self.backend, scope=scope, registry=registry or self.registry)

    # ---- enforcement ---------------------------------------------------

    def _require(self, permission: Permission) -> None:
        if self.scope is None or self.registry is None:
            return
        self.registry.require(self.scope, permission)

    def _guard_message(self, message: Any) -> None:
        """Reject a message that does not belong to the bound tenant.

        The bound scope names one group; the group names one platform chat. A
        message carrying any other chat id is a cross-tenant read attempt,
        whether it comes from a bug or a crafted payload.
        """
        if self.scope is None or self.registry is None:
            return
        chat_id = getattr(message, "chat_id", None)
        if chat_id is None:
            return
        group = self.registry.get_group(self.scope.installation_id, self.scope.group_id)
        if group.platform_chat_id is not None and int(chat_id) != int(group.platform_chat_id):
            raise ScopeViolation(
                f"message for chat {chat_id} does not belong to {self.scope}"
            )

    # ---- boundary ------------------------------------------------------

    async def context(self, message: Any, *, target_user_id: int | None = None, identity_lookup: bool = False, target_user_ids: tuple[int, ...] = ()):
        self._require(Permission.READ_MEMORY)
        self._guard_message(message)
        return await self.backend.context(
            message,
            target_user_id=target_user_id,
            identity_lookup=identity_lookup,
            target_user_ids=target_user_ids,
        )

    async def put(self, item: Any) -> str:
        self._require(Permission.WRITE_MEMORY)
        self._guard_item(item)
        return await self.backend.put(item)

    def _guard_item(self, item: Any) -> None:
        if self.scope is None or self.registry is None:
            return
        chat_id = getattr(item, "chat_id", None)
        if chat_id is None:
            return
        group = self.registry.get_group(self.scope.installation_id, self.scope.group_id)
        if group.platform_chat_id is not None and int(chat_id) != int(group.platform_chat_id):
            raise ScopeViolation(f"memory item for chat {chat_id} does not belong to {self.scope}")

    async def observe(self, message: Any, reply_text: str = "") -> None:
        self._require(Permission.WRITE_MEMORY)
        self._guard_message(message)
        await self.backend.observe(message, reply_text)

    async def record_message(self, message: Any, role: str = "user") -> None:
        self._require(Permission.WRITE_MEMORY)
        self._guard_message(message)
        await self.backend.record_message(message, role=role)

    async def metric(self, trace_id: str, kind: str, payload: dict[str, Any]) -> None:
        await self.backend.metric(trace_id, kind, payload)

    async def thread_context(self, message: Any, **kwargs: Any):
        self._require(Permission.READ_MEMORY)
        self._guard_message(message)
        return await self.backend.thread_context(message, **kwargs)

    def observe_sync_for_test(self, message: Any) -> None:
        asyncio.run(self.observe(message))

    def context_sync_for_test(self, message: Any):
        return asyncio.run(self.context(message))
