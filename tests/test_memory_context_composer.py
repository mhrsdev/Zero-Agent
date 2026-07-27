import pytest

from zero.memory_context import compose_memory_context
from zero.deferred_memory import DeferredMemory
from zero.models import IncomingMessage
from zero.semantic_memory import SemanticUserMemory
from zero.storage import ZeroStore


def approve_name(memory, chat_id, sender_id, name):
    candidate = memory.candidate(
        chat_id=chat_id, sender_id=sender_id, category="identity",
        key="preferred_name", value=name, confidence=.95,
        evidence_message_ids=[1], source_text=f"اسمم {name}",
    )
    memory.approve(candidate, sender_id)


@pytest.mark.asyncio
async def test_context_keeps_current_reply_target_and_reply_chain_separate(tmp_path):
    db = str(tmp_path / "memory.db")
    store = ZeroStore(db)
    semantic = SemanticUserMemory(db)
    chat = -1001
    await store.upsert_profile(chat, 22, "B", username="b_user", display_name="کاربر ب")
    await store.upsert_profile(chat, 33, "A", username="a_user", display_name="کاربر الف")
    approve_name(semantic, chat, 22, "بهرام")
    approve_name(semantic, chat, 33, "آرمان")
    await store.add_long_memory(chat, "personal_note", "یادداشت مخصوص بهرام", created_by=22, subject_user_id=22)
    for message_id, sender_id, reply_to, text in (
        (10, 22, None, "root by B"),
        (11, 999, 10, "Zero reply"),
        (12, 33, 11, "follow-up by A"),
    ):
        await store.append_recent(
            chat, sender_id, str(sender_id), "assistant" if sender_id == 999 else "user", text,
            platform="telegram", account_scope="listener", telegram_message_id=message_id,
            reply_to_message_id=reply_to,
        )
    message = IncomingMessage(
        chat_id=chat, chat_title="g", sender_id=33, sender_label="A", text="زیرو ادامه بده",
        message_id=12, reply_to_message_id=11, reply_sender_id=999,
        platform="telegram", account_scope="listener",
    )

    context, meta = await compose_memory_context(
        store=store, semantic_memory=semantic, message=message,
        recent=await store.get_recent(chat, 100), layered={"short": [], "medium": [], "long": []},
    )

    assert "[CURRENT_MESSAGE_IDENTITY]" in context
    assert "[CURRENT_USER_MEMORY]" in context
    assert "owner=chat_id=-1001,sender_id=33" in context
    assert "preferred_name=آرمان" in context
    assert "[REPLY_CHAIN]" in context
    assert "telegram_message_id=11" in context and "telegram_message_id=10" in context
    assert "[TARGET_USER_MEMORY]" in context
    assert "owner=chat_id=-1001,sender_id=22" in context
    assert "یادداشت مخصوص بهرام" in context
    assert meta["target_ids"] == [22]


@pytest.mark.asyncio
async def test_reply_to_zero_uses_nearest_human_ancestor_only(tmp_path):
    db = str(tmp_path / "memory.db")
    store = ZeroStore(db)
    semantic = SemanticUserMemory(db)
    chat = -1001
    for message_id, sender_id, reply_to, role, label in (
        (10, 30, None, "user", "C"),
        (11, 999, 10, "assistant", "Zero"),
        (12, 20, 11, "user", "B"),
        (13, 999, 12, "assistant", "Zero"),
        (14, 10, 13, "user", "A"),
    ):
        await store.append_recent(
            chat, sender_id, label, role, str(message_id),
            platform="telegram", account_scope="listener", telegram_message_id=message_id,
            reply_to_message_id=reply_to,
        )
        if role == "user":
            await store.upsert_profile(chat, sender_id, label)
    message = IncomingMessage(
        chat_id=chat, chat_title="g", sender_id=10, sender_label="A", text="زیرو ادامه بده",
        message_id=14, reply_to_message_id=13, reply_sender_id=999, reply_to_zero=True,
        platform="telegram", account_scope="listener",
    )

    _, meta = await compose_memory_context(
        store=store, semantic_memory=semantic, message=message,
        recent=await store.get_recent(chat, 100), layered={"short": [], "medium": [], "long": []},
    )

    assert meta["target_ids"] == [20]


