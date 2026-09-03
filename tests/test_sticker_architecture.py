from __future__ import annotations

from zero.sqlite_tx import sqlite_txn
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from conftest import CONFIG_EXAMPLE
from zero.brain import ZeroBrain, detect_mood_from_user
from zero.config import ZeroConfig
from zero.stickers.classifier import StickerClassifier
from zero.stickers.library import StickerLibrary
from zero.stickers.models import Sticker, StickerCandidate
from zero.stickers.sender import StickerSender
from zero.storage import ZeroStore


def make_sticker(doc_id: int, *, mood: str = "", emoji: str = "", quality: float = 0.9,
                 spam: float = 0.0, nsfw: float = 0.0,
                 last_message_id: int | None = None) -> Sticker:
    now = int(time.time())
    return Sticker(
        doc_id=doc_id, access_hash=doc_id * 100, file_reference=b"fresh-ref",
        mime_type="image/webp", emoji=emoji, stickerset_id=None,
        stickerset_access_hash=None, stickerset_short_name=None,
        is_animated=False, is_video=False, mood_tags=mood,
        quality_score=quality, spam_score=spam, nsfw_score=nsfw,
        first_seen=now, last_seen=now, first_sender_id=10,
        last_message_id=last_message_id,
    )


def load_config() -> ZeroConfig:
    config = ZeroConfig.load(CONFIG_EXAMPLE)
    config.stickers.enabled = True
    config.stickers.auto_enabled = True
    config.stickers.limit_per_hour = 50
    return config


@pytest.mark.asyncio
async def test_specific_mood_never_falls_back_to_unrelated_safe_sticker(tmp_path):
    store = ZeroStore(str(tmp_path / "specific-mood.db"))
    await store.add_sticker(make_sticker(1, mood="funny", emoji="😂"))
    client = SimpleNamespace(send_file=AsyncMock(return_value=SimpleNamespace(id=99)))
    brain = object.__new__(ZeroBrain)
    brain.config = load_config()
    brain.store = store
    brain._client = client
    brain._sticker_send_lock = asyncio.Lock()

    outcome = await brain._send_sticker_once(-1001, "sad", direct_request=True)

    client.send_file.assert_not_awaited()
    assert outcome.reason == "no_relevant_candidate"


@pytest.mark.asyncio
async def test_direct_request_send_is_awaited_before_reply_returns():
    brain = object.__new__(ZeroBrain)
    brain.config = SimpleNamespace(stickers=SimpleNamespace(enabled=True))
    sent: list[tuple[int, str, bool]] = []

    async def fake_send(chat_id: int, mood: str, direct_request: bool = False, **_kwargs):
        sent.append((chat_id, mood, direct_request))
        return SimpleNamespace(sent=True, reason="sent")

    brain._send_sticker_async = fake_send
    result = await brain._maybe_reply_with_sticker("چشم قربان", -1001, "استیکرشو بفرست")

    assert result == "چشم قربان"
    assert sent == [(-1001, "react", True)]


@pytest.mark.asyncio
async def test_classifier_canonicalizes_reaction_tag_to_react():
    store = SimpleNamespace(update_sticker_classification=AsyncMock())
    classifier = StickerClassifier(load_config(), store)
    sticker = make_sticker(2)
    sticker.vision_tags = "reaction,face"

    await classifier.classify_sticker(sticker)

    mood_tags = store.update_sticker_classification.await_args.args[1].split(",")
    assert "react" in mood_tags
    assert "reaction" not in mood_tags


@pytest.mark.asyncio
async def test_library_normalizes_legacy_reaction_alias(tmp_path):
    store = ZeroStore(str(tmp_path / "alias.db"))
    await store.add_sticker(make_sticker(3, mood="reaction", emoji="🤔"))
    config = load_config()
    library = StickerLibrary(config, store, config.stickers)

    chosen = await library.get_random_sticker(mood="react", min_quality=0.45)

    assert chosen is not None
    assert chosen.doc_id == 3


@pytest.mark.asyncio
async def test_library_rejects_spam_even_when_quality_is_high(tmp_path):
    store = ZeroStore(str(tmp_path / "spam.db"))
    await store.add_sticker(make_sticker(4, mood="funny", quality=0.99, spam=0.9))
    config = load_config()
    library = StickerLibrary(config, store, config.stickers)

    chosen = await library.get_random_sticker(mood="funny", min_quality=0.45)

    assert chosen is None


