# transfer artifact for debug-sandbox
from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
import uuid
from pathlib import Path

from zero.fsprivacy import path_is_private
from zero.storage import ZeroStore


def test_store_allows_database_in_writable_unowned_parent_without_chmodding_parent():
    parent = Path(tempfile.gettempdir())
    before_mode = stat.S_IMODE(parent.stat().st_mode)
    db = parent / f"zero-store-{os.getpid()}-{uuid.uuid4().hex}.db"
    try:
        store = ZeroStore(str(db))
        conn = store._conn()
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()
        assert path_is_private(db)
        assert stat.S_IMODE(parent.stat().st_mode) == before_mode
    finally:
        import gc
        import time

        gc.collect()
        for candidate in (db, Path(f"{db}-wal"), Path(f"{db}-shm")):
            for attempt in range(3):
                try:
                    candidate.unlink(missing_ok=True)
                    break
                except PermissionError:
                    # Transient Windows AV/indexer locks on freshly written files.
                    time.sleep(0.2 * (attempt + 1))
