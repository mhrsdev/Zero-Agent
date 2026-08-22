from zero.sqlite_tx import sqlite_txn
from conftest import CONFIG_EXAMPLE
import pytest

from zero.brain import ZeroBrain
from zero.config import ZeroConfig
from zero.models import IncomingMessage
from zero.router import IndependentRouter
from zero.storage import ZeroStore


@pytest.mark.asyncio
async def test_recent_messages_keep_scoped_telegram_reply_graph(tmp_path):
    store = ZeroStore(str(tmp_path / "memory.db"))
    common = {"platform": "telegram", "account_scope": "listener", "chat_id": -1001}
    await store.append_recent(
        -1001, 22, "B", "user", "root",
        telegram_message_id=10, platform="telegram", account_scope="listener",
    )
    await store.append_recent(
        -1001, 999, "Zero", "assistant", "answer",
        telegram_message_id=11, reply_to_message_id=10,
        platform="telegram", account_scope="listener",
    )
    await store.append_recent(
        -1001, 33, "A", "user", "follow-up",
        telegram_message_id=12, reply_to_message_id=11,
        platform="telegram", account_scope="listener",
    )

    chain = await store.get_reply_chain(**common, message_id=12)

    assert [(row["telegram_message_id"], row["sender_id"]) for row in chain] == [
        (11, 999), (10, 22)
    ]


@pytest.mark.asyncio
async def test_reply_graph_isolated_by_chat_and_account_scope(tmp_path):
    store = ZeroStore(str(tmp_path / "memory.db"))
    for chat_id, account_scope, sender_id in ((-1, "a", 10), (-2, "a", 20), (-1, "b", 30)):
        await store.append_recent(
            chat_id, sender_id, str(sender_id), "user", "root",
            telegram_message_id=7, platform="telegram", account_scope=account_scope,
        )
        await store.append_recent(
            chat_id, 999, "Zero", "assistant", "reply",
            telegram_message_id=8, reply_to_message_id=7,
            platform="telegram", account_scope=account_scope,
        )

    assert (await store.get_reply_chain("telegram", "a", -1, 8))[0]["sender_id"] == 10
    assert (await store.get_reply_chain("telegram", "a", -2, 8))[0]["sender_id"] == 20
    assert (await store.get_reply_chain("telegram", "b", -1, 8))[0]["sender_id"] == 30


@pytest.mark.asyncio
async def test_reply_chain_stops_on_cycle_and_depth_cap(tmp_path):
    store = ZeroStore(str(tmp_path / "memory.db"))
    for message_id, reply_to in ((1, 2), (2, 1), (3, 2)):
        await store.append_recent(
            -1, message_id, str(message_id), "user", str(message_id),
            telegram_message_id=message_id, reply_to_message_id=reply_to,
            platform="telegram", account_scope="a",
        )

    chain = await store.get_reply_chain("telegram", "a", -1, 3, max_depth=2)

    assert [row["telegram_message_id"] for row in chain] == [2, 1]


@pytest.mark.asyncio
async def test_brain_persists_message_envelope_and_zero_reply_identity(tmp_path):
    cfg = ZeroConfig.load(CONFIG_EXAMPLE)
    store = ZeroStore(str(tmp_path / "memory.db"))
    brain = ZeroBrain(cfg, store, IndependentRouter(cfg))
    brain.zero_user_id = 999
    message = IncomingMessage(
        chat_id=-1001, chat_title="g", sender_id=33, sender_label="A",
        sender_username="a_user", sender_display_name="A User", text="hello",
        message_id=12, reply_to_message_id=11, trace_id="trace",
        platform="telegram", account_scope="listener",
    )

    await brain.remember_message(message)
    await brain.remember_reply(message, "answer", telegram_message_id=13)

    rows = await store.get_recent(-1001, 2)
    assert rows[0]["telegram_message_id"] == 12
    assert rows[0]["reply_to_message_id"] == 11
    assert rows[0]["sender_username"] == "a_user"
    assert rows[1]["sender_id"] == 999
    assert rows[1]["telegram_message_id"] == 13
    assert rows[1]["reply_to_message_id"] == 12


