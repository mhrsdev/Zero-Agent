"""Shared administration services used by TUI, listener and panel."""
from __future__ import annotations

from pathlib import Path

from .sessions import SessionRegistry
from .tenancy import GroupState, GroupStateError, Role, Scope
from .tenancy.registry import QUOTA_PERIODS, Group, TenancyRegistry


class GroupAdminError(ValueError):
    pass


def _group_id(chat_id: int) -> str:
    value = int(chat_id)
    if value == 0:
        raise GroupAdminError("Telegram chat id must be non-zero")
    return f"telegram:{value}"


class GroupAdminService:
    def __init__(self, registry: TenancyRegistry, *, installation_id: str, owner_user_id: int):
        self.registry = registry
        self.installation_id = str(installation_id)
        self.owner_user_id = int(owner_user_id)
        if self.owner_user_id == 0:
            raise GroupAdminError("owner user id must be non-zero")

    def _scope(self, group_id: str) -> Scope:
        scope = Scope(self.installation_id, group_id, self.owner_user_id)
        self.registry.add_member(scope, self.owner_user_id, Role.OWNER)
        return scope

    def _resolve(self, chat_id: int) -> tuple[Group, Scope]:
        try:
            scope = self.registry.resolve_scope(self.installation_id, platform_chat_id=int(chat_id), user_id=self.owner_user_id)
            group = self.registry.get_group(scope.installation_id, scope.group_id)
        except GroupStateError as exc:
            raise GroupAdminError("group is not registered") from exc
        self.registry.add_member(scope, self.owner_user_id, Role.OWNER)
        return group, scope

    def add_group(self, chat_id: int, *, title: str = "") -> Group:
        group_id = _group_id(chat_id)
        group = self.registry.discover_group(
            self.installation_id, group_id, platform="telegram", platform_chat_id=int(chat_id), title=str(title or "").strip()[:200],
        )
        if group.state is GroupState.ARCHIVED:
            raise GroupAdminError("archived group cannot be reactivated")
        scope = self._scope(group.group_id)
        if group.state is not GroupState.ACTIVE:
            group = self.registry.set_group_state(scope, GroupState.ACTIVE)
        return group

    def disable_group(self, chat_id: int) -> Group:
        group, scope = self._resolve(chat_id)
        if group.state is GroupState.DISABLED:
            return group
        if group.state not in {GroupState.ACTIVE, GroupState.PENDING}:
            raise GroupAdminError(f"group cannot be disabled from {group.state.value}")
        return self.registry.set_group_state(scope, GroupState.DISABLED)

    def remove_group(self, chat_id: int, *, confirmed: bool = False) -> Group:
        if not confirmed:
            raise GroupAdminError("group removal requires explicit confirmation")
        group, scope = self._resolve(chat_id)
        if group.state is GroupState.ARCHIVED:
            return group
        if group.state in {GroupState.ACTIVE, GroupState.DISABLED}:
            group = self.registry.set_group_state(scope, GroupState.REMOVAL_PENDING)
        if group.state in {GroupState.PENDING, GroupState.REMOVAL_PENDING}:
            group = self.registry.set_group_state(scope, GroupState.ARCHIVED)
        return group

    def set_reply_limits(self, chat_id: int, limits: dict[str, int]) -> dict[str, int]:
        unknown = set(limits) - set(QUOTA_PERIODS)
        if unknown or not limits:
            raise GroupAdminError("limits must use hour, day, week and month periods")
        _group, scope = self._resolve(chat_id)
        self.registry.set_quotas(scope, "human_replies", limits)
        return self.registry.quotas(scope, "human_replies")


def group_is_allowed(registry: TenancyRegistry, installation_id: str, chat_id: int, *, legacy_allowed: bool) -> bool:
    try:
        scope = registry.resolve_scope(installation_id, platform_chat_id=int(chat_id))
        return registry.get_group(scope.installation_id, scope.group_id).serving
    except GroupStateError:
        return bool(legacy_allowed)


def active_group_chat_ids(registry: TenancyRegistry, installation_id: str, *, legacy_ids: list[int]) -> list[int]:
    groups = registry.groups(installation_id)
    if not groups:
        return sorted({int(value) for value in legacy_ids})
    return sorted({int(group.platform_chat_id) for group in groups if group.serving and group.platform_chat_id is not None})


def resolve_listener_session_path(fallback: str | Path, *, session_root: str | Path | None = None) -> Path:
    return SessionRegistry(session_root).resolve_active_path(fallback)
