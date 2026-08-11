import asyncio
import sqlite3

from zero.models import IncomingMessage


def message(*, chat=-100, sender=1, text="پروژه چه شد؟", mid=10, reply_to=None):
    return IncomingMessage(
        chat_id=chat, chat_title="گروه", sender_id=sender, sender_label=f"u{sender}",
        text=text, message_id=mid, reply_to_message_id=reply_to,
        platform="telegram", account_scope="listener",
    )


def test_personal_and_group_memory_are_jointly_retrievable_without_cross_chat_leakage(tmp_path):
    from zero.memory_v3 import MemoryV3Item, MemoryV3Service

    async def run():
        service = MemoryV3Service(str(tmp_path / "v3.db"))
        await service.put(MemoryV3Item.personal(
            chat_id=-100, user_id=1, content="علی برای پروژهٔ گروه پایتون کار می‌کند",
            source_message_ids=(1,), importance=.9, confidence=.95,
        ))
        await service.put(MemoryV3Item.group(
            chat_id=-100, content="تصمیم گروه: جلسهٔ پروژه چهارشنبه است",
            source_message_ids=(2,), importance=.9, confidence=.95,
        ))
        await service.put(MemoryV3Item.personal(
            chat_id=-200, user_id=1, content="نباید از گروه دیگر دیده شود",
            source_message_ids=(3,), importance=.9, confidence=.95,
        ))

        context, meta = await service.context(message())
        assert "علی برای پروژه" in context
        assert "جلسهٔ پروژه چهارشنبه" in context
        assert "نباید از گروه دیگر" not in context
        assert meta["personal_selected"] == 1
        assert meta["group_selected"] == 1

    asyncio.run(run())


def test_reply_thread_keeps_all_participants_and_relevant_sibling(tmp_path):
    from zero.memory_v3 import MemoryV3Service

    async def run():
        service = MemoryV3Service(str(tmp_path / "v3.db"))
        await service.record_message(message(sender=1, mid=1, text="دوشنبه جلسه بگیریم"))
        await service.record_message(message(sender=2, mid=2, reply_to=1, text="دوشنبه امتحان دارم"))
        await service.record_message(message(sender=3, mid=3, reply_to=1, text="سه‌شنبه بهتره"))
        current = message(sender=1, mid=4, reply_to=2, text="پس چهارشنبه؟")
        await service.record_message(current)

        thread = await service.thread_context(current, max_depth=8, sibling_limit=12)
        assert thread.participant_ids == (1, 2, 3)
        assert [row.message_id for row in thread.ancestors] == [2, 1]
        assert [row.message_id for row in thread.siblings] == [3]

    asyncio.run(run())


def test_v3_thread_context_is_rendered_as_a_multi_person_group_story(tmp_path):
    from zero.memory_context import compose_memory_context
    from zero.memory_v3 import MemoryV3Service
    from zero.semantic_memory import SemanticUserMemory
    from zero.storage import ZeroStore

    async def run():
        db = str(tmp_path / "main.db")
        store = ZeroStore(db)
        service = MemoryV3Service(str(tmp_path / "v3.db"))
        for item in (
            message(sender=1, mid=1, text="دوشنبه جلسه بگیریم"),
            message(sender=2, mid=2, reply_to=1, text="دوشنبه امتحان دارم"),
            message(sender=3, mid=3, reply_to=1, text="سه‌شنبه بهتره"),
        ):
            await service.record_message(item)
        current = message(sender=1, mid=4, reply_to=2, text="پس چهارشنبه؟")
        await service.record_message(current)
        context, meta = await compose_memory_context(
            store=store, semantic_memory=SemanticUserMemory(db), message=current,
            recent=[], layered={"short": [], "medium": [], "long": []}, v3_memory=service,
        )
        assert "[MULTI_PERSON_REPLY_THREAD]" in context
        assert "دوشنبه امتحان دارم" in context
        assert "سه‌شنبه بهتره" in context
        assert meta["thread_participant_ids"] == [1, 2, 3]

    asyncio.run(run())


def test_v2_migration_is_idempotent_and_preserves_source_scope(tmp_path):
    from zero.memory_v2.service import MemoryItem, MemoryV2Service
    from zero.memory_v3 import MemoryV3Service

    async def run():
        old = MemoryV2Service(str(tmp_path / "v2.db"))
        old_id = await old.put(MemoryItem(
            "", "fact", "group_user", "education_track = ریاضی", "education_track=ریاضی",
            user_id=7, chat_id=-100, group_id=-100, subject="user",
            predicate="education_track", object="ریاضی", source_message_ids=(55,),
        ))
        service = MemoryV3Service(str(tmp_path / "v3.db"))
        first = await service.migrate_v2(str(tmp_path / "v2.db"))
        second = await service.migrate_v2(str(tmp_path / "v2.db"))
        assert first["items"] == 1
        assert second["items"] == 0
        assert service.count_items() == 1
        row = service.item_by_legacy_id(old_id)
        assert row["chat_id"] == -100 and row["owner_user_id"] == 7
        assert row["source_message_ids_json"] == "[55]"

    asyncio.run(run())


def test_legacy_zero_memory_migration_keeps_personal_and_group_ownership(tmp_path):
    from zero.memory_v3 import MemoryV3Service

    source = tmp_path / "zero.db"
    conn = sqlite3.connect(source)
    conn.executescript("""
        CREATE TABLE long_term_memory(memory_id TEXT,chat_id INTEGER,subject_user_id INTEGER,category TEXT,content TEXT,confidence REAL,source_message_ids_json TEXT,created_at REAL,expires_at REAL,status TEXT);
        CREATE TABLE medium_term_memory(event_id TEXT,chat_id INTEGER,participants_json TEXT,topic TEXT,summary TEXT,source_message_ids_json TEXT,importance REAL,confidence REAL,occurred_at REAL,expires_at REAL,status TEXT);
    """)
    conn.execute("INSERT INTO long_term_memory VALUES('l1',-100,7,'preference','قهوهٔ تلخ دوست دارد',.9,'[9]',1,NULL,'active')")
    conn.execute("INSERT INTO medium_term_memory VALUES('m1',-100,'[7,8]','project','جلسهٔ پروژه چهارشنبه است','[10]',.8,.9,2,NULL,'active')")
    conn.commit(); conn.close()

    async def run():
        service = MemoryV3Service(str(tmp_path / "v3.db"))
        result = await service.migrate_legacy_zero(str(source))
        assert result["items"] == 2
        text, _ = await service.context(message(chat=-100, sender=7, text="چی از من یادت هست و پروژه چه شد؟"))
        assert "قهوهٔ تلخ" in text and "جلسهٔ پروژه چهارشنبه" in text
        assert "قهوهٔ تلخ" not in (await service.context(message(chat=-200, sender=7)))[0]

    asyncio.run(run())
