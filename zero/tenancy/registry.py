"""Persistent registry of installations, groups, memberships and per-group policy.

Every table here is keyed by ``(installation_id, group_id)``. That composite key
is the enforcement point: a query that forgets it cannot accidentally read
another tenant's rows, because there is no unscoped accessor on this class.

Group discovery is deliberately two-step. A group Zero is added to arrives as
:attr:`GroupState.PENDING` and serves no traffic until someone with
:attr:`Permission.MANAGE_GROUP_STATE` approves it, so being added to a chat is
never by itself consent to operate in it.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..paths import expand
from .models import (
    GroupState,
    GroupStateError,
    Permission,
    PermissionDenied,
    Role,
    Scope,
    can_transition,
    permissions_for,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS installations(
    installation_id TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS groups(
    installation_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'telegram',
    platform_chat_id INTEGER,
    title TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    discovered_at REAL NOT NULL,
    approved_at REAL,
    PRIMARY KEY(installation_id, group_id),
    FOREIGN KEY(installation_id) REFERENCES installations(installation_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS groups_platform_chat_idx
    ON groups(installation_id, platform, platform_chat_id);
CREATE TABLE IF NOT EXISTS memberships(
    installation_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    added_at REAL NOT NULL,
    PRIMARY KEY(installation_id, group_id, user_id)
);
CREATE TABLE IF NOT EXISTS group_settings(
    installation_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(installation_id, group_id, key)
);
CREATE TABLE IF NOT EXISTS group_quotas(
    installation_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    resource TEXT NOT NULL,
    period TEXT NOT NULL,
    limit_value INTEGER NOT NULL,
    PRIMARY KEY(installation_id, group_id, resource, period)
);
CREATE TABLE IF NOT EXISTS usage_records(
    installation_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    resource TEXT NOT NULL,
    period TEXT NOT NULL,
    bucket TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY(installation_id, group_id, resource, period, bucket)
);
CREATE TABLE IF NOT EXISTS identity_history(
    installation_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    observed_at REAL NOT NULL,
    PRIMARY KEY(installation_id, group_id, user_id, label)
);
"""

#: Settings a group owns independently of every other group.
GROUP_SETTING_KEYS = frozenset({
    "persona", "provider_profile", "tool_policy", "web_search_enabled", "memory_enabled",
})

QUOTA_PERIODS = ("hour", "day", "week", "month")


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    blocked_period: str | None
    usage: dict[str, int]
    limits: dict[str, int]



@dataclass(frozen=True)
class Group:
    installation_id: str
    group_id: str
    platform: str
    platform_chat_id: int | None
    title: str
    state: GroupState

    @property
    def serving(self) -> bool:
        return self.state is GroupState.ACTIVE


