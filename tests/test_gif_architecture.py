import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from zero.brain import ZeroBrain, user_requests_gif, user_requests_sticker
from zero.config import ZeroConfig
from zero.gifs.decision import GifSendOutcome
from zero.stickers.library import StickerLibrary
from zero.stickers.observer import StickerObserver
from zero.stickers.models import Sticker, StickerCandidate
from zero.stickers.sender import StickerSender
from zero.storage import ZeroStore


def make_media(doc_id: int, *, mood: str, gif: bool, quality: float = 0.9) -> Sticker:
    return Sticker(
        doc_id=doc_id,
        access_hash=doc_id * 10,
        file_reference=f"ref-{doc_id}".encode(),
        mime_type="video/mp4" if gif else "image/webp",
        emoji="",
        stickerset_id=None if gif else 700,
        stickerset_access_hash=None if gif else 701,
        stickerset_short_name=None if gif else "safe_pack",
        is_animated=False,
        is_video=gif,
        mood_tags=mood,
        quality_score=quality,
        first_seen=1,
        last_seen=1,
        usage_count=1,
    )


def make_brain(store: ZeroStore, client, *, rng=None) -> ZeroBrain:
    brain = object.__new__(ZeroBrain)
    brain.config = ZeroConfig.load("config/zero.example.yaml")
    brain.store = store
    brain._client = client
    brain._gif_rng = rng
    brain._gif_send_lock = asyncio.Lock()
    return brain


def test_gif_intent_is_distinct_from_sticker_intent():
    assert user_requests_gif("یه گیف خنده دار بفرست") is True
    assert user_requests_gif("send a funny gif") is True
    assert user_requests_sticker("یه گیف خنده دار بفرست") is False
    assert user_requests_gif("یه استیکر بفرست") is False


@pytest.mark.asyncio
async def test_gif_ingestion_derives_safe_mood_from_persian_caption(tmp_path):
    store = ZeroStore(str(tmp_path / "ingestion.db"))
    cfg = ZeroConfig.load("config/zero.example.yaml")
    observer = StickerObserver(cfg, store, client=AsyncMock(), vision=None)
    event = SimpleNamespace(
        id=99, raw_text="خیلی خنده دار بود 😂", media=True,
        document=SimpleNamespace(
            id=99, access_hash=990, file_reference=b"ref",
            mime_type="video/mp4", attributes=[],
        ),
    )
    await observer.process_gif(event, sender_id=42, sender_label="u", chat_id=-1001)
    stored = await store.get_sticker(99)
    assert stored is not None
    assert "funny" in (stored.mood_tags or "").split(",")
    assert stored.vision_summary == "خیلی خنده دار بود 😂"


@pytest.mark.asyncio
async def test_existing_unclassified_gif_gets_caption_semantics_backfilled(tmp_path):
    store = ZeroStore(str(tmp_path / "backfill.db"))
    cfg = ZeroConfig.load("config/zero.example.yaml")
    observer = StickerObserver(cfg, store, client=AsyncMock(), vision=None)
    doc = SimpleNamespace(
        id=100, access_hash=1000, file_reference=b"old",
        mime_type="video/mp4", attributes=[],
    )
    first = SimpleNamespace(id=1, raw_text="", media=True, document=doc)
    await observer.process_gif(first, sender_id=42, sender_label="u", chat_id=-1001)
    before = await store.get_sticker(100)
    assert not before.mood_tags
    doc.file_reference = b"fresh"
    second = SimpleNamespace(id=2, raw_text="خیلی ناراحت و غمگین", media=True, document=doc)
    await observer.process_gif(second, sender_id=42, sender_label="u", chat_id=-1001)
    after = await store.get_sticker(100)
    assert "sad" in (after.mood_tags or "").split(",")
    assert after.vision_summary == "خیلی ناراحت و غمگین"
    assert after.file_reference == b"fresh"


@pytest.mark.asyncio
async def test_sticker_selection_never_crosses_into_gif_candidates(tmp_path):
    store = ZeroStore(str(tmp_path / "separation.db"))
    await store.add_sticker(make_media(1, mood="funny", gif=True))
    library = StickerLibrary(ZeroConfig.load("config/zero.example.yaml"), store, ZeroConfig.load("config/zero.example.yaml").stickers)
    assert await library.get_random_sticker(mood="funny") is None


