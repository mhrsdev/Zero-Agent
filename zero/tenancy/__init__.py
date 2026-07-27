"""Multi-group tenancy: explicit ownership for every stateful operation."""

from .models import (
    GROUP_TRANSITIONS,
    SERVING_STATES,
    GroupState,
    GroupStateError,
    Permission,
    PermissionDenied,
    Role,
    Scope,
    ScopeViolation,
    TenancyError,
    can_transition,
    permissions_for,
)
from .registry import GROUP_SETTING_KEYS, Group, TenancyRegistry

__all__ = [
    "GROUP_SETTING_KEYS",
    "GROUP_TRANSITIONS",
    "SERVING_STATES",
    "Group",
    "GroupState",
    "GroupStateError",
    "Permission",
    "PermissionDenied",
    "Role",
    "Scope",
    "ScopeViolation",
    "TenancyError",
    "TenancyRegistry",
    "can_transition",
    "permissions_for",
]
