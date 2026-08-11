"""Safe action layer used by the interactive Zero TUI."""
from __future__ import annotations

from collections.abc import Callable

from zero.admin import GroupAdminError, GroupAdminService
from zero.sessions import (
    LoginOutcome,
    LoginAdapter,
    SessionRecord,
    SessionRegistry,
    SessionRegistryError,
    login_session,
)
from zero.tenancy.registry import Group


class TUIAdminError(RuntimeError):
    """A display-safe administrative failure."""


class TUIAdmin:
    def __init__(self, sessions: SessionRegistry, groups: GroupAdminService) -> None:
        self.sessions = sessions
        self.groups = groups

    def add_session(self, session_id: str, label: str = "") -> SessionRecord:
        try:
            return self.sessions.add(session_id, label=label)
        except SessionRegistryError as exc:
            raise TUIAdminError(str(exc)) from exc

    def activate_session(self, session_id: str) -> SessionRecord:
        try:
            return self.sessions.activate(session_id)
        except SessionRegistryError as exc:
            raise TUIAdminError(str(exc)) from exc

    def delete_session(self, session_id: str, *, confirmation: str) -> None:
        if confirmation != f"DELETE {session_id}":
            raise TUIAdminError("confirmation did not match")
        try:
            self.sessions.remove(session_id, confirmed=True)
        except SessionRegistryError as exc:
            raise TUIAdminError(str(exc)) from exc

    async def login_session(
        self,
        session_id: str,
        *,
        adapter: LoginAdapter,
        api_id: int,
        api_hash: str,
        phone: str,
        code_provider: Callable[[], str],
        password_provider: Callable[[], str],
    ) -> LoginOutcome:
        try:
            return await login_session(
                self.sessions,
                session_id,
                adapter=adapter,
                api_id=api_id,
                api_hash=api_hash,
                phone=phone,
                code_provider=code_provider,
                password_provider=password_provider,
            )
        except SessionRegistryError as exc:
            raise TUIAdminError(str(exc)) from exc

    def add_group(self, chat_id: int, title: str = "") -> Group:
        try:
            return self.groups.add_group(int(chat_id), title=title)
        except (GroupAdminError, ValueError) as exc:
            raise TUIAdminError(str(exc)) from exc

    def disable_group(self, chat_id: int, *, confirmation: str) -> Group:
        if confirmation != f"DISABLE {int(chat_id)}":
            raise TUIAdminError("confirmation did not match")
        try:
            return self.groups.disable_group(int(chat_id))
        except (GroupAdminError, ValueError) as exc:
            raise TUIAdminError(str(exc)) from exc

    def remove_group(self, chat_id: int, *, confirmation: str) -> Group:
        if confirmation != f"REMOVE {int(chat_id)}":
            raise TUIAdminError("confirmation did not match")
        try:
            return self.groups.remove_group(int(chat_id), confirmed=True)
        except (GroupAdminError, ValueError) as exc:
            raise TUIAdminError(str(exc)) from exc

    def set_group_limits(self, chat_id: int, *, hour: int, day: int, week: int, month: int) -> dict[str, int]:
        limits = {"hour": int(hour), "day": int(day), "week": int(week), "month": int(month)}
        try:
            self.groups.set_reply_limits(int(chat_id), limits)
        except (GroupAdminError, ValueError) as exc:
            raise TUIAdminError(str(exc)) from exc
        return limits
