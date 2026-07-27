import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from zero.config import ZeroConfig
from zero.brain import ZeroBrain
from zero.router import IndependentRouter
from zero.deferred_memory import DeferredMemory
from zero.models import IncomingMessage, RouteResult
from zero.semantic_memory import SemanticUserMemory
from zero.storage import ZeroStore


def message(text, sender_id=7):
    return IncomingMessage(
        chat_id=-1001, chat_title="g", sender_id=sender_id,
        sender_label=str(sender_id), text=text, message_id=10,
    )


def test_deferred_notes_reject_sensitive_text_and_keep_scoped_safe_notes(tmp_path):
    db = tmp_path / "memory.db"
    ZeroStore(str(db))
    memory = DeferredMemory(db)

    memory.capture_note(message("یادت باشه رمز من abc123 است"))
    memory.capture_note(message("یادت باشه من قهوه تلخ دوست دارم"))

    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT sender_id,content FROM user_memory_notes").fetchall()
    assert rows == [(7, "یادت باشه من قهوه تلخ دوست دارم")]
    assert memory.notes_context(message("قهوه رو چطور درست کنم؟", sender_id=8)) == ""
    assert "قهوه تلخ" in memory.notes_context(message("قهوه رو چطور درست کنم؟"))
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT last_used_at FROM user_memory_notes").fetchone()[0] is not None


def test_semantic_correction_keeps_old_value_active_if_new_write_fails(tmp_path):
    memory = SemanticUserMemory(tmp_path / "memory.db")
    candidate = memory.candidate(
        chat_id=1, sender_id=2, category="identity", key="preferred_name",
        value="قدیمی", confidence=.9, evidence_message_ids=[1], source_text="x",
    )
    old_id = memory.approve(candidate, 2)
    with memory._conn() as con:
        con.executescript("""
        CREATE TRIGGER fail_semantic_replacement
        BEFORE INSERT ON semantic_user_memory WHEN NEW.version > 1
        BEGIN SELECT RAISE(ABORT, 'injected'); END;
        """)

    with pytest.raises(sqlite3.IntegrityError):
        memory.correct(old_id, "جدید", 2)

    assert memory.retrieve(1, 2)[0]["value"] == "قدیمی"


def test_semantic_pending_candidates_are_deduplicated(tmp_path):
    memory = SemanticUserMemory(tmp_path / "memory.db")
    kwargs = dict(
        chat_id=1, sender_id=2, category="identity", key="preferred_name",
        value="علی", confidence=.9, evidence_message_ids=[1], source_text="اسمم علی است",
    )
    assert memory.candidate(**kwargs) == memory.candidate(**kwargs)
    with memory._conn() as con:
        assert con.execute("SELECT count(*) FROM semantic_user_memory_candidates WHERE status='pending'").fetchone()[0] == 1


def test_semantic_concurrent_pending_candidates_merge_evidence(tmp_path):
    memory = SemanticUserMemory(tmp_path / "memory.db")

    def create(message_id):
        return memory.candidate(
            chat_id=1, sender_id=2, category="identity", key="preferred_name",
            value="علی", confidence=.9, evidence_message_ids=[message_id], source_text="اسمم علی است",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(create, range(1, 9)))

    assert len(set(ids)) == 1
    with memory._conn() as con:
        row = con.execute("SELECT evidence_message_ids_json FROM semantic_user_memory_candidates WHERE status='pending'").fetchone()
    assert set(__import__('json').loads(row[0])) == set(range(1, 9))


@pytest.mark.asyncio
async def test_semantic_monthly_summary_replaces_deterministic_fallback(tmp_path, monkeypatch):
    cfg = ZeroConfig.load("/root/zero/config/zero.example.yaml")
    store = ZeroStore(str(tmp_path / "memory.db"))
    brain = ZeroBrain(cfg, store, IndependentRouter(cfg))

    async def semantic_summary(*args, **kwargs):
        return "خلاصه معنایی تازه و معتبر گروه"

    monkeypatch.setattr(brain, "build_daily_summary", semantic_summary)
    await brain.build_monthly_group_memory(-1)

    with brain.memory_v3._conn() as conn:
        row = conn.execute(
            "SELECT content,kind,scope FROM memory_v3_items WHERE chat_id=? AND kind=?",
            (-1, "group_monthly_summary"),
        ).fetchone()
    assert row and row["content"] == "خلاصه معنایی تازه و معتبر گروه"
    assert row["scope"] == "group"


@pytest.mark.asyncio
async def test_summary_rejects_fallback_provider_even_if_text_looks_valid(tmp_path):
    cfg = ZeroConfig.load("/root/zero/config/zero.example.yaml")
    store = ZeroStore(str(tmp_path / "memory.db"))
    await store.append_recent(-1, 1, "u", "user", "پیام گروه")
    brain = ZeroBrain(cfg, store, IndependentRouter(cfg))

    async def fallback(*args, **kwargs):
        return RouteResult(text="خلاصه ظاهراً معتبر", provider="fallback", model="none", attempts=1, metadata={"error": "unavailable"})

    brain.router.complete = fallback
    assert await brain.build_daily_summary(-1) == ""