class TenancyRegistry:
    """Scoped access to installations, groups, members, settings and quotas."""

    def __init__(self, path: str | Path):
        self.path = expand(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as db:
            db.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # ---- installations -------------------------------------------------

    def create_installation(self, installation_id: str, *, label: str = "") -> None:
        with self._conn() as db:
            db.execute(
                "INSERT OR IGNORE INTO installations(installation_id,label,created_at) VALUES(?,?,?)",
                (installation_id, label, time.time()),
            )

    def installations(self) -> list[str]:
        with self._conn() as db:
            return [r["installation_id"] for r in db.execute("SELECT installation_id FROM installations ORDER BY installation_id")]

    # ---- group lifecycle -----------------------------------------------

    def discover_group(
        self, installation_id: str, group_id: str, *,
        platform: str = "telegram", platform_chat_id: int | None = None, title: str = "",
    ) -> Group:
        """Record a newly seen group as PENDING. Never auto-approves."""
        self.create_installation(installation_id)
        with self._conn() as db:
            db.execute(
                "INSERT OR IGNORE INTO groups(installation_id,group_id,platform,platform_chat_id,title,state,discovered_at) VALUES(?,?,?,?,?,?,?)",
                (installation_id, group_id, platform, platform_chat_id, title, GroupState.PENDING.value, time.time()),
            )
        return self.get_group(installation_id, group_id)

    def get_group(self, installation_id: str, group_id: str) -> Group:
        with self._conn() as db:
            row = db.execute(
                "SELECT * FROM groups WHERE installation_id=? AND group_id=?",
                (installation_id, group_id),
            ).fetchone()
        if row is None:
            raise GroupStateError(f"unknown group: {installation_id}/{group_id}")
        return Group(
            row["installation_id"], row["group_id"], row["platform"],
            row["platform_chat_id"], row["title"], GroupState(row["state"]),
        )

    def groups(self, installation_id: str, *, state: GroupState | None = None) -> list[Group]:
        query = "SELECT * FROM groups WHERE installation_id=?"
        params: list[Any] = [installation_id]
        if state is not None:
            query += " AND state=?"
            params.append(state.value)
        with self._conn() as db:
            rows = db.execute(query + " ORDER BY group_id", params).fetchall()
        return [
            Group(r["installation_id"], r["group_id"], r["platform"], r["platform_chat_id"], r["title"], GroupState(r["state"]))
            for r in rows
        ]

    def set_group_state(self, scope: Scope, target: GroupState, *, actor_id: int | None = None) -> Group:
        """Transition a group, enforcing both the actor's rights and the lifecycle."""
        actor = actor_id if actor_id is not None else scope.user_id
        self.require(scope.for_user(actor), Permission.MANAGE_GROUP_STATE)
        current = self.get_group(scope.installation_id, scope.group_id)
        if current.state is target:
            return current
        if not can_transition(current.state, target):
            raise GroupStateError(f"illegal transition {current.state.value} -> {target.value}")
        approved = time.time() if target is GroupState.ACTIVE else None
        with self._conn() as db:
            db.execute(
                "UPDATE groups SET state=?, approved_at=COALESCE(?,approved_at) WHERE installation_id=? AND group_id=?",
                (target.value, approved, scope.installation_id, scope.group_id),
            )
        return self.get_group(scope.installation_id, scope.group_id)

    def require_serving(self, scope: Scope) -> Group:
        """Reject traffic to a group that is not approved and active."""
        group = self.get_group(scope.installation_id, scope.group_id)
        if not group.serving:
            raise GroupStateError(f"group {scope} is {group.state.value}, not serving")
        return group

    # ---- membership and permissions ------------------------------------

    def add_member(self, scope: Scope, user_id: int, role: Role) -> None:
        with self._conn() as db:
            db.execute(
                "INSERT INTO memberships(installation_id,group_id,user_id,role,added_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(installation_id,group_id,user_id) DO UPDATE SET role=excluded.role",
                (scope.installation_id, scope.group_id, int(user_id), Role(role).value, time.time()),
            )

    def remove_member(self, scope: Scope, user_id: int) -> None:
        with self._conn() as db:
            db.execute(
                "DELETE FROM memberships WHERE installation_id=? AND group_id=? AND user_id=?",
                (scope.installation_id, scope.group_id, int(user_id)),
            )

    def role_of(self, scope: Scope, user_id: int | None = None) -> Role | None:
        target = scope.user_id if user_id is None else user_id
        if target is None:
            return None
        with self._conn() as db:
            row = db.execute(
                "SELECT role FROM memberships WHERE installation_id=? AND group_id=? AND user_id=?",
                (scope.installation_id, scope.group_id, int(target)),
            ).fetchone()
        return Role(row["role"]) if row else None

    def members(self, scope: Scope) -> dict[int, Role]:
        with self._conn() as db:
            rows = db.execute(
                "SELECT user_id, role FROM memberships WHERE installation_id=? AND group_id=? ORDER BY user_id",
                (scope.installation_id, scope.group_id),
            ).fetchall()
        return {int(r["user_id"]): Role(r["role"]) for r in rows}

    def permissions(self, scope: Scope, user_id: int | None = None) -> frozenset[Permission]:
        role = self.role_of(scope, user_id)
        return permissions_for(role) if role else frozenset()

    def has(self, scope: Scope, permission: Permission, user_id: int | None = None) -> bool:
        return permission in self.permissions(scope, user_id)

    def require(self, scope: Scope, permission: Permission, user_id: int | None = None) -> None:
        if not self.has(scope, permission, user_id):
            target = scope.user_id if user_id is None else user_id
            raise PermissionDenied(f"user {target} lacks {permission.value} in {scope}")

    # ---- per-group settings --------------------------------------------

    def set_setting(self, scope: Scope, key: str, value: Any, *, actor_id: int | None = None) -> None:
        if key not in GROUP_SETTING_KEYS:
            raise ValueError(f"unknown group setting: {key}")
        self.require(scope.for_user(actor_id if actor_id is not None else scope.user_id), Permission.MANAGE_SETTINGS)
        with self._conn() as db:
            db.execute(
                "INSERT INTO group_settings(installation_id,group_id,key,value_json,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(installation_id,group_id,key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                (scope.installation_id, scope.group_id, key, json.dumps(value, ensure_ascii=False), time.time()),
            )

    def get_setting(self, scope: Scope, key: str, default: Any = None) -> Any:
        with self._conn() as db:
            row = db.execute(
                "SELECT value_json FROM group_settings WHERE installation_id=? AND group_id=? AND key=?",
                (scope.installation_id, scope.group_id, key),
            ).fetchone()
        return json.loads(row["value_json"]) if row else default

    def settings(self, scope: Scope) -> dict[str, Any]:
        with self._conn() as db:
            rows = db.execute(
                "SELECT key, value_json FROM group_settings WHERE installation_id=? AND group_id=?",
                (scope.installation_id, scope.group_id),
            ).fetchall()
        return {r["key"]: json.loads(r["value_json"]) for r in rows}

    # ---- quotas and usage ----------------------------------------------

    @staticmethod
    def _validate_quota(resource: str, period: str, limit: int | None = None) -> tuple[str, str, int | None]:
        resource = str(resource or "").strip()
        if not resource or len(resource) > 128:
            raise ValueError("quota resource must be non-empty and at most 128 characters")
        if period not in QUOTA_PERIODS:
            raise ValueError(f"unsupported quota period: {period}")
        parsed = None if limit is None else int(limit)
        if parsed is not None and parsed < 0:
            raise ValueError("quota limit must be zero or greater")
        return resource, period, parsed

    def set_quota(self, scope: Scope, resource: str, limit: int, *, period: str = "day") -> None:
        resource, period, parsed = self._validate_quota(resource, period, limit)
        self.get_group(scope.installation_id, scope.group_id)
        with self._conn() as db:
            db.execute(
                "INSERT INTO group_quotas(installation_id,group_id,resource,period,limit_value) VALUES(?,?,?,?,?) "
                "ON CONFLICT(installation_id,group_id,resource,period) DO UPDATE SET limit_value=excluded.limit_value",
                (scope.installation_id, scope.group_id, resource, period, parsed),
            )

    def set_quotas(self, scope: Scope, resource: str, limits: dict[str, int]) -> None:
        self.get_group(scope.installation_id, scope.group_id)
        parsed = [self._validate_quota(resource, period, limit) for period, limit in limits.items()]
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                for item_resource, period, limit in parsed:
                    db.execute(
                        "INSERT INTO group_quotas(installation_id,group_id,resource,period,limit_value) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(installation_id,group_id,resource,period) DO UPDATE SET limit_value=excluded.limit_value",
                        (scope.installation_id, scope.group_id, item_resource, period, limit),
                    )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def quotas(self, scope: Scope, resource: str) -> dict[str, int]:
        resource = str(resource or "").strip()
        with self._conn() as db:
            rows = db.execute(
                "SELECT period,limit_value FROM group_quotas WHERE installation_id=? AND group_id=? AND resource=?",
                (scope.installation_id, scope.group_id, resource),
            ).fetchall()
        values = {row["period"]: int(row["limit_value"]) for row in rows}
        return {period: values[period] for period in QUOTA_PERIODS if period in values}

    def get_quota(self, scope: Scope, resource: str, *, period: str = "day") -> int | None:
        with self._conn() as db:
            row = db.execute(
                "SELECT limit_value FROM group_quotas WHERE installation_id=? AND group_id=? AND resource=? AND period=?",
                (scope.installation_id, scope.group_id, resource, period),
            ).fetchone()
        return int(row["limit_value"]) if row else None

    @staticmethod
    def _bucket(period: str, now: float | None = None) -> str:
        if period not in QUOTA_PERIODS:
            raise ValueError(f"unsupported quota period: {period}")
        stamp = time.gmtime(now if now is not None else time.time())
        if period == "hour":
            return time.strftime("%Y-%m-%dT%H", stamp)
        if period == "day":
            return time.strftime("%Y-%m-%d", stamp)
        if period == "week":
            iso_year, iso_week, _ = date(stamp.tm_year, stamp.tm_mon, stamp.tm_mday).isocalendar()
            return f"{iso_year:04d}-W{iso_week:02d}"
        return time.strftime("%Y-%m", stamp)

    def consume_quotas(self, scope: Scope, resource: str, *, amount: int = 1, now: float | None = None) -> QuotaDecision:
        amount = int(amount)
        if amount <= 0:
            raise ValueError("quota amount must be positive")
        limits = self.quotas(scope, resource)
        if not limits:
            return QuotaDecision(True, None, {}, {})
        buckets = {period: self._bucket(period, now) for period in limits}
        usage: dict[str, int] = {}
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                for period in QUOTA_PERIODS:
                    if period not in limits:
                        continue
                    row = db.execute(
                        "SELECT used FROM usage_records WHERE installation_id=? AND group_id=? AND resource=? AND period=? AND bucket=?",
                        (scope.installation_id, scope.group_id, resource, period, buckets[period]),
                    ).fetchone()
                    usage[period] = int(row["used"]) if row else 0
                blocked = next((period for period in QUOTA_PERIODS if period in limits and usage[period] + amount > limits[period]), None)
                if blocked is not None:
                    db.execute("ROLLBACK")
                    return QuotaDecision(False, blocked, usage, limits)
                for period in QUOTA_PERIODS:
                    if period not in limits:
                        continue
                    usage[period] += amount
                    db.execute(
                        "INSERT INTO usage_records(installation_id,group_id,resource,period,bucket,used,updated_at) VALUES(?,?,?,?,?,?,?) "
                        "ON CONFLICT(installation_id,group_id,resource,period,bucket) DO UPDATE SET used=excluded.used,updated_at=excluded.updated_at",
                        (scope.installation_id, scope.group_id, resource, period, buckets[period], usage[period], time.time()),
                    )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return QuotaDecision(True, None, usage, limits)

    def refund_quotas(self, scope: Scope, resource: str, *, amount: int = 1, now: float | None = None) -> None:
        amount = max(0, int(amount))
        limits = self.quotas(scope, resource)
        if not limits or amount == 0:
            return
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                for period in limits:
                    bucket = self._bucket(period, now)
                    db.execute(
                        "UPDATE usage_records SET used=MAX(0,used-?),updated_at=? WHERE installation_id=? AND group_id=? AND resource=? AND period=? AND bucket=?",
                        (amount, time.time(), scope.installation_id, scope.group_id, resource, period, bucket),
                    )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def consume(self, scope: Scope, resource: str, *, amount: int = 1, period: str = "day", now: float | None = None) -> tuple[bool, int, int | None]:
        """Consume quota for one group. Returns ``(allowed, used, limit)``.

        Usage is stored per ``(installation, group, resource, period, bucket)``,
        so one group exhausting a resource cannot affect another.
        """
        limit = self.get_quota(scope, resource, period=period)
        bucket = self._bucket(period, now)
        with self._conn() as db:
            row = db.execute(
                "SELECT used FROM usage_records WHERE installation_id=? AND group_id=? AND resource=? AND period=? AND bucket=?",
                (scope.installation_id, scope.group_id, resource, period, bucket),
            ).fetchone()
            used = int(row["used"]) if row else 0
            if limit is not None and used + amount > limit:
                return False, used, limit
            used += amount
            db.execute(
                "INSERT INTO usage_records(installation_id,group_id,resource,period,bucket,used,updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(installation_id,group_id,resource,period,bucket) DO UPDATE SET used=excluded.used,updated_at=excluded.updated_at",
                (scope.installation_id, scope.group_id, resource, period, bucket, used, time.time()),
            )
        return True, used, limit

    def usage(self, scope: Scope, resource: str, *, period: str = "day", now: float | None = None) -> int:
        with self._conn() as db:
            row = db.execute(
                "SELECT used FROM usage_records WHERE installation_id=? AND group_id=? AND resource=? AND period=? AND bucket=?",
                (scope.installation_id, scope.group_id, resource, period, self._bucket(period, now)),
            ).fetchone()
        return int(row["used"]) if row else 0

    # ---- identity history ----------------------------------------------

    def record_identity(self, scope: Scope, user_id: int, label: str) -> None:
        """Remember a label a user has used in this group, scoped to this group."""
        if not str(label or "").strip():
            return
        with self._conn() as db:
            db.execute(
                "INSERT OR IGNORE INTO identity_history(installation_id,group_id,user_id,label,observed_at) VALUES(?,?,?,?,?)",
                (scope.installation_id, scope.group_id, int(user_id), str(label), time.time()),
            )

    def identity_history(self, scope: Scope, user_id: int) -> list[str]:
        with self._conn() as db:
            rows = db.execute(
                "SELECT label FROM identity_history WHERE installation_id=? AND group_id=? AND user_id=? ORDER BY observed_at",
                (scope.installation_id, scope.group_id, int(user_id)),
            ).fetchall()
        return [r["label"] for r in rows]

    def resolve_scope(
        self, installation_id: str, *, platform_chat_id: int, user_id: int | None = None,
        thread_id: int | None = None, request_id: str = "", trace_id: str = "-",
        platform: str = "telegram",
    ) -> Scope:
        """Map a platform chat id to the scope that owns it."""
        with self._conn() as db:
            row = db.execute(
                "SELECT group_id FROM groups WHERE installation_id=? AND platform=? AND platform_chat_id=?",
                (installation_id, platform, int(platform_chat_id)),
            ).fetchone()
        if row is None:
            raise GroupStateError(f"no group registered for chat {platform_chat_id} in {installation_id}")
        return Scope(installation_id, row["group_id"], user_id, thread_id, request_id, trace_id)
