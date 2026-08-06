import pytest
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from zero.brain import ZeroBrain, sticker_context_allowed, user_requests_sticker, sticker_retry_feedback
from zero.config import ZeroConfig
from zero.storage import ZeroStore
from zero.stickers.observer import StickerObserver


def test_direct_sticker_phrases_and_retry_feedback_are_detected():
    for text in ('یه استیکر بفرست','استیکرشو بفرست','زیرو استیکر','یه استیکر دیگه'):
        assert user_requests_sticker(text)
    assert sticker_retry_feedback('این برای اینجا نبود')
    assert sticker_retry_feedback('یکی دیگه بفرست')


def test_sticker_context_policy_blocks_technical_and_allows_clear_joke():
    assert not sticker_context_allowed('یک سؤال فنی درباره API دارم 😂')
    assert sticker_context_allowed('چه شوخی باحالی بود 😂')
    assert sticker_context_allowed('در بحث فنی استیکر بفرست', direct_request=True)


def test_sticker_runtime_defaults_are_conservative():
    cfg = ZeroConfig.load('/root/zero/config/zero.yaml').stickers
    assert cfg.auto_enabled is True
    assert cfg.chance_percent == 100
    assert cfg.limit_per_hour == 10
    assert cfg.cooldown_seconds == 120
    assert cfg.min_messages_between == 5
    assert cfg.repeat_window == 20


@pytest.mark.asyncio
async def test_sticker_policy_is_chat_scoped_and_counts_messages(tmp_path):
    store = ZeroStore(str(tmp_path / 'sticker-policy.db'))
    await store.record_sticker_send(123, -1001)
    for i in range(4):
        await store.append_recent(-1001, i + 1, 'same-name', 'user', 'message')
    await store.append_recent(-1002, 99, 'same-name', 'user', 'other chat')
    policy = await store.get_sticker_send_policy(-1001)
    assert policy['sent_last_hour'] == 1
    assert policy['messages_since_last'] == 4
    assert (await store.get_sticker_send_policy(-1002))['sent_last_hour'] == 0


@pytest.mark.asyncio
async def test_direct_sticker_request_sends_without_llm_marker():
    brain=object.__new__(ZeroBrain)
    brain.config=SimpleNamespace(stickers=SimpleNamespace(enabled=True))
    sent=[]
    async def fake_send(chat_id,mood,direct_request=False): sent.append((chat_id,mood,direct_request))
    brain._send_sticker_async=fake_send
    result=await brain._maybe_reply_with_sticker('چشم قربان',-1001,'استیکرشو بفرست')
    await asyncio.sleep(0)
    assert result=='چشم قربان'
    assert sent==[(-1001,'react',True)]


@pytest.mark.asyncio
async def test_failed_favorite_does_not_mark_local_saved():
    observer=object.__new__(StickerObserver)
    observer.config=SimpleNamespace(stickers=SimpleNamespace(auto_save_enabled=True))
    observer.account_saver=SimpleNamespace(save_to_favorites=AsyncMock(return_value=False))
    observer.store=SimpleNamespace(mark_sticker_saved=AsyncMock())
    sticker=SimpleNamespace(saved_to_account=False,nsfw_score=0.0,spam_score=0.0,quality_score=0.8,usage_count=3,doc_id=7)
    assert await observer.maybe_auto_save(sticker) is False
    observer.store.mark_sticker_saved.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_sticker_uses_context_and_does_not_need_llm_marker(monkeypatch):
    brain = object.__new__(ZeroBrain)
    brain.config = SimpleNamespace(stickers=SimpleNamespace(enabled=True, auto_enabled=True, send_chance=1.0))
    sent = []

    async def fake_send(chat_id, mood, direct_request=False):
        sent.append((chat_id, mood, direct_request))

    brain._send_sticker_async = fake_send
    monkeypatch.setattr('zero.brain.random.random', lambda: 0.0)
    assert await brain._maybe_reply_with_sticker('خوبه 😄', -1001, 'چه شوخی باحالی بود 😂') == 'خوبه 😄'
    await asyncio.sleep(0)
    assert sent == [(-1001, 'funny', False)]


@pytest.mark.asyncio
async def test_auto_sticker_skips_technical_context(monkeypatch):
    brain = object.__new__(ZeroBrain)
    brain.config = SimpleNamespace(stickers=SimpleNamespace(enabled=True, auto_enabled=True, send_chance=1.0))
    brain._send_sticker_async = AsyncMock()
    monkeypatch.setattr('zero.brain.random.random', lambda: 0.0)
    result = await brain._maybe_reply_with_sticker('پاسخ فنی', -1001, 'این خطای API چرا میاد؟ 😂')
    await asyncio.sleep(0)
    assert result == 'پاسخ فنی'
    brain._send_sticker_async.assert_not_awaited()
