import hashlib
import sqlite3

import pytest

from zero.memory_v3 import MemoryV3Item, MemoryV3Service
from zero.memory_v3.migration import (
    apply_v1_to_v3,
    rollback_v1_to_v3,
    verify_v1_to_v3,
)


def source_db(path):
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE long_term_memory(
                memory_id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL,
                subject_user_id INTEGER, category TEXT NOT NULL,
                content TEXT NOT NULL, confidence REAL NOT NULL,
                source_message_ids_json TEXT NOT NULL, created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL, expires_at INTEGER,
                status TEXT NOT NULL, created_by INTEGER NOT NULL
            );
            """
        )
        db.execute(
            "INSERT INTO long_term_memory VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("valid-1", -100, 7, "preference", "likes tea", .9, "[11]", 1, 1, None, "active", 7),
        )
        db.execute(
            "INSERT INTO long_term_memory VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ambiguous-1", -100, None, "preference", "", .9, "not-json", 1, 1, None, "active", 7),
        )


def backup_proof(source, backup):
    backup.write_bytes(source.read_bytes())
    return hashlib.sha256(backup.read_bytes()).hexdigest()


def test_direct_v1_to_v3_dry_run_apply_verify_and_rollback(tmp_path):
    source = tmp_path / "v1.db"
    target = tmp_path / "v3.db"
    backup = tmp_path / "v1.backup.db"
    source_db(source)
    service = MemoryV3Service(str(target))
    service._put_sync(MemoryV3Item.group(chat_id=-100, content="pre-existing", kind="fact"))

    dry = apply_v1_to_v3(source, target, run_id="r1", dry_run=True)
    assert dry.scanned == 2 and dry.imported == 0 and dry.quarantined == 1
    assert service.count_items() == 1

    with pytest.raises(ValueError, match="backup proof"):
        apply_v1_to_v3(source, target, run_id="r1", dry_run=False)

    digest = backup_proof(source, backup)
    result = apply_v1_to_v3(source, target, run_id="r1", dry_run=False, backup_path=backup, backup_sha256=digest)
    assert result.imported == 1 and result.quarantined == 1
    assert verify_v1_to_v3(target, "r1")["valid"] is True

    repeated = apply_v1_to_v3(source, target, run_id="r1", dry_run=False, backup_path=backup, backup_sha256=digest)
    assert repeated.imported == 0 and MemoryV3Service(str(target)).count_items() == 2

    with pytest.raises(RuntimeError, match="interrupt"):
        apply_v1_to_v3(source, target, run_id="r2", dry_run=False, backup_path=backup, backup_sha256=digest, fail_after=0)
    resumed = apply_v1_to_v3(source, target, run_id="r2", dry_run=False, backup_path=backup, backup_sha256=digest)
    assert resumed.imported == 0 and resumed.reused == 1
    assert verify_v1_to_v3(target, "r2")["valid"] is True

    rolled = rollback_v1_to_v3(target, "r1")
    assert rolled["soft_deleted"] == 1
    assert MemoryV3Service(str(target)).count_items() == 2
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT count(*) FROM memory_v3_items WHERE status='active' AND content='pre-existing'").fetchone()[0] == 1