@pytest.mark.asyncio
async def test_send_policy_counts_the_full_hour_not_two_minutes(tmp_path):
    store = ZeroStore(str(tmp_path / "hour.db"))
    await store.record_sticker_send(5, -1001)
    with sqlite_txn(store._conn()) as conn:
        conn.execute("UPDATE sticker_send_history SET sent_at=?", (int(time.time()) - 1800,))
        conn.commit()

    policy = await store.get_sticker_send_policy(-1001)

    assert policy["sent_last_hour"] == 1


@pytest.mark.asyncio
async def test_sender_falls_back_to_real_observation_source():
    store = SimpleNamespace(
        update_sticker_last_message=AsyncMock(),
        increment_sticker_usage=AsyncMock(),
        mark_sticker_recent_saved=AsyncMock(),
        update_sticker_file_reference=AsyncMock(),
    )
    client = SimpleNamespace(
        send_file=AsyncMock(side_effect=RuntimeError("stale reference")),
        forward_messages=AsyncMock(return_value=SimpleNamespace(id=501)),
    )
    sticker = make_sticker(6)
    sticker.source_chat_id = -2002
    sticker.source_message_id = 77
    sender = StickerSender(load_config(), store, client)

    ok = await sender.send_sticker(-1001, StickerCandidate(sticker=sticker))

    assert ok is True
    client.forward_messages.assert_awaited_once_with(
        entity=-1001, messages=77, from_peer=-2002,
    )


@pytest.mark.asyncio
async def test_automatic_chance_never_blocks_a_direct_request(tmp_path):
    store = ZeroStore(str(tmp_path / "chance.db"))
    await store.add_sticker(make_sticker(7, mood="funny", emoji="😂"))
    client = SimpleNamespace(send_file=AsyncMock(return_value=SimpleNamespace(id=700)))
    brain = object.__new__(ZeroBrain)
    brain.config = load_config()
    brain.config.stickers.send_chance = 0.0
    brain.store = store
    brain._client = client
    brain._sticker_send_lock = asyncio.Lock()
    with patch("zero.brain_media.random.random", return_value=0.9):
        automatic = await brain._send_sticker_once(-1001, "funny", direct_request=False)
    direct = await brain._send_sticker_once(-1001, "funny", direct_request=True)
    assert automatic.reason == "chance_rejected"
    assert direct.sent is True
    assert client.send_file.await_count == 1


@pytest.mark.asyncio
async def test_retry_never_reuses_the_only_recent_sticker(tmp_path):
    store = ZeroStore(str(tmp_path / "retry.db"))
    await store.add_sticker(make_sticker(8, mood="react", emoji="😐"))
    await store.record_sticker_send(8, -1001, trigger_type="direct")
    client = SimpleNamespace(send_file=AsyncMock(return_value=SimpleNamespace(id=800)))
    brain = object.__new__(ZeroBrain)
    brain.config = load_config()
    brain.store = store
    brain._client = client
    brain._sticker_send_lock = asyncio.Lock()
    outcome = await brain._send_sticker_once(
        -1001, "react", direct_request=True, retry_request=True
    )
    assert outcome.reason == "repeat_window"
    client.send_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_and_automatic_sticker_limits_are_independent(tmp_path):
    store = ZeroStore(str(tmp_path / "trigger-types.db"))
    await store.record_sticker_send(9, -1001, trigger_type="auto")
    await store.record_sticker_send(10, -1001, trigger_type="direct")
    automatic = await store.get_sticker_send_policy(-1001, trigger_type="auto")
    direct = await store.get_sticker_send_policy(-1001, trigger_type="direct")
    assert automatic["sent_last_hour"] == 1
    assert direct["sent_last_hour"] == 1


def test_contextual_direct_requests_cover_more_than_six_moods():
    assert detect_mood_from_user("برای تولدم یه استیکر بفرست") == "celebrate"
    assert detect_mood_from_user("یه استیکر تایید بفرست") == "approve"
    assert detect_mood_from_user("یه استیکر در حال فکر بفرست") == "thinking"
    assert detect_mood_from_user("یه استیکر دعا بفرست") == "pray"


@pytest.mark.asyncio
async def test_generic_direct_fallback_is_explicit_and_explainable(tmp_path):
    store = ZeroStore(str(tmp_path / "generic.db"))
    await store.add_sticker(make_sticker(11, mood="", emoji=""))
    client = SimpleNamespace(send_file=AsyncMock(return_value=SimpleNamespace(id=1100)))
    brain = object.__new__(ZeroBrain)
    brain.config = load_config()
    brain.store = store
    brain._client = client
    brain._sticker_send_lock = asyncio.Lock()
    outcome = await brain._send_sticker_once(-1001, "react", direct_request=True)
    assert outcome.sent is True
    assert outcome.fallback_level == "generic_direct"
    assert outcome.relevance_score == pytest.approx(0.45 + 0.15 * 0.9)
