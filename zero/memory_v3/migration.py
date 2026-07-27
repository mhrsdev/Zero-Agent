from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .service import MemoryV3Item, MemoryV3Service


@dataclass(frozen=True)
class MigrationResult:
    run_id: str
    scanned: int
    imported: int
    reused: int
    quarantined: int


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _metadata(target: Path) -> None:
    with sqlite3.connect(target) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_v3_migration_runs(
                run_id TEXT PRIMARY KEY, source_path TEXT NOT NULL,
                source_sha256 TEXT NOT NULL, started_at REAL NOT NULL,
                finished_at REAL, status TEXT NOT NULL, backup_path TEXT,
                backup_sha256 TEXT
            );
            CREATE TABLE IF NOT EXISTS memory_v3_migration_map(
                run_id TEXT NOT NULL, source_table TEXT NOT NULL,
                source_id TEXT NOT NULL, source_sha256 TEXT NOT NULL,
                target_id TEXT, action TEXT NOT NULL,
                PRIMARY KEY(run_id, source_table, source_id, source_sha256)
            );
            """
        )


def _source_rows(source: Path, table: str, required: set[str]) -> list[sqlite3.Row]:
    with sqlite3.connect(source) as db:
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if table not in tables:
            return []
        columns = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
        missing = required - columns
        if missing:
            raise ValueError(f"unsupported V1 {table} schema: {sorted(missing)}")
        return list(db.execute(f"SELECT * FROM {table} WHERE status IN ('active','verified')"))


def _ids(raw: Any) -> tuple[int, ...]:
    try:
        values = json.loads(raw or "[]")
        if not isinstance(values, list):
            raise ValueError
        return tuple(int(v) for v in values)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()


def _item(table: str, row: sqlite3.Row) -> MemoryV3Item:
    if table == "long_term_memory":
        content = str(row["content"] or "").strip()
        if not content or row["chat_id"] is None or not str(row["category"] or "").strip():
            raise ValueError("ambiguous_scope_or_content")
        source_ids = _ids(row["source_message_ids_json"])
        if row["source_message_ids_json"] not in (None, "", "[]") and not source_ids:
            raise ValueError("invalid_source_message_ids")
        subject = row["subject_user_id"]
        return MemoryV3Item(
            content=content,
            scope="personal" if subject is not None else "group",
            chat_id=int(row["chat_id"]),
            owner_user_id=int(subject) if subject is not None else None,
            subject_user_id=int(subject) if subject is not None else None,
            kind=f"v1:{row['category']}",
            importance=.8,
            confidence=float(row["confidence"]),
            source_message_ids=source_ids,
            legacy_id=f"long_term_memory:{row['memory_id']}",
            created_at=float(row["created_at"]),
            expires_at=row["expires_at"],
            metadata={"source_table": "long_term_memory", "migration": "v1_to_v3"},
        )
    if table == "medium_term_memory":
        topic = str(row["topic"] or "").strip()
        summary = str(row["summary"] or "").strip()
        if not topic or not summary or row["chat_id"] is None:
            raise ValueError("ambiguous_scope_or_content")
        participants = _ids(row["participants_json"])
        sources = _ids(row["source_message_ids_json"])
        if row["participants_json"] not in (None, "", "[]") and not participants:
            raise ValueError("invalid_participants")
        if row["source_message_ids_json"] not in (None, "", "[]") and not sources:
            raise ValueError("invalid_source_message_ids")
        try:
            importance = float(row["importance"])
            confidence = float(row["confidence"])
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_quality_score") from exc
        return MemoryV3Item(
            content=f"{topic}: {summary}", scope="group", chat_id=int(row["chat_id"]), kind="v1:group_event",
            importance=importance, confidence=confidence, source_message_ids=sources,
            legacy_id=f"medium_term_memory:{row['event_id']}", created_at=float(row["occurred_at"]),
            expires_at=row["expires_at"], metadata={"source_table": "medium_term_memory", "migration": "v1_to_v3", "participants": participants},
        )
    if table == "semantic_user_memory":
        category = str(row["category"] or "").strip()
        key = str(row["key"] or "").strip()
        if not category or not key or row["chat_id"] is None or row["sender_id"] is None:
            raise ValueError("ambiguous_identity_or_content")
        try:
            value = json.loads(row["value_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_value_json") from exc
        evidence = _ids(row["evidence_message_ids_json"])
        if row["evidence_message_ids_json"] not in (None, "", "[]") and not evidence:
            raise ValueError("invalid_evidence_message_ids")
        try:
            confidence = float(row["confidence"])
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_quality_score") from exc
        return MemoryV3Item.personal(
            chat_id=int(row["chat_id"]), user_id=int(row["sender_id"]),
            content=f"{category}.{key}={value}", kind="v1:semantic",
            importance=.8, confidence=confidence, source_message_ids=evidence,
            legacy_id=f"semantic_user_memory:{row['id']}", created_at=float(row["first_seen_at"]),
            expires_at=row["expires_at"], metadata={"source_table": "semantic_user_memory", "migration": "v1_to_v3", "category": category, "key": key},
        )
    raise ValueError(f"unsupported source table: {table}")


def apply_v1_to_v3(
    source_path: str | Path,
    target_path: str | Path,
    *,
    run_id: str,
    dry_run: bool,
    backup_path: str | Path | None = None,
    backup_sha256: str | None = None,
    fail_after: int | None = None,
) -> MigrationResult:
    source, target = Path(source_path), Path(target_path)
    if not source.exists():
        raise ValueError("V1 source database is missing")
    if not dry_run:
        if not backup_path or not backup_sha256:
            raise ValueError("backup proof is required for apply")
        backup = Path(backup_path)
        if not backup.exists() or _digest(backup) != backup_sha256:
            raise ValueError("backup proof does not match backup file")
    service = MemoryV3Service(str(target))
    if not service.healthy:
        raise ValueError("V3 target initialization failed")
    _metadata(target)
    source_hash = _digest(source)
    table_specs = {
        "long_term_memory": {"id": "memory_id", "required": {"memory_id", "chat_id", "subject_user_id", "category", "content", "confidence", "source_message_ids_json", "created_at", "expires_at", "status"}},
        "medium_term_memory": {"id": "event_id", "required": {"event_id", "chat_id", "participants_json", "topic", "summary", "source_message_ids_json", "importance", "confidence", "occurred_at", "expires_at", "status"}},
        "semantic_user_memory": {"id": "id", "required": {"id", "chat_id", "sender_id", "category", "key", "value_json", "confidence", "evidence_message_ids_json", "first_seen_at", "expires_at", "status"}},
    }
    rows_by_table = {table: _source_rows(source, table, spec["required"]) for table, spec in table_specs.items()}
    scanned = sum(len(rows) for rows in rows_by_table.values())
    with sqlite3.connect(target) as db:
        db.execute(
            "INSERT OR IGNORE INTO memory_v3_migration_runs(run_id,source_path,source_sha256,started_at,status,backup_path,backup_sha256) VALUES(?,?,?,?,?,?,?)",
            (run_id, str(source.resolve()), source_hash, time.time(), "dry_run" if dry_run else "running", str(backup_path) if backup_path else None, backup_sha256),
        )
    imported = reused = quarantined = 0
    for table, rows in rows_by_table.items():
        source_id_key = table_specs[table]["id"]
        for row in rows:
            source_id, row_hash = str(row[source_id_key]), hashlib.sha256(repr(tuple(row)).encode()).hexdigest()
            if not dry_run and fail_after is not None and imported + reused + quarantined >= fail_after:
                with sqlite3.connect(target) as db:
                    db.execute("UPDATE memory_v3_migration_runs SET finished_at=?,status=? WHERE run_id=?", (time.time(), "interrupted", run_id))
                raise RuntimeError("fault_injected_migration_interrupt")
            with sqlite3.connect(target) as db:
                prior = db.execute("SELECT action FROM memory_v3_migration_map WHERE run_id=? AND source_table=? AND source_id=? AND source_sha256=?", (run_id, table, source_id, row_hash)).fetchone()
            if prior:
                if prior[0] == "quarantine":
                    quarantined += 1
                elif prior[0] == "reused":
                    reused += 1
                continue
            try:
                item = _item(table, row)
            except (TypeError, ValueError) as exc:
                quarantined += 1
                with sqlite3.connect(target) as db:
                    db.execute("INSERT OR IGNORE INTO memory_v3_quarantine(source_name,source_id,reason,created_at) VALUES(?,?,?,?)", (f"v1:{source.resolve()}", f"{table}:{source_id}", str(exc), time.time()))
                    db.execute("INSERT OR IGNORE INTO memory_v3_migration_map VALUES(?,?,?,?,?,?)", (run_id, table, source_id, row_hash, None, "quarantine"))
                continue
            if dry_run:
                continue
            normalized = service._norm(item.content)
            with sqlite3.connect(target) as db:
                existing = db.execute("SELECT id FROM memory_v3_items WHERE scope=? AND chat_id IS ? AND owner_user_id IS ? AND normalized_content=? AND status='active'", (item.scope, item.chat_id, item.owner_user_id, normalized)).fetchone()
            target_id = service._put_sync(item)
            action = "reused" if existing else "inserted"
            with sqlite3.connect(target) as db:
                db.execute("INSERT OR IGNORE INTO memory_v3_migration_map VALUES(?,?,?,?,?,?)", (run_id, table, source_id, row_hash, target_id, action))
            if action == "reused":
                reused += 1
            else:
                imported += 1
    with sqlite3.connect(target) as db:
        db.execute("UPDATE memory_v3_migration_runs SET finished_at=?,status=? WHERE run_id=?", (time.time(), "dry_run" if dry_run else "applied", run_id))
    return MigrationResult(run_id, scanned, imported, reused, quarantined)


def verify_v1_to_v3(target_path: str | Path, run_id: str) -> dict[str, Any]:
    with sqlite3.connect(target_path) as db:
        run = db.execute("SELECT status FROM memory_v3_migration_runs WHERE run_id=?", (run_id,)).fetchone()
        mappings = db.execute("SELECT target_id,action FROM memory_v3_migration_map WHERE run_id=?", (run_id,)).fetchall()
        missing = sum(1 for target_id, action in mappings if action != "quarantine" and not db.execute("SELECT 1 FROM memory_v3_items WHERE id=?", (target_id,)).fetchone())
        quarantined = sum(1 for _, action in mappings if action == "quarantine")
    return {"run_id": run_id, "status": run[0] if run else "missing", "mapped": len(mappings), "quarantined": quarantined, "missing_targets": missing, "valid": bool(run and run[0] in {"applied", "dry_run"} and missing == 0)}


def rollback_v1_to_v3(target_path: str | Path, run_id: str) -> dict[str, Any]:
    with sqlite3.connect(target_path) as db:
        ids = [r[0] for r in db.execute("SELECT target_id FROM memory_v3_migration_map WHERE run_id=? AND action='inserted' AND target_id IS NOT NULL", (run_id,))]
        changed = 0
        for target_id in ids:
            changed += db.execute("UPDATE memory_v3_items SET status='deleted' WHERE id=? AND status='active'", (target_id,)).rowcount
        db.execute("UPDATE memory_v3_migration_runs SET status='rolled_back',finished_at=? WHERE run_id=?", (time.time(), run_id))
    return {"run_id": run_id, "soft_deleted": changed}
