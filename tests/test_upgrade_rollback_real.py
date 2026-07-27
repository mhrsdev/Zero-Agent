"""Real upgrade→rollback→verify cycle test.

Simulates upgrading an old-schema Zero DB (pre-P0-2, without ownership columns)
to the new schema, verifying the upgrade, then rolling back from a pre-migration
backup and verifying the rollback restores the old state.

Uses real SQLite operations — no mocks.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backup_restore.py"


def _run(cmd: list[str]) -> tuple[int, str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def _create_old_db(path: Path) -> None:
    """Create a DB with the NEW schema minus P0-2 ownership columns.

    This simulates a pre-P0-2 database that has all other columns (lease,
    heartbeat, etc.) but lacks installation_id, group_id, thread_id.
    """
    import sys as _sys
    _sys.path.insert(0, str(ROOT))
    from zero.office.db import OFFICE_SCHEMA

    conn = sqlite3.connect(str(path))
    # Run the full schema to create all tables/indexes correctly
    conn.executescript(OFFICE_SCHEMA)

    # Now remove the P0-2 columns to simulate pre-migration state
    # We do this by recreating office_jobs without those columns
    conn.executescript("""
        ALTER TABLE office_jobs RENAME TO office_jobs_old;
        CREATE TABLE office_jobs (
          id TEXT PRIMARY KEY,
          trace_id TEXT NOT NULL,
          account_scope TEXT NOT NULL DEFAULT 'telegram',
          user_id INTEGER NOT NULL,
          chat_id INTEGER NOT NULL,
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
        INSERT INTO office_jobs SELECT
          id, trace_id, account_scope, user_id, chat_id, message_id,
          status, operation_type, office_format, request_text, input_filename,
          input_path, output_path, result_text, preview_paths_json, plan_json,
          detected_mime, input_size_bytes, uncompressed_size_bytes,
          extracted_characters, quota_date, quota_reservation_id, quota_state,
          attempt_count, repair_count, lease_owner, lease_expires_at,
          heartbeat_at, error_code, error_message_safe, created_at, updated_at,
          started_at, completed_at
        FROM office_jobs_old;
        DROP TABLE office_jobs_old;

        ALTER TABLE office_quota_usage RENAME TO office_quota_usage_old;
        CREATE TABLE office_quota_usage (
          user_id INTEGER NOT NULL,
          quota_date TEXT NOT NULL,
          jobs_reserved INTEGER NOT NULL DEFAULT 0,
          jobs_committed INTEGER NOT NULL DEFAULT 0,
          characters_reserved INTEGER NOT NULL DEFAULT 0,
          characters_committed INTEGER NOT NULL DEFAULT 0,
          last_job_id TEXT,
          updated_at INTEGER NOT NULL,
          PRIMARY KEY(user_id, quota_date)
        );
        INSERT INTO office_quota_usage SELECT
          user_id, quota_date, jobs_reserved, jobs_committed,
          characters_reserved, characters_committed, last_job_id, updated_at
        FROM office_quota_usage_old;
        DROP TABLE office_quota_usage_old;

        ALTER TABLE office_delivery_outbox RENAME TO office_delivery_outbox_old;
        CREATE TABLE office_delivery_outbox (
          id TEXT PRIMARY KEY,
          job_id TEXT NOT NULL UNIQUE,
          outbound_key TEXT NOT NULL UNIQUE,
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
        INSERT INTO office_delivery_outbox SELECT
          id, job_id, outbound_key, destination_chat_id,
          status, attempt_count, lease_owner, lease_expires_at,
          telegram_message_id, error_code, created_at, updated_at
        FROM office_delivery_outbox_old;
        DROP TABLE office_delivery_outbox_old;
    """)

    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO settings VALUES ('schema_version', '1.0')");
    conn.execute(
        "INSERT INTO office_jobs (id, trace_id, account_scope, user_id, chat_id, "
        "message_id, status, operation_type, office_format, quota_date, "
        "quota_reservation_id, quota_state, created_at, updated_at) "
        "VALUES ('job-1', 'trace-1', 'telegram', 123, 456, 789, "
        "'quota_reserved', 'docx_to_pdf', 'docx', '2026-07-26', "
        "'res-1', 'reserved', 1700000000, 1700000000)"
    )
    conn.commit()
    conn.execute(
        "INSERT INTO office_quota_usage (user_id, quota_date, jobs_reserved, "
        "jobs_committed, characters_reserved, characters_committed, last_job_id, updated_at) "
        "VALUES (123, '2026-07-26', 1, 0, 0, 0, 'job-1', 1700000000)"
    )
    conn.execute(
        "INSERT INTO office_delivery_outbox (id, job_id, outbound_key, destination_chat_id, "
        "status, attempt_count, created_at, updated_at) "
        "VALUES ('out-1', 'job-1', 'key-1', 456, 'reserved', 1, 1700000000, 1700000000)"
    )
    conn.commit()
    conn.close()


def _table_columns(path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(path))
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    conn.close()
    return cols


def _row_count(path: Path, table: str) -> int:
    conn = sqlite3.connect(str(path))
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return count


def _has_column(path: Path, table: str, col: str) -> bool:
    return col in _table_columns(path, table)


def _run_migration(path: Path) -> None:
    """Run the real OfficeRepository.migrate() on the DB."""
    sys.path.insert(0, str(ROOT))
    from zero.office.db import OfficeRepository
    repo = OfficeRepository(str(path))
    repo.migrate()


class TestUpgradeRollbackVerify:
    """Real upgrade→rollback→verify cycle."""

    def test_old_db_has_no_ownership_columns(self, tmp_path):
        """Pre-condition: old schema DB does NOT have P0-2 columns."""
        db = tmp_path / "old.db"
        _create_old_db(db)
        assert not _has_column(db, "office_jobs", "installation_id")
        assert not _has_column(db, "office_jobs", "group_id")
        assert not _has_column(db, "office_delivery_outbox", "installation_id")

    def test_migration_adds_ownership_columns(self, tmp_path):
        """Upgrade: migrate() adds installation_id, group_id, thread_id."""
        db = tmp_path / "old.db"
        _create_old_db(db)
        _run_migration(db)

        assert _has_column(db, "office_jobs", "installation_id")
        assert _has_column(db, "office_jobs", "group_id")
        assert _has_column(db, "office_jobs", "thread_id")
        assert _has_column(db, "office_quota_usage", "installation_id")
        assert _has_column(db, "office_quota_usage", "group_id")
        assert _has_column(db, "office_delivery_outbox", "installation_id")
        assert _has_column(db, "office_delivery_outbox", "group_id")

    def test_migration_preserves_existing_data(self, tmp_path):
        """Upgrade preserves all existing data rows."""
        db = tmp_path / "old.db"
        _create_old_db(db)
        original_jobs = _row_count(db, "office_jobs")
        original_quota = _row_count(db, "office_quota_usage")
        original_outbox = _row_count(db, "office_delivery_outbox")

        _run_migration(db)

        assert _row_count(db, "office_jobs") == original_jobs
        assert _row_count(db, "office_quota_usage") == original_quota
        assert _row_count(db, "office_delivery_outbox") == original_outbox

    def test_migration_is_idempotent(self, tmp_path):
        """Running migrate() twice does not fail or duplicate columns."""
        db = tmp_path / "old.db"
        _create_old_db(db)
        _run_migration(db)
        _run_migration(db)  # should not raise
        assert _has_column(db, "office_jobs", "installation_id")

    def test_full_upgrade_rollback_cycle(self, tmp_path):
        """Full cycle: old DB → backup → migrate (upgrade) → verify → restore (rollback) → verify."""
        db = tmp_path / "zero.db"
        backup = tmp_path / "zero_pre_migration.db"
        _create_old_db(db)

        # Step 1: Backup pre-migration state
        rc, output = _run([sys.executable, str(SCRIPT), "backup", str(db), str(backup)])
        assert rc == 0, f"Backup failed: {output}"

        # Verify backup is the old schema
        assert not _has_column(backup, "office_jobs", "installation_id")

        # Step 2: Run migration (upgrade)
        _run_migration(db)

        # Verify upgrade
        assert _has_column(db, "office_jobs", "installation_id")
        assert _has_column(db, "office_jobs", "group_id")
        assert _row_count(db, "office_jobs") == 1  # data preserved

        # Step 3: Rollback — restore from pre-migration backup
        rc, output = _run([sys.executable, str(SCRIPT), "restore", str(backup), str(db)])
        assert rc == 0, f"Rollback failed: {output}"

        # Step 4: Verify rollback — old schema restored
        assert not _has_column(db, "office_jobs", "installation_id")
        assert not _has_column(db, "office_jobs", "group_id")
        assert _row_count(db, "office_jobs") == 1  # data still there
        assert _row_count(db, "office_quota_usage") == 1

        # Step 5: Verify DB integrity after rollback
        rc, output = _run([sys.executable, str(SCRIPT), "verify", str(db)])
        assert rc == 0, f"Post-rollback verify failed: {output}"

    def test_migration_adds_indexes(self, tmp_path):
        """Migration creates scope indexes for query performance."""
        db = tmp_path / "old.db"
        _create_old_db(db)
        _run_migration(db)

        conn = sqlite3.connect(str(db))
        indexes = {row[1] for row in conn.execute(
            "SELECT type, name, tbl_name FROM sqlite_master WHERE type='index'"
        )}
        conn.close()
        assert "idx_office_jobs_scope" in indexes
        assert "idx_office_outbox_scope" in indexes

    def test_upgraded_db_sets_defaults_on_existing_rows(self, tmp_path):
        """Existing rows get DEFAULT '' for new ownership columns."""
        db = tmp_path / "old.db"
        _create_old_db(db)
        _run_migration(db)

        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT installation_id, group_id FROM office_jobs WHERE id='job-1'").fetchone()
        conn.close()
        assert row[0] == ""  # default empty string
        assert row[1] == ""  # default empty string

    def test_upgraded_db_accepts_new_scoped_inserts(self, tmp_path):
        """After migration, new rows with scope values can be inserted."""
        db = tmp_path / "old.db"
        _create_old_db(db)
        _run_migration(db)

        conn = sqlite3.connect(str(db))
        conn.execute(
        "INSERT INTO office_jobs (id, trace_id, account_scope, user_id, chat_id, message_id, "
        "operation_type, office_format, status, created_at, updated_at, "
        "installation_id, group_id, quota_date, quota_reservation_id, quota_state) "
        "VALUES ('job-2', 'trace-2', 'telegram', 456, 789, 101, 'xlsx_to_pdf', 'xlsx', "
        "'queued', 1700000001, 1700000001, 'inst-2', 'group-2', '2026-07-26', 'res-2', 'reserved')"
    )
        conn.commit()
        row = conn.execute("SELECT installation_id, group_id FROM office_jobs WHERE id='job-2'").fetchone()
        conn.close()
        assert row[0] == "inst-2"
        assert row[1] == "group-2"
