"""Tenancy value types: scope, group lifecycle, roles and permissions.

Every stateful operation in Zero is owned by an explicit scope. A bare
``chat_id`` is not ownership: the same Telegram chat can be reached by different
installations, and the same user holds different rights in different groups.
:class:`Scope` carries that ownership as one immutable value so it can be passed
down a call chain and asserted on, instead of being reconstructed from whatever
happens to be in range.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class GroupState(str, Enum):
    """Lifecycle of a group inside one installation."""

    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"
    REMOVAL_PENDING = "removal_pending"


#: Allowed lifecycle transitions. A group is discovered as PENDING and only an
#: explicit approval moves it to ACTIVE; nothing transitions out of ARCHIVED.
GROUP_TRANSITIONS: dict[GroupState, frozenset[GroupState]] = {
    GroupState.PENDING: frozenset({GroupState.ACTIVE, GroupState.DISABLED, GroupState.ARCHIVED}),
    GroupState.ACTIVE: frozenset({GroupState.DISABLED, GroupState.REMOVAL_PENDING, GroupState.ARCHIVED}),
    GroupState.DISABLED: frozenset({GroupState.ACTIVE, GroupState.REMOVAL_PENDING, GroupState.ARCHIVED}),
    GroupState.REMOVAL_PENDING: frozenset({GroupState.ARCHIVED, GroupState.ACTIVE}),
    GroupState.ARCHIVED: frozenset(),
}

#: States in which a group may serve traffic.
SERVING_STATES = frozenset({GroupState.ACTIVE})


class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class Permission(str, Enum):
    READ_GROUP = "read_group"
    SEND_MESSAGE = "send_message"
    READ_MEMORY = "read_memory"
    WRITE_MEMORY = "write_memory"
    MANAGE_SETTINGS = "manage_settings"
    MANAGE_MEMBERS = "manage_members"
    MANAGE_GROUP_STATE = "manage_group_state"
    USE_TOOLS = "use_tools"
    VIEW_USAGE = "view_usage"


_VIEWER = frozenset({Permission.READ_GROUP, Permission.VIEW_USAGE})
_MEMBER = _VIEWER | {Permission.SEND_MESSAGE, Permission.READ_MEMORY, Permission.WRITE_MEMORY, Permission.USE_TOOLS}
_ADMIN = _MEMBER | {Permission.MANAGE_SETTINGS, Permission.MANAGE_MEMBERS}
_OWNER = _ADMIN | {Permission.MANAGE_GROUP_STATE}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: _VIEWER,
    Role.MEMBER: _MEMBER,
    Role.ADMIN: _ADMIN,
    Role.OWNER: _OWNER,
}

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

#: Identifiers that must never be used as fallback owners. Using any of these
#: as an installation_id or group_id would create a fail-open path that
#: silently routes traffic to a wrong tenant.
FORBIDDEN_IDS = frozenset({"legacy", "default", "0", ""})

#: group_id values that encode a discovery candidate rather than an approved
#: group. They may appear during intake but must never reach stateful ownership.
_CANDIDATE_PREFIX = "candidate:"


class TenancyError(Exception):
    """Base class for tenancy violations."""


class PermissionDenied(TenancyError):
    """The scope does not hold the required permission."""


class ScopeViolation(TenancyError):
    """An operation was attempted outside the scope that owns the data."""


class GroupStateError(TenancyError):
    """An illegal group lifecycle transition, or traffic to a non-serving group."""


@dataclass(frozen=True, slots=True)
class Scope:
    """Immutable ownership of a single unit of work.

    ``installation_id`` and ``group_id`` identify the tenant. ``user_id`` is the
    acting principal. ``thread_id`` distinguishes forum topics inside one group,
    which are separate delivery destinations. ``request_id`` and ``trace_id``
    tie the work to one request for auditing.
    """

    installation_id: str
    group_id: str
    user_id: int | None = None
    thread_id: int | None = None
    request_id: str = ""
    trace_id: str = "-"

    def __post_init__(self) -> None:
        for field_name in ("installation_id", "group_id"):
            value = getattr(self, field_name)
            if not _ID.fullmatch(str(value)):
                raise ValueError(f"invalid {field_name}: identifiers must be non-empty and symbolic")
            if str(value).casefold() in FORBIDDEN_IDS:
                raise ValueError(f"{field_name} must not use a forbidden fallback: {value!r}")
            if str(value).startswith(_CANDIDATE_PREFIX):
                raise ValueError(f"{field_name} must not be a candidate placeholder: {value!r}")
        if self.user_id is not None and int(self.user_id) == 0:
            raise ValueError("user_id must be a real principal or None")

    @property
    def key(self) -> tuple[str, str]:
        """The tenant key that owns every scoped row."""
        return (self.installation_id, self.group_id)

    def with_thread(self, thread_id: int | None) -> "Scope":
        return Scope(self.installation_id, self.group_id, self.user_id, thread_id, self.request_id, self.trace_id)

    def for_user(self, user_id: int | None) -> "Scope":
        return Scope(self.installation_id, self.group_id, user_id, self.thread_id, self.request_id, self.trace_id)

    def owns(self, other: "Scope") -> bool:
        """Whether ``other`` belongs to the same tenant."""
        return self.key == other.key

    def assert_owns(self, other: "Scope") -> None:
        if not self.owns(other):
            raise ScopeViolation("cross-tenant access rejected")

    def __str__(self) -> str:
        return f"{self.installation_id}/{self.group_id}"


def permissions_for(role: Role) -> frozenset[Permission]:
    return ROLE_PERMISSIONS[role]


def can_transition(current: GroupState, target: GroupState) -> bool:
    return target in GROUP_TRANSITIONS[current]
