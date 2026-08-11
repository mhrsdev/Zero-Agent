from conftest import CONFIG_RUNTIME
import asyncio

import pytest

from zero.telegram_search import (
    TelegramIntent,
    TelegramSearchContextBuilder,
    TelegramSearchConversationState,
    TelegramSearchIntentDetector,
    TelegramSearchItem,
    TelegramSearchRequest,
    TelegramSearchResult,
    TelegramSearchHybridRouter,
    TelegramSearchClient,
    dedup_items,
    normalize_target,
)


def test_intents_and_target_normalization():
    d = TelegramSearchIntentDetector()
    assert d.detect("زیرو تو تلگرام درباره Gemini سرچ کن").name == "telegram_message_search"
    assert d.detect("کانال خوب درباره AI پیدا کن").name == "channel_discovery"
    assert d.detect("توی کانال‌هایی که عضوی درباره OpenAI بگرد").name == "joined_dialog_search"
    assert d.detect("این لینک https://t.me/s/example/12 چیه؟").name == "channel_inspection"
    assert normalize_target("https://t.me/s/example/12") == "example"


def test_state_is_sender_chat_scoped_and_ttl():
    state = TelegramSearchConversationState(ttl_seconds=1)
    req = TelegramSearchRequest("t", 10, 20, query="Gemini")
    state.save(req)
    assert state.get(TelegramSearchRequest("x", 10, 20, query="follow-up")) is req
    assert state.get(TelegramSearchRequest("x", 11, 20, query="follow-up")) is None


def test_dedup_and_context_budget_are_bounded():
    a = TelegramSearchItem("joined_dialogs", text="same", link="https://t.me/x/1", confidence=.8)
    b = TelegramSearchItem("telegram_global", text="same", link="https://t.me/x/1", confidence=.8)
    unique = dedup_items([a, b])
    assert len(unique) == 1
    context = TelegramSearchContextBuilder().build([TelegramSearchResult("joined_dialogs", "ok", "q", unique)], max_chars=300)
    assert len(context) <= 300
    assert "TELEGRAM_RESULT" in context


class FakeProvider:
    def __init__(self, name, items):
        self.name, self.items, self.calls = name, items, 0

    async def search(self, request):
        self.calls += 1
        return TelegramSearchResult(self.name, "ok", request.query, list(self.items), len(self.items))


@pytest.mark.asyncio
async def test_state_survives_restart_and_limits_are_persistent(tmp_path):
    from zero.storage import ZeroStore
    db=tmp_path/'state.db'; first=ZeroStore(str(db)); state=TelegramSearchConversationState(store=first)
    request=TelegramSearchRequest('trace',10,20,search_session_id='session',query='Gemini')
    await state.save_persistent(request)
    restored=await TelegramSearchConversationState(store=ZeroStore(str(db))).restore_persistent(TelegramSearchRequest('new',10,20,query='فقط فارسی',intent='none'))
    assert restored and restored.query == 'Gemini'
    assert await TelegramSearchConversationState(store=ZeroStore(str(db))).restore_persistent(TelegramSearchRequest('x',11,20,query='x',intent='none')) is None
    store=ZeroStore(str(db)); assert (await store.consume_telegram_search_limit(account_scope='session',kind='global',daily_limit=1))[0]
    assert not (await store.consume_telegram_search_limit(account_scope='session',kind='global',daily_limit=1))[0]


@pytest.mark.asyncio
async def test_cache_persists_and_unavailable_is_not_cached(tmp_path):
    from zero.config import ZeroConfig
    from zero.storage import ZeroStore
    class Provider:
        def __init__(self, ok): self.calls=0; self.ok=ok
        async def search(self, req):
            self.calls += 1
            return TelegramSearchResult('joined_dialogs','ok' if self.ok else 'unavailable',req.query,[TelegramSearchItem('joined_dialogs',text='x',username='public_x',link='https://t.me/public_x/1',message_id=1,confidence=.8)] if self.ok else [])
    class Router:
        def __init__(self, store, provider): self.state=TelegramSearchConversationState(store=store); self.provider=provider
        async def search(self, req): return [await self.provider.search(req)]
    cfg=ZeroConfig.load(CONFIG_RUNTIME); store=ZeroStore(str(tmp_path/'cache.db')); await store.set_setting('tgsearch_enabled','true')
    good=Provider(True); client=TelegramSearchClient(cfg,store); client.router=Router(store,good); req=lambda q: TelegramSearchRequest('t',1,2,query=q,intent='joined_dialog_search',requested_sources=('joined_dialogs',))
    await client.search_request(req('cacheable')); await client.search_request(req('cacheable')); assert good.calls == 1
    bad=Provider(False); client2=TelegramSearchClient(cfg,ZeroStore(str(tmp_path/'cache.db'))); client2.router=Router(client2.store,bad); await client2.search_request(req('unavailable')); await client2.search_request(req('unavailable')); assert bad.calls == 2
