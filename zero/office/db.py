from __future__ import annotations

from contextlib import contextmanager

import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from typing import Any


TERMINAL_STATUSES = {"completed", "failed", "cancelled", "expired"}
ACTIVE_STATUSES = {"planning", "processing", "validating_output", "rendering", "reviewing", "repairing"}
TRANSITIONS: dict[str, set[str]] = {
    "received": {"validating", "cancelled", "failed"},
    "validating": {"quota_reserved", "cancelled", "failed"},
    "quota_reserved": {"queued", "cancelled", "failed"},
    "queued": {"planning", "processing", "cancelled", "expired", "failed"},
    "planning": {"quota_reserved", "processing", "queued", "cancelled", "failed", "expired"},
    "processing": {"validating_output", "repairing", "queued", "cancelled", "failed", "expired"},
    "validating_output": {"rendering", "reviewing", "repairing", "completed", "failed"},
    "rendering": {"reviewing", "repairing", "completed", "failed"},
    "reviewing": {"repairing", "completed", "failed"},
    "repairing": {"queued", "processing", "validating_output", "failed"},
    "completed": set(), "failed": set(), "cancelled": set(), "expired": set(),
}


class QuotaExceeded(RuntimeError):
    pass


class StateTransitionError(RuntimeError):
    pass


OFFICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS office_jobs (
  id TEXT PRIMARY KEY,
  trace_id TEXT NOT NULL,
  account_scope TEXT NOT NULL DEFAULT 'telegram',
  installation_id TEXT NOT NULL,
  group_id TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  chat_id INTEGER NOT NULL,
  thread_id INTEGER,
  message_id INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('received','validating','quota_reserved','queued','planning','processing','validating_output','rendering','reviewing','repairing','completed','failed','cancelled','expired')),
  operation_type TEXT NOT NULL,
  office_format TEXT NOT NULL CHECK(office_format IN ('docx','xlsx','pptx')),
  request_text TEXT NOT NULL DEFAULT '',
  input_filename TEXT NOT NULL DEFAULT '',
  input_path TEXT NOT NULL DEFAULT '',
  output_path TEXT NOT NULL DEFAULT '',
  result_text TEXT NOT NULL DEFAULT '',
  preview_paths_json TEXT NOT NULL DEFAULT '[]',
  plan_json TEXT NOT NULL DEFAULT '{}',
  detected_mime TEXT NOT NULL DEFAULT '',
  input_size_bytes INTEGER NOT NULL DEFAULT 0,
  uncompressed_size_bytes INTEGER NOT NULL DEFAULT 0,
  extracted_characters INTEGER NOT NULL DEFAULT 0,
  quota_date TEXT NOT NULL,
  quota_reservation_id TEXT NOT NULL,
  quota_state TEXT NOT NULL CHECK(quota_state IN ('reserved','committed','refunded','unlimited')),
  attempt_count INTEGER NOT NULL DEFAULT 0,
  repair_count INTEGER NOT NULL DEFAULT 0,
  lease_owner TEXT,
  lease_expires_at INTEGER,
  heartbeat_at INTEGER,
  error_code TEXT NOT NULL DEFAULT '',
  error_message_safe TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  started_at INTEGER,
  completed_at INTEGER,
  UNIQUE(account_scope, chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_office_jobs_queue ON office_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_office_jobs_lease ON office_jobs(status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_office_jobs_user_active ON office_jobs(user_id, status, lease_expires_at);
-- Scope indexes are created in migrate() after ALTER TABLE, not here,
-- because adding them in OFFICE_SCHEMA would fail on pre-P0-2 DBs that
-- lack installation_id/group_id columns.

CREATE TABLE IF NOT EXISTS office_quota_usage (
  installation_id TEXT NOT NULL,
  group_id TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  quota_date TEXT NOT NULL,
  jobs_reserved INTEGER NOT NULL DEFAULT 0 CHECK(jobs_reserved >= 0),
  jobs_committed INTEGER NOT NULL DEFAULT 0 CHECK(jobs_committed >= 0),
  characters_reserved INTEGER NOT NULL DEFAULT 0 CHECK(characters_reserved >= 0),
  characters_committed INTEGER NOT NULL DEFAULT 0 CHECK(characters_committed >= 0),
  last_job_id TEXT,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(installation_id, group_id, user_id, quota_date)
);

CREATE TABLE IF NOT EXISTS office_job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  reason TEXT NOT NULL DEFAULT '',
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL,
  FOREIGN KEY(job_id) REFERENCES office_jobs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_office_job_events_job ON office_job_events(job_id, id);

CREATE TABLE IF NOT EXISTS office_delivery_outbox (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL UNIQUE,
  outbound_key TEXT NOT NULL UNIQUE,
  installation_id TEXT NOT NULL,
  group_id TEXT NOT NULL,
  thread_id INTEGER,
  destination_chat_id INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('reserved','sent','retryable_failed','permanent_failed','ambiguous','cancelled')),
  attempt_count INTEGER NOT NULL DEFAULT 1,
  lease_owner TEXT,
  lease_expires_at INTEGER,
  telegram_message_id INTEGER,
  error_code TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  FOREIGN KEY(job_id) REFERENCES office_jobs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_office_outbox_status ON office_delivery_outbox(status, lease_expires_at);
-- Scope index created in migrate() after ALTER TABLE.
CREATE TABLE IF NOT EXISTS office_metrics (
  name TEXT PRIMARY KEY,
  value INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);
"""


class OfficeRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @contextmanager
    def _session_ctx(self):
        """Transaction scope that also closes the connection (sqlite3 `with` does not)."""
        conn = self.connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()


    def migrate(self) -> None:
        with self._session_ctx() as conn:
            conn.executescript(OFFICE_SCHEMA)
            # P0-2: add ownership columns to pre-existing databases.
            job_cols = {r[1] for r in conn.execute("PRAGMA table_info(office_jobs)")}
            for col, decl in (
                ("installation_id", "TEXT NOT NULL DEFAULT ''"),
                ("group_id", "TEXT NOT NULL DEFAULT ''"),
                ("thread_id", "INTEGER"),
            ):
                if col not in job_cols:
                    conn.execute(f"ALTER TABLE office_jobs ADD COLUMN {col} {decl}")
            quota_cols = {r[1] for r in conn.execute("PRAGMA table_info(office_quota_usage)")}
            for col, decl in (
                ("installation_id", "TEXT NOT NULL DEFAULT ''"),
                ("group_id", "TEXT NOT NULL DEFAULT ''"),
            ):
                if col not in quota_cols:
                    conn.execute(f"ALTER TABLE office_quota_usage ADD COLUMN {col} {decl}")
            outbox_cols = {r[1] for r in conn.execute("PRAGMA table_info(office_delivery_outbox)")}
            for col, decl in (
                ("installation_id", "TEXT NOT NULL DEFAULT ''"),
                ("group_id", "TEXT NOT NULL DEFAULT ''"),
                ("thread_id", "INTEGER"),
                ("destination_chat_id", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if col not in outbox_cols:
                    conn.execute(f"ALTER TABLE office_delivery_outbox ADD COLUMN {col} {decl}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_office_jobs_scope ON office_jobs(installation_id, group_id, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_office_outbox_scope ON office_delivery_outbox(installation_id, group_id)")

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def reserve_and_create(
        self, *, job_id: str, trace_id: str, user_id: int, chat_id: int, message_id: int,
        operation_type: str, office_format: str, request_text: str, input_filename: str,
        input_path: str, detected_mime: str, input_size_bytes: int, uncompressed_size_bytes: int,
        extracted_characters: int, quota_date: str, jobs_limit: int, character_limit: int,
        unlimited: bool = False, account_scope: str = "telegram",
        installation_id: str = "", group_id: str = "", thread_id: int | None = None,
    ) -> dict[str, Any]:
        # P0-2: fail-closed on missing or forbidden owner identifiers.
        from zero.tenancy.models import FORBIDDEN_IDS, _CANDIDATE_PREFIX
        for field_name, value in (("installation_id", installation_id), ("group_id", group_id)):
            if not str(value).strip():
                raise ValueError(f"{field_name} must not be empty")
            if str(value).casefold() in FORBIDDEN_IDS:
                raise ValueError(f"{field_name} must not use a forbidden fallback: {value!r}")
            if str(value).startswith(_CANDIDATE_PREFIX):
                raise ValueError(f"{field_name} must not be a candidate placeholder: {value!r}")
        if extracted_characters > character_limit:
            raise QuotaExceeded("character_limit")
        now = int(time.time())
        with self._session_ctx() as conn:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute(
                "SELECT * FROM office_jobs WHERE account_scope=? AND chat_id=? AND message_id=?",
                (account_scope, chat_id, message_id),
            ).fetchone()
            if duplicate:
                conn.commit()
                return dict(duplicate)
            quota_state = "unlimited" if unlimited else "reserved"
            reservation_id = str(uuid.uuid4())
            if not unlimited:
                conn.execute(
                    "INSERT INTO office_quota_usage(installation_id,group_id,user_id,quota_date,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(installation_id,group_id,user_id,quota_date) DO NOTHING",
                    (installation_id, group_id, user_id, quota_date, now),
                )
                usage = conn.execute(
                    "SELECT * FROM office_quota_usage WHERE installation_id=? AND group_id=? AND user_id=? AND quota_date=?",
                    (installation_id, group_id, user_id, quota_date),
                ).fetchone()
                if int(usage["jobs_reserved"]) + int(usage["jobs_committed"]) >= jobs_limit:
                    conn.rollback()
                    raise QuotaExceeded("daily_jobs")
                conn.execute(
                    "UPDATE office_quota_usage SET jobs_reserved=jobs_reserved+1,characters_reserved=characters_reserved+?,last_job_id=?,updated_at=? WHERE installation_id=? AND group_id=? AND user_id=? AND quota_date=?",
                    (extracted_characters, job_id, now, installation_id, group_id, user_id, quota_date),
                )
            conn.execute(
                """INSERT INTO office_jobs(
                    id,trace_id,account_scope,installation_id,group_id,user_id,chat_id,thread_id,message_id,status,operation_type,office_format,request_text,
                    input_filename,input_path,detected_mime,input_size_bytes,uncompressed_size_bytes,extracted_characters,
                    quota_date,quota_reservation_id,quota_state,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,'quota_reserved',?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, trace_id, account_scope, installation_id, group_id, user_id, chat_id, thread_id, message_id, operation_type, office_format, request_text,
                 input_filename, input_path, detected_mime, input_size_bytes, uncompressed_size_bytes, extracted_characters,
                 quota_date, reservation_id, quota_state, now, now),
            )
            for from_status, to_status, event_type in (
                (None, "received", "job_received"), ("received", "validating", "input_validated"),
                ("validating", "quota_reserved", "quota_reserved"),
            ):
                conn.execute(
                    "INSERT INTO office_job_events(job_id,event_type,from_status,to_status,created_at) VALUES(?,?,?,?,?)",
                    (job_id, event_type, from_status, to_status, now),
                )
            row = conn.execute("SELECT * FROM office_jobs WHERE id=?", (job_id,)).fetchone()
            conn.commit()
            return dict(row)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._session_ctx() as conn:
            return self._row(conn.execute("SELECT * FROM office_jobs WHERE id=?", (job_id,)).fetchone())

    def get_by_message(self, account_scope: str, chat_id: int, message_id: int) -> dict[str, Any] | None:
        with self._session_ctx() as conn:
            return self._row(conn.execute(
                "SELECT * FROM office_jobs WHERE account_scope=? AND chat_id=? AND message_id=?",
                (account_scope, chat_id, message_id),
            ).fetchone())

    def events(self, job_id: str) -> list[dict[str, Any]]:
        with self._session_ctx() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM office_job_events WHERE job_id=? ORDER BY id", (job_id,))]

    def transition(
        self, job_id: str, to_status: str, *, expected: str | None = None, reason: str = "",
        error_code: str = "", error_message_safe: str = "", details: dict[str, Any] | None = None,
    ) -> bool:
        if to_status not in TRANSITIONS:
            raise StateTransitionError("unknown_status")
        now = int(time.time())
        with self._session_ctx() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM office_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                conn.rollback(); raise StateTransitionError("job_not_found")
            current = str(row["status"])
            if expected is not None and current != expected:
                conn.rollback(); return False
            if to_status not in TRANSITIONS[current]:
                conn.rollback(); raise StateTransitionError(f"invalid_transition:{current}:{to_status}")
            terminal_at = now if to_status in TERMINAL_STATUSES else None
            started_at = now if to_status == "processing" else None
            clear_lease = to_status in TERMINAL_STATUSES or to_status == "queued"
            conn.execute(
                """UPDATE office_jobs SET status=?,updated_at=?,completed_at=COALESCE(?,completed_at),
                   started_at=COALESCE(started_at,?),error_code=?,error_message_safe=?,
                   lease_owner=CASE WHEN ? THEN NULL ELSE lease_owner END,
                   lease_expires_at=CASE WHEN ? THEN NULL ELSE lease_expires_at END
                   WHERE id=?""",
                (to_status, now, terminal_at, started_at, error_code, error_message_safe, clear_lease, clear_lease, job_id),
            )
            conn.execute(
                "INSERT INTO office_job_events(job_id,event_type,from_status,to_status,reason,details_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (job_id, "status_transition", current, to_status, reason[:120], json.dumps(details or {}, separators=(",", ":")), now),
            )
            conn.commit()
            return True

    def quota_usage(self, user_id: int, quota_date: str, *, installation_id: str = "", group_id: str = "") -> dict[str, int]:
        empty = {"jobs_reserved": 0, "jobs_committed": 0, "characters_reserved": 0, "characters_committed": 0}
        with self._session_ctx() as conn:
            if installation_id and group_id:
                row = conn.execute(
                    "SELECT jobs_reserved,jobs_committed,characters_reserved,characters_committed FROM office_quota_usage WHERE installation_id=? AND group_id=? AND user_id=? AND quota_date=?",
                    (installation_id, group_id, user_id, quota_date),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT jobs_reserved,jobs_committed,characters_reserved,characters_committed FROM office_quota_usage WHERE user_id=? AND quota_date=?",
                    (user_id, quota_date),
                ).fetchone()
            return {key: int(row[key]) for key in empty} if row else empty

    def commit_quota(self, job_id: str) -> bool:
        return self._finish_quota(job_id, commit=True, reason="success")

    def refund_quota(self, job_id: str, reason: str) -> bool:
        return self._finish_quota(job_id, commit=False, reason=reason)

    def _finish_quota(self, job_id: str, *, commit: bool, reason: str) -> bool:
        now = int(time.time())
        with self._session_ctx() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT installation_id,group_id,user_id,quota_date,extracted_characters,quota_state,status FROM office_jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["quota_state"] != "reserved":
                conn.rollback(); return False
            chars = int(row["extracted_characters"])
            if commit:
                conn.execute(
                    """UPDATE office_quota_usage SET jobs_reserved=jobs_reserved-1,jobs_committed=jobs_committed+1,
                       characters_reserved=characters_reserved-?,characters_committed=characters_committed+?,updated_at=?
                       WHERE installation_id=? AND group_id=? AND user_id=? AND quota_date=?""",
                    (chars, chars, now, row["installation_id"], row["group_id"], row["user_id"], row["quota_date"]),
                )
                state, event = "committed", "quota_committed"
            else:
                conn.execute(
                    """UPDATE office_quota_usage SET jobs_reserved=jobs_reserved-1,
                       characters_reserved=characters_reserved-?,updated_at=? WHERE installation_id=? AND group_id=? AND user_id=? AND quota_date=?""",
                    (chars, now, row["installation_id"], row["group_id"], row["user_id"], row["quota_date"]),
                )
                state, event = "refunded", "quota_refunded"
                conn.execute(
                    "INSERT INTO office_metrics(name,value,updated_at) VALUES('office_quota_refunds_total',1,?) ON CONFLICT(name) DO UPDATE SET value=value+1,updated_at=excluded.updated_at",
                    (now,),
                )
            conn.execute("UPDATE office_jobs SET quota_state=?,updated_at=? WHERE id=?", (state, now, job_id))
            conn.execute(
                "INSERT INTO office_job_events(job_id,event_type,from_status,to_status,reason,created_at) VALUES(?,?,?,?,?,?)",
                (job_id, event, row["status"], row["status"], reason[:120], now),
            )
            conn.commit(); return True

    def claim_next(
        self, worker: str, *, lease_seconds: int, global_limit: int, per_user_limit: int, now: int | None = None,
    ) -> dict[str, Any] | None:
        now = int(time.time()) if now is None else int(now)
        with self._session_ctx() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT user_id,COUNT(*) AS c FROM office_jobs WHERE status IN ('planning','processing','validating_output','rendering','reviewing','repairing') AND COALESCE(lease_expires_at,0)>? GROUP BY user_id",
                (now,),
            ).fetchall()
            if sum(int(row["c"]) for row in active) >= global_limit:
                conn.rollback(); return None
            busy_users = {int(row["user_id"]): int(row["c"]) for row in active}
            candidates = conn.execute("SELECT * FROM office_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 100").fetchall()
            candidate = next((row for row in candidates if busy_users.get(int(row["user_id"]), 0) < per_user_limit), None)
            if candidate is None:
                conn.rollback(); return None
            changed = conn.execute(
                """UPDATE office_jobs SET status='planning',lease_owner=?,lease_expires_at=?,heartbeat_at=?,
                   attempt_count=attempt_count+1,updated_at=? WHERE id=? AND status='queued'""",
                (worker, now + lease_seconds, now, now, candidate["id"]),
            ).rowcount
            if changed != 1:
                conn.rollback(); return None
            conn.execute(
                "INSERT INTO office_job_events(job_id,event_type,from_status,to_status,reason,created_at) VALUES(?, 'job_claimed','queued','planning',?,?)",
                (candidate["id"], worker[:80], now),
            )
            row = conn.execute("SELECT * FROM office_jobs WHERE id=?", (candidate["id"],)).fetchone()
            conn.commit(); return dict(row)

    def claim_for_planning(self, worker: str, *, lease_seconds: int, now: int | None = None) -> dict[str, Any] | None:
        now = int(time.time()) if now is None else int(now)
        with self._session_ctx() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM office_jobs WHERE status='quota_reserved' ORDER BY created_at,id LIMIT 1").fetchone()
            if row is None:
                conn.rollback(); return None
            changed = conn.execute(
                """UPDATE office_jobs SET status='planning',lease_owner=?,lease_expires_at=?,heartbeat_at=?,
                   attempt_count=attempt_count+1,updated_at=? WHERE id=? AND status='quota_reserved'""",
                (f"planner:{worker}", now + lease_seconds, now, now, row["id"]),
            ).rowcount
            if changed != 1:
                conn.rollback(); return None
            conn.execute(
                "INSERT INTO office_job_events(job_id,event_type,from_status,to_status,reason,created_at) VALUES(?,'planning_claimed','quota_reserved','planning',?,?)",
                (row["id"], worker[:80], now),
            )
            claimed = conn.execute("SELECT * FROM office_jobs WHERE id=?", (row["id"],)).fetchone()
            conn.commit(); return dict(claimed)

    def list_jobs(self, statuses: set[str], *, limit: int = 100) -> list[dict[str, Any]]:
        if not statuses or not statuses <= set(TRANSITIONS):
            return []
        placeholders = ",".join("?" for _ in statuses)
        with self._session_ctx() as conn:
            rows = conn.execute(
                f"SELECT * FROM office_jobs WHERE status IN ({placeholders}) ORDER BY created_at,id LIMIT ?",
                (*sorted(statuses), min(1000, max(1, int(limit)))),
            ).fetchall()
            return [dict(row) for row in rows]

    def increment_metric(self, name: str, value: int = 1) -> None:
        if not re.fullmatch(r"office(?:cli)?_[a-z0-9_]+", name):
            raise ValueError("invalid_metric_name")
        now = int(time.time())
        with self._session_ctx() as conn:
            conn.execute(
                "INSERT INTO office_metrics(name,value,updated_at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET value=value+excluded.value,updated_at=excluded.updated_at",
                (name, int(value), now),
            )

    def set_metric(self, name: str, value: int) -> None:
        if not re.fullmatch(r"office(?:cli)?_[a-z0-9_]+", name):
            raise ValueError("invalid_metric_name")
        now = int(time.time())
        with self._session_ctx() as conn:
            conn.execute(
                "INSERT INTO office_metrics(name,value,updated_at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (name, int(value), now),
            )

    def metrics_snapshot(self) -> dict[str, int]:
        keys = {
            "office_jobs_received_total", "office_jobs_completed_total", "office_jobs_failed_total",
            "office_jobs_rejected_total", "office_quota_rejections_total",
            "office_character_limit_rejections_total", "officecli_failures_total",
            "office_job_duration_seconds_sum", "office_job_duration_seconds_count",
            "office_jobs_in_progress", "office_quota_refunds_total", "office_delivery_failures_total",
        }
        with self._session_ctx() as conn:
            stored = {str(row["name"]): int(row["value"]) for row in conn.execute("SELECT name,value FROM office_metrics")}
            stored["office_jobs_received_total"] = int(conn.execute("SELECT COUNT(*) FROM office_jobs").fetchone()[0])
            stored["office_jobs_completed_total"] = int(conn.execute("SELECT COUNT(*) FROM office_jobs WHERE status='completed'").fetchone()[0])
            stored["office_jobs_failed_total"] = int(conn.execute("SELECT COUNT(*) FROM office_jobs WHERE status='failed'").fetchone()[0])
            stored["office_jobs_in_progress"] = int(conn.execute("SELECT COUNT(*) FROM office_jobs WHERE status NOT IN ('completed','failed','cancelled','expired')").fetchone()[0])
        return {key: int(stored.get(key, 0)) for key in sorted(keys)}

    def heartbeat(self, job_id: str, worker: str, *, lease_seconds: int, now: int | None = None) -> bool:
        now = int(time.time()) if now is None else int(now)
        with self._session_ctx() as conn:
            changed = conn.execute(
                "UPDATE office_jobs SET heartbeat_at=?,lease_expires_at=?,updated_at=? WHERE id=? AND lease_owner=? AND status IN ('planning','processing','validating_output','rendering','reviewing','repairing')",
                (now, now + lease_seconds, now, job_id, worker),
            ).rowcount
            return changed == 1

    def recover_expired_leases(self, *, now: int | None = None, max_attempts: int) -> dict[str, int]:
        now = int(time.time()) if now is None else int(now)
        result = {"requeued": 0, "failed": 0}
        with self._session_ctx() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT * FROM office_jobs WHERE status IN ('planning','processing','validating_output','rendering','reviewing','repairing') AND COALESCE(lease_expires_at,0)<=?",
                (now,),
            ).fetchall()
            for row in rows:
                if int(row["attempt_count"]) >= max_attempts:
                    status, event = "failed", "lease_exhausted"
                    result["failed"] += 1
                    if row["quota_state"] == "reserved":
                        chars = int(row["extracted_characters"])
                        conn.execute(
                            "UPDATE office_quota_usage SET jobs_reserved=jobs_reserved-1,characters_reserved=characters_reserved-?,updated_at=? WHERE user_id=? AND quota_date=?",
                            (chars, now, row["user_id"], row["quota_date"]),
                        )
                        conn.execute("UPDATE office_jobs SET quota_state='refunded' WHERE id=?", (row["id"],))
                else:
                    status, event = "queued", "lease_recovered"
                    result["requeued"] += 1
                conn.execute(
                    "UPDATE office_jobs SET status=?,lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,updated_at=?,completed_at=? WHERE id=?",
                    (status, now, now if status == "failed" else None, row["id"]),
                )
                conn.execute(
                    "INSERT INTO office_job_events(job_id,event_type,from_status,to_status,reason,created_at) VALUES(?,?,?,?,?,?)",
                    (row["id"], event, row["status"], status, "expired_lease", now),
                )
            conn.commit()
        return result

    def update_job_artifacts(self, job_id: str, **values: Any) -> None:
        allowed = {"operation_type", "input_path", "output_path", "result_text", "preview_paths_json", "plan_json", "repair_count", "error_code", "error_message_safe"}
        values = {key: value for key, value in values.items() if key in allowed}
        if not values:
            return
        values["updated_at"] = int(time.time())
        clause = ",".join(f"{key}=?" for key in values)
        with self._session_ctx() as conn:
            conn.execute(f"UPDATE office_jobs SET {clause} WHERE id=?", (*values.values(), job_id))

    def reserve_delivery(self, job_id: str, worker: str, *, lease_seconds: int, now: int | None = None) -> str | None:
        now = int(time.time()) if now is None else int(now)
        with self._session_ctx() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM office_delivery_outbox WHERE job_id=?", (job_id,)).fetchone()
            if row:
                if row["status"] in {"sent", "ambiguous", "permanent_failed", "cancelled"} or (row["status"] == "reserved" and int(row["lease_expires_at"] or 0) > now):
                    conn.rollback(); return None
                if row["status"] == "reserved":
                    # A crashed sender may have delivered after the API call but
                    # before receipt persistence. Telegram offers no outbound
                    # idempotency key, so expired in-flight sends are ambiguous
                    # and must never be blindly replayed.
                    conn.execute(
                        "UPDATE office_delivery_outbox SET status='ambiguous',lease_owner=NULL,lease_expires_at=NULL,error_code='crash_window',updated_at=? WHERE job_id=?",
                        (now, job_id),
                    )
                    conn.commit(); return None
                conn.execute(
                    "UPDATE office_delivery_outbox SET status='reserved',attempt_count=attempt_count+1,lease_owner=?,lease_expires_at=?,updated_at=? WHERE job_id=?",
                    (worker, now + lease_seconds, now, job_id),
                )
                key = str(row["outbound_key"])
            else:
                key = f"office:{job_id}"
                # P0-2: carry the job's scope into the outbox row.
                job_row = conn.execute(
                    "SELECT installation_id,group_id,thread_id,chat_id FROM office_jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
                if job_row is None:
                    conn.rollback(); return None
                conn.execute(
                    "INSERT INTO office_delivery_outbox(id,job_id,outbound_key,installation_id,group_id,thread_id,destination_chat_id,status,lease_owner,lease_expires_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'reserved',?,?,?,?)",
                    (str(uuid.uuid4()), job_id, key, job_row["installation_id"], job_row["group_id"], job_row["thread_id"], job_row["chat_id"], worker, now + lease_seconds, now, now),
                )
            conn.commit(); return key

    def complete_delivery(self, outbound_key: str, *, status: str, error_code: str = "", telegram_message_id: int | None = None) -> None:
        if status not in {"sent", "retryable_failed", "permanent_failed", "ambiguous", "cancelled"}:
            raise ValueError("invalid_delivery_status")
        with self._session_ctx() as conn:
            conn.execute(
                "UPDATE office_delivery_outbox SET status=?,error_code=?,telegram_message_id=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE outbound_key=?",
                (status, error_code[:80], telegram_message_id, int(time.time()), outbound_key),
            )

    @staticmethod
    def _commit_quota_locked(conn: sqlite3.Connection, job_id: str, now: int) -> bool:
        row = conn.execute(
            "SELECT installation_id,group_id,user_id,quota_date,extracted_characters,quota_state,status FROM office_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        if not row or row["quota_state"] != "reserved":
            return False
        chars = int(row["extracted_characters"])
        changed = conn.execute(
            """UPDATE office_quota_usage SET jobs_reserved=jobs_reserved-1,jobs_committed=jobs_committed+1,
               characters_reserved=characters_reserved-?,characters_committed=characters_committed+?,updated_at=?
               WHERE installation_id=? AND group_id=? AND user_id=? AND quota_date=? AND jobs_reserved>0""",
            (chars, chars, now, row["installation_id"], row["group_id"], row["user_id"], row["quota_date"]),
        ).rowcount
        if changed != 1:
            raise RuntimeError("quota_reservation_missing")
        conn.execute("UPDATE office_jobs SET quota_state='committed',updated_at=? WHERE id=?", (now, job_id))
        conn.execute(
            "INSERT INTO office_job_events(job_id,event_type,from_status,to_status,reason,created_at) VALUES(?,?,?,?,?,?)",
            (job_id, "quota_committed", row["status"], row["status"], "delivery_receipt", now),
        )
        return True

    def complete_delivery_and_commit_quota(self, outbound_key: str, *, telegram_message_id: int) -> bool:
        now = int(time.time())
        with self._session_ctx() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT job_id,status FROM office_delivery_outbox WHERE outbound_key=?", (outbound_key,)).fetchone()
            if not row or row["status"] not in {"reserved", "sent"}:
                conn.rollback(); return False
            if row["status"] == "reserved":
                conn.execute(
                    "UPDATE office_delivery_outbox SET status='sent',error_code='',telegram_message_id=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE outbound_key=?",
                    (telegram_message_id, now, outbound_key),
                )
            committed = self._commit_quota_locked(conn, str(row["job_id"]), now)
            conn.commit()
            return committed or row["status"] == "sent"

    def reconcile_sent_delivery_quotas(self) -> int:
        now = int(time.time())
        with self._session_ctx() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """SELECT o.job_id FROM office_delivery_outbox o JOIN office_jobs j ON j.id=o.job_id
                   WHERE o.status='sent' AND j.quota_state='reserved'"""
            ).fetchall()
            count = 0
            for row in rows:
                count += int(self._commit_quota_locked(conn, str(row["job_id"]), now))
            conn.commit()
            return count



def migrate_office(db_path: str | Path) -> None:
    OfficeRepository(db_path)
