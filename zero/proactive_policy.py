from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    reason: str
    retry_at: int | None = None


class PolicyEngine:
    """Deterministic, persistent pre-transport policy gate."""

    def __init__(self, store):
        self.store = store
        with store._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS proactive_policy_events(
                id INTEGER PRIMARY KEY, candidate_id TEXT, chat_id INTEGER,
                subject_user_id INTEGER, topic TEXT, event TEXT,
                action TEXT, reason TEXT, created_at INTEGER)"""
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(proactive_policy_events)")}
            for name in ("action", "reason"):
                if name not in columns:
                    conn.execute(f"ALTER TABLE proactive_policy_events ADD COLUMN {name} TEXT")

    @staticmethod
    def _hours() -> tuple[int, int] | None:
        try:
            start = int(os.getenv("ZERO_PROACTIVE_ALLOWED_HOUR_START", "9"))
            end = int(os.getenv("ZERO_PROACTIVE_ALLOWED_HOUR_END", "22"))
        except ValueError:
            return None
        if not (0 <= start <= 23 and 1 <= end <= 24):
            return None
        return start, end

    @staticmethod
    def _local_hour(row, now: int) -> int:
        candidates = (
            row.get("user_timezone"), row.get("chat_timezone"),
            os.getenv("ZERO_PROACTIVE_FALLBACK_TIMEZONE"), os.getenv("TZ"), "UTC",
        )
        for name in candidates:
            if not name:
                continue
            try:
                return datetime.fromtimestamp(now, ZoneInfo(str(name))).hour
            except (ZoneInfoNotFoundError, ValueError, OSError):
                continue
        return datetime.fromtimestamp(now, ZoneInfo("UTC")).hour

    def decide(self, row, now: int | None = None, *, local_hour: int | None = None) -> PolicyDecision:
        now = int(now if now is not None else time.time())
        required = ("id", "chat_id", "subject_user_id", "topic_summary")
        if not all(key in row for key in required) or not str(row.get("topic_summary") or "").strip():
            return PolicyDecision("block", "invalid_candidate")

        with self.store._conn() as conn:
            has_preferences = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='proactive_feedback_preferences'").fetchone()
            if has_preferences:
                pref = conn.execute("SELECT proactive_enabled FROM proactive_feedback_preferences WHERE chat_id=? AND subject_user_id=?", (row["chat_id"], row["subject_user_id"])).fetchone()
                if pref and not bool(pref[0]):
                    return PolicyDecision("block", "user_opt_out")

        hours = self._hours()
        if hours is None:
            return PolicyDecision("postpone", "invalid_quiet_hours", now + 3600)
        start, end = hours
        hour = int(local_hour if local_hour is not None else self._local_hour(row, now))
        allowed = True if (start == 0 and end == 24) else (
            start <= hour < end if start < end else hour >= start or hour < end
        )
        if not allowed:
            delta = (start - hour) % 24 or 24
            return PolicyDecision("postpone", "quiet_hours", now + delta * 3600)

        with self.store._conn() as conn:
            if conn.execute(
                "SELECT 1 FROM proactive_policy_events WHERE subject_user_id=? AND event='sent' AND created_at>? LIMIT 1",
                (row["subject_user_id"], now - 72 * 3600),
            ).fetchone():
                return PolicyDecision("block", "user_rate_limit")
            if conn.execute(
                "SELECT count(*) FROM proactive_policy_events WHERE chat_id=? AND event='sent' AND created_at>?",
                (row["chat_id"], now - 24 * 3600),
            ).fetchone()[0] >= 2:
                return PolicyDecision("block", "group_rate_limit")
            latest = conn.execute(
                """SELECT event FROM proactive_policy_events
                WHERE chat_id=? AND subject_user_id=? AND topic=?
                AND event IN ('open','resolved','cancelled')
                ORDER BY created_at DESC,id DESC LIMIT 1""",
                (row["chat_id"], row["subject_user_id"], row["topic_summary"]),
            ).fetchone()
            if latest and latest[0] == "open":
                return PolicyDecision("block", "topic_open")
        return PolicyDecision("allow", "allowed")

    def record(self, row, event: str, now: int | None = None) -> None:
        self._insert(row, event=event, action=None, reason=None, now=now)

    def record_decision(self, row, decision: PolicyDecision, now: int | None = None) -> None:
        self._insert(row, event="decision", action=decision.action, reason=decision.reason, now=now)

    def _insert(self, row, *, event, action, reason, now) -> None:
        with self.store._conn() as conn:
            conn.execute(
                """INSERT INTO proactive_policy_events(
                candidate_id,chat_id,subject_user_id,topic,event,action,reason,created_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (
                    row.get("id"), row.get("chat_id"), row.get("subject_user_id"),
                    str(row.get("topic_summary") or "")[:120], event, action, reason,
                    int(now if now is not None else time.time()),
                ),
            )