@pytest.mark.asyncio
async def test_clear_gif_intent_with_only_unrelated_candidate_fails_closed(tmp_path):
    store = ZeroStore(str(tmp_path / "unrelated.db"))
    await store.add_sticker(make_media(2, mood="sad", gif=True))
    client = SimpleNamespace(send_file=AsyncMock())
    brain = make_brain(store, client)
    outcome = await brain._send_gif_once(-1001, "funny", direct_request=True)
    assert isinstance(outcome, GifSendOutcome)
    assert outcome.reason == "no_relevant_candidate"
    assert outcome.candidate_count == 0
    client.send_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_matching_gif_is_selected_and_sent_deterministically(tmp_path):
    store = ZeroStore(str(tmp_path / "matching.db"))
    await store.add_sticker(make_media(3, mood="funny", gif=True))
    await store.add_sticker(make_media(4, mood="sad", gif=True))
    client = SimpleNamespace(send_file=AsyncMock(return_value=SimpleNamespace(id=30)))
    brain = make_brain(store, client, rng=SimpleNamespace(random=lambda: 0.0, choice=lambda items: items[0]))
    outcome = await brain._send_gif_once(-1001, "funny", direct_request=True)
    assert outcome.sent is True
    assert outcome.candidate_id == 3
    assert outcome.transport == "sent"
    assert outcome.relevance_score >= outcome.confidence_threshold
    kwargs = client.send_file.await_args.kwargs
    assert "attributes" not in kwargs


@pytest.mark.asyncio
async def test_gif_retry_never_reuses_only_recent_candidate(tmp_path):
    store = ZeroStore(str(tmp_path / "retry.db"))
    await store.add_sticker(make_media(5, mood="funny", gif=True))
    await store.record_gif_send(5, -1001, trigger_type="direct")
    client = SimpleNamespace(send_file=AsyncMock())
    brain = make_brain(store, client)
    brain.config = brain.config.model_copy(update={
        "gifs": brain.config.gifs.model_copy(update={"direct_cooldown_seconds": 0})
    })
    outcome = await brain._send_gif_once(
        -1001, "funny", direct_request=True, retry_request=True
    )
    assert outcome.reason == "repeat_window"
    client.send_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_gif_policy_is_independent_from_sticker_history(tmp_path):
    store = ZeroStore(str(tmp_path / "policy.db"))
    await store.add_sticker(make_media(6, mood="funny", gif=False))
    await store.record_sticker_send(6, -1001, trigger_type="direct")
    policy = await store.get_gif_send_policy(-1001, trigger_type="direct")
    assert policy["sent_last_hour"] == 0


@pytest.mark.asyncio
async def test_gif_transport_failure_is_not_reported_as_selection_failure(tmp_path):
    store = ZeroStore(str(tmp_path / "transport.db"))
    await store.add_sticker(make_media(7, mood="funny", gif=True))
    client = SimpleNamespace(
        send_file=AsyncMock(side_effect=RuntimeError("stale reference")),
        forward_messages=AsyncMock(side_effect=RuntimeError("source unavailable")),
    )
    brain = make_brain(store, client)
    outcome = await brain._send_gif_once(-1001, "funny", direct_request=True)
    assert outcome.reason == "transport_failed"
    assert outcome.candidate_id == 7
    assert outcome.transport == "failed"


@pytest.mark.asyncio
async def test_gif_sender_uses_source_observation_after_stale_reference(tmp_path):
    store = ZeroStore(str(tmp_path / "fallback.db"))
    media = make_media(8, mood="funny", gif=True)
    media.source_chat_id = -2002
    media.source_message_id = 88
    await store.add_sticker(media)
    await store.record_sticker_observation(8, -2002, 44, 88)
    stored = await store.get_sticker(8)
    client = SimpleNamespace(
        send_file=AsyncMock(side_effect=RuntimeError("stale reference")),
        forward_messages=AsyncMock(return_value=SimpleNamespace(id=81)),
    )
    sender = StickerSender(ZeroConfig.load("config/zero.example.yaml"), store, client)
    sent = await sender.send_media(-1001, StickerCandidate(stored, 1.0, "gif:funny"))
    assert sent is True
    client.forward_messages.assert_awaited_once_with(
        entity=-1001, messages=88, from_peer=-2002
    )


@pytest.mark.asyncio
async def test_legacy_gif_history_schema_is_migrated_without_data_loss(tmp_path):
    db = tmp_path / "legacy-gif.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE gif_send_history (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, doc_id INTEGER NOT NULL, sent_at INTEGER NOT NULL)")
        conn.execute("INSERT INTO gif_send_history(chat_id, doc_id, sent_at) VALUES (-1001, 7, 10)")
        conn.commit()
    store = ZeroStore(str(db))
    await store.record_gif_send(8, -1001, trigger_type="direct")
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(gif_send_history)")}
        rows = conn.execute("SELECT doc_id, trigger_type FROM gif_send_history ORDER BY id").fetchall()
    assert "trigger_type" in columns
    assert rows == [(7, "auto"), (8, "direct")]