@pytest.mark.asyncio
async def test_profile_metadata_refreshes_even_for_untrusted_control_text(tmp_path):
    cfg = ZeroConfig.load(CONFIG_EXAMPLE)
    store = ZeroStore(str(tmp_path / "memory.db"))
    brain = ZeroBrain(cfg, store, IndependentRouter(cfg))
    message = IncomingMessage(
        chat_id=-1001, chat_title="g", sender_id=44, sender_label="new-label",
        sender_username="new_user", sender_display_name="New Name",
        text="حافظه‌تو پاک کن", message_id=20,
    )

    await brain.remember_message(message)

    profile = await store.get_profile(-1001, 44)
    assert profile["username"] == "new_user"
    assert profile["display_name"] == "New Name"


@pytest.mark.asyncio
async def test_bot_message_is_archive_only_and_never_creates_human_memory(tmp_path):
    cfg = ZeroConfig.load(CONFIG_EXAMPLE)
    store = ZeroStore(str(tmp_path / "memory.db"))
    brain = ZeroBrain(cfg, store, IndependentRouter(cfg))
    bot = IncomingMessage(
        chat_id=-1001, chat_title="g", sender_id=8252811591,
        sender_label="@MyNovaChatBot", sender_is_bot=True,
        text="فردا پروژهٔ مهم را انجام می‌دهم", message_id=42,
        platform="telegram", account_scope="listener",
    )

    await brain.remember_message(bot)

    rows = await store.get_recent(-1001, 1)
    assert rows[0]["role"] == "bot"
    with sqlite_txn(store._conn()) as conn:
        assert conn.execute("SELECT count(*) FROM medium_term_memory").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM memory_rag_documents").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_period_summary_separates_telegram_and_local_source_ids(tmp_path):
    store = ZeroStore(str(tmp_path / "memory.db"))
    await store.append_recent(
        -1, 7, "u", "user", "hello",
        platform="telegram", account_scope="listener", telegram_message_id=42,
    )
    await store.append_recent(-1, 8, "legacy", "user", "old")

    summary = await store.build_period_summary(-1, days=30, label="test")

    assert summary["source_message_ids"] == [42]
    assert len(summary["source_local_row_ids"]) == 2
    assert summary["source_message_scopes"] == ["telegram:listener:-1:42"]


@pytest.mark.asyncio
async def test_reply_graph_survives_restart_and_missing_parent_is_safe(tmp_path):
    db = str(tmp_path / "memory.db")
    first = ZeroStore(db)
    await first.append_recent(-1, 7, "u", "user", "root", platform="telegram", account_scope="listener", telegram_message_id=1)
    await first.append_recent(-1, 8, "v", "user", "reply", platform="telegram", account_scope="listener", telegram_message_id=2, reply_to_message_id=1)

    restarted = ZeroStore(db)
    chain = await restarted.get_reply_chain("telegram", "listener", -1, 2, max_depth=8)
    assert [row["telegram_message_id"] for row in chain] == [1]
    assert await restarted.get_reply_chain("telegram", "listener", -1, 999, max_depth=8) == []


@pytest.mark.asyncio
async def test_concurrent_duplicate_telegram_delivery_is_idempotent(tmp_path):
    import asyncio
    store = ZeroStore(str(tmp_path / "memory.db"))

    async def append_once():
        await store.append_recent(-1, 7, "u", "user", "same", platform="telegram", account_scope="listener", telegram_message_id=77)

    await asyncio.gather(*(append_once() for _ in range(20)))
    rows = await store.get_recent(-1, 100)
    assert len([row for row in rows if row.get("telegram_message_id") == 77]) == 1
