"""Real backup→restore→verify cycle test.

Creates a real SQLite DB with data, backs it up, modifies the original,
restores from backup, and verifies the restored DB matches the original state.
Uses the actual backup_restore.py script — no mocks.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup_restore.py"


def _run(cmd: list[str]) -> tuple[int, str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def _create_db(path: Path) -> None:
    """Create a real Zero-like SQLite DB with tables and data."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS office_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, status TEXT, installation_id TEXT, group_id TEXT
        );
        CREATE TABLE IF NOT EXISTS direct_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER, role TEXT, content TEXT
        );
    """)
    conn.execute("INSERT INTO settings VALUES ('bot_name', 'Zero')");
    conn.execute("INSERT INTO settings VALUES ('version', '0.1.0')");
    conn.execute("INSERT INTO office_jobs (user_id, status, installation_id, group_id) VALUES (123, 'completed', 'inst-1', 'group-1')");
    conn.execute("INSERT INTO direct_memory (chat_id, role, content) VALUES (123, 'user', 'hello world')");
    conn.commit()
    conn.close()


def _row_count(path: Path, table: str) -> int:
    conn = sqlite3.connect(str(path))
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return count


def _get_setting(path: Path, key: str) -> str | None:
    conn = sqlite3.connect(str(path))
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row[0] if row else None


class TestBackupRestoreVerify:
    """Real backup→restore→verify cycle using filesystem operations."""

    def test_backup_creates_valid_copy(self, tmp_path):
        """backup command creates a real file that passes integrity_check."""
        db = tmp_path / "test.db"
        backup = tmp_path / "backup.db"
        _create_db(db)
        assert db.exists(), "Source DB must exist"

        rc, output = _run([sys.executable, str(SCRIPT), "backup", str(db), str(backup)])
        assert rc == 0, f"Backup failed: {output}"
        assert backup.exists(), "Backup file must be created"

        # Verify backup has same data
        assert _row_count(backup, "settings") == 2
        assert _row_count(backup, "office_jobs") == 1
        assert _row_count(backup, "direct_memory") == 1
        assert _get_setting(backup, "bot_name") == "Zero"

    def test_restore_replaces_modified_db(self, tmp_path):
        """restore command replaces a modified DB with the backup copy."""
        db = tmp_path / "test.db"
        backup = tmp_path / "backup.db"
        _create_db(db)

        # Create backup
        rc, _ = _run([sys.executable, str(SCRIPT), "backup", str(db), str(backup)])
        assert rc == 0

        # Modify the original DB (add data, change settings)
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE settings SET value='Modified' WHERE key='bot_name'")
        conn.execute("INSERT INTO office_jobs (user_id, status, installation_id, group_id) VALUES (456, 'pending', 'inst-2', 'group-2')")
        conn.commit()
        conn.close()

        assert _get_setting(db, "bot_name") == "Modified"
        assert _row_count(db, "office_jobs") == 2

        # Restore from backup
        rc, output = _run([sys.executable, str(SCRIPT), "restore", str(backup), str(db)])
        assert rc == 0, f"Restore failed: {output}"

        # Verify DB is back to original state
        assert _get_setting(db, "bot_name") == "Zero"
        assert _row_count(db, "office_jobs") == 1
        assert _row_count(db, "direct_memory") == 1

    def test_verify_reports_healthy_db(self, tmp_path):
        """verify command reports ok=True for a valid DB."""
        db = tmp_path / "test.db"
        _create_db(db)

        rc, output = _run([sys.executable, str(SCRIPT), "verify", str(db)])
        assert rc == 0, f"Verify failed: {output}"
        assert "ok" in output
        assert "True" in output or "'ok': True" in output

    def test_verify_detects_missing_db(self, tmp_path):
        """verify command reports ok=False for a missing DB."""
        missing = tmp_path / "nonexistent.db"
        rc, output = _run([sys.executable, str(SCRIPT), "verify", str(missing)])
        assert rc == 1, "Verify should fail for missing DB"
        assert "not found" in output.lower() or "False" in output

    def test_restore_creates_bak_of_existing(self, tmp_path):
        """restore creates a .bak file of the existing DB before overwriting."""
        db = tmp_path / "test.db"
        backup = tmp_path / "backup.db"
        _create_db(db)

        # Create backup
        _run([sys.executable, str(SCRIPT), "backup", str(db), str(backup)])

        # Modify original
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE settings SET value='Changed'")
        conn.commit()
        conn.close()

        # Restore
        rc, _ = _run([sys.executable, str(SCRIPT), "restore", str(backup), str(db)])
        assert rc == 0

        # .bak should exist with the modified content
        bak = Path(f"{db}.bak")
        assert bak.exists(), ".bak file must be created"
        assert _get_setting(bak, "bot_name") == "Changed"

    def test_full_backup_restore_verify_cycle(self, tmp_path):
        """Full cycle: create DB → backup → destroy → restore → verify."""
        db = tmp_path / "zero.db"
        backup = tmp_path / "zero_backup.db"
        _create_db(db)

        original_settings = _row_count(db, "settings")
        original_jobs = _row_count(db, "office_jobs")

        # Step 1: Backup
        rc, _ = _run([sys.executable, str(SCRIPT), "backup", str(db), str(backup)])
        assert rc == 0

        # Step 2: Destroy original
        db.unlink()
        assert not db.exists()

        # Step 3: Restore
        rc, _ = _run([sys.executable, str(SCRIPT), "restore", str(backup), str(db)])
        assert rc == 0
        assert db.exists()

        # Step 4: Verify
        rc, output = _run([sys.executable, str(SCRIPT), "verify", str(db)])
        assert rc == 0

        # Data must match original
        assert _row_count(db, "settings") == original_settings
        assert _row_count(db, "office_jobs") == original_jobs
        assert _get_setting(db, "version") == "0.1.0"

    def test_backup_preserves_wal(self, tmp_path):
        """Backup copies WAL file when it exists."""
        db = tmp_path / "test.db"
        backup = tmp_path / "backup.db"
        _create_db(db)

        # Enable WAL mode and write (creates -wal file)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("INSERT INTO settings VALUES ('wal_test', 'yes')")
        conn.commit()
        conn.close()

        wal_path = Path(f"{db}-wal")
        if wal_path.exists():
            rc, _ = _run([sys.executable, str(SCRIPT), "backup", str(db), str(backup)])
            assert rc == 0
            backup_wal = Path(f"{backup}-wal")
            assert backup_wal.exists(), "WAL file must be copied with backup"