@pytest.mark.asyncio
async def test_relevant_recent_messages_prefer_newest_matches(tmp_path):
    db = str(tmp_path / "memory.db")
    store = ZeroStore(db)
    semantic = SemanticUserMemory(db)
    recent = [
        {
            "id": index, "chat_id": -1001, "sender_id": 1, "sender_label": "u", "role": "user",
            "text": f"موضوع مشترک {index}" if index <= 30 else "گپ", "telegram_message_id": index,
            "platform": "telegram", "account_scope": "listener", "reply_to_message_id": None,
        }
        for index in range(1, 51)
    ]
    message = IncomingMessage(chat_id=-1001, chat_title="g", sender_id=9, sender_label="current", text="موضوع مشترک؟")

    context, _ = await compose_memory_context(
        store=store, semantic_memory=semantic, message=message,
        recent=recent, layered={"short": [], "medium": [], "long": []},
    )

    block = context.split("[RELEVANT_RECENT_MESSAGES]\n", 1)[1].split("\n[/RELEVANT_RECENT_MESSAGES]", 1)[0]
    ids = [int(line.split("telegram_message_id=", 1)[1].split()[0]) for line in block.splitlines()]
    assert ids == list(range(30, 20, -1))


@pytest.mark.asyncio
async def test_ambiguous_name_never_merges_users(tmp_path):
    db = str(tmp_path / "memory.db")
    store = ZeroStore(db)
    semantic = SemanticUserMemory(db)
    chat = -1001
    await store.upsert_profile(chat, 1, "علی", display_name="علی")
    await store.upsert_profile(chat, 2, "علی", display_name="علی")
    message = IncomingMessage(chat_id=chat, chat_title="g", sender_id=3, sender_label="C", text="علی کیه؟")

    context, meta = await compose_memory_context(
        store=store, semantic_memory=semantic, message=message,
        recent=[], layered={"short": [], "medium": [], "long": []},
    )

    assert "[TARGET_IDENTITY_AMBIGUITY]" in context
    assert meta["ambiguous"] is True
    assert "[TARGET_USER_MEMORY]" not in context


@pytest.mark.asyncio
async def test_context_is_bounded_complete_and_deduplicates_recent(tmp_path):
    db = str(tmp_path / "memory.db")
    store = ZeroStore(db)
    semantic = SemanticUserMemory(db)
    chat = -1001
    recent = []
    for i in range(100):
        recent.append({
            "id": i, "chat_id": chat, "sender_id": i % 4 + 1,
            "sender_label": f"u{i % 4}", "role": "user", "text": ("موضوع پایتون " if i == 2 else "گپ ") + ("x" * 500),
            "telegram_message_id": i + 1, "platform": "telegram", "account_scope": "listener",
            "reply_to_message_id": None, "created_at": i,
        })
    message = IncomingMessage(chat_id=chat, chat_title="g", sender_id=9, sender_label="current", text="پایتون چی شد؟")

    context, _ = await compose_memory_context(
        store=store, semantic_memory=semantic, message=message,
        recent=recent, layered={"short": [], "medium": [], "long": []},
    )

    assert len(context) <= 28000
    for tag in ("CURRENT_USER_MEMORY", "RECENT_GROUP_FLOW", "RELEVANT_RECENT_MESSAGES", "ORDINARY_MEMORY", "RAG_MEMORY"):
        assert context.count(f"[{tag}]") == context.count(f"[/{tag}]") == 1
    assert context.count("telegram_message_id=3 ") <= 1


@pytest.mark.asyncio
async def test_relevant_deferred_note_is_wired_into_current_user_context(tmp_path):
    db = str(tmp_path / "memory.db")
    store = ZeroStore(db)
    semantic = SemanticUserMemory(db)
    deferred = DeferredMemory(db)
    saved = IncomingMessage(chat_id=-1, chat_title="g", sender_id=7, sender_label="u", text="یادت باشه من قهوه تلخ دوست دارم", message_id=1)
    deferred.capture_note(saved)
    query = IncomingMessage(chat_id=-1, chat_title="g", sender_id=7, sender_label="u", text="قهوه مورد علاقم چی بود؟", message_id=2)

    context, _ = await compose_memory_context(
        store=store, semantic_memory=semantic, message=query,
        recent=[], layered={"short": [], "medium": [], "long": []},
    )

    assert "قهوه تلخ" in context
    assert "owner=chat_id=-1,sender_id=7" in context


@pytest.mark.asyncio
async def test_rag_failure_does_not_break_context_composition(tmp_path, monkeypatch):
    db = str(tmp_path / "memory.db")
    store = ZeroStore(db)
    semantic = SemanticUserMemory(db)
    message = IncomingMessage(chat_id=-1, chat_title="g", sender_id=9, sender_label="u", text="قبلاً چی گفتم؟")

    async def fail(*args, **kwargs):
        raise RuntimeError("fts unavailable")

    monkeypatch.setattr(store, "retrieve_rag", fail)
    context, meta = await compose_memory_context(
        store=store, semantic_memory=semantic, message=message,
        recent=[], layered={"short": [], "medium": [], "long": []},
    )
    assert "[RAG_MEMORY]" in context
    assert meta["chars"] == len(context)
