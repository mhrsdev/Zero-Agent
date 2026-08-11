# transfer artifact for debug-sandbox
from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
import uuid
from pathlib import Path

from zero.storage import ZeroStore


def test_store_allows_database_in_writable_unowned_parent_without_chmodding_parent():
    parent = Path(tempfile.gettempdir())
    before_mode = stat.S_IMODE(parent.stat().st_mode)
    db = parent / f"zero-store-{os.getpid()}-{uuid.uuid4().hex}.db"
    try:
        store = ZeroStore(str(db))
        with store._conn() as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert stat.S_IMODE(db.stat().st_mode) == 0o600
        assert stat.S_IMODE(parent.stat().st_mode) == before_mode
    finally:
        for candidate in (db, Path(f"{db}-wal"), Path(f"{db}-shm")):
            candidate.unlink(missing_ok=True)
