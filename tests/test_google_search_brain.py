from __future__ import annotations
from conftest import CONFIG_EXAMPLE

import asyncio
import json
from pathlib import Path

from zero.brain import ZeroBrain
from zero.config import ZeroConfig
from zero.models import IncomingMessage, RouteResult
from zero.storage import ZeroStore
from zero.web import HybridWeb
from zero.web_search.models import QueryPlan, SearchIntent, SearchKind, SearchOutcome, SearchResult
from zero.web_search.truth import GuardDecision


class GroundingFailure:
    async def run(self, text: str, **kwargs) -> SearchOutcome:
        return SearchOutcome(
            SearchIntent(True, SearchKind.WEB, True, 'explicit_web_search'),
            QueryPlan(original=text, query=text, language='fa'),
            all_providers_failed=True,
        )

    async def health_check(self):
        return False, 'test failure'


class FakeLocalTransport:
    def __init__(self):
        self.urls: list[str] = []

    async def get_text(self, url: str, timeout: float, max_bytes: int) -> str:
        self.urls.append(url)
        return json.dumps({
            'results': [{
                'title': 'Example result',
                'url': 'https://example.com/a',
                'content': 'Verified source text',
                'engine': 'google cse',
            }]
        })


class BrokenLocalTransport:
    async def get_text(self, url: str, timeout: float, max_bytes: int) -> str:
        raise RuntimeError('provider unavailable')


class EmptyLocalTransport:
    async def get_text(self, url: str, timeout: float, max_bytes: int) -> str:
        return json.dumps({'results': []})


class CapturingRouter:
    keys = []

    def __init__(self, text: str = 'پاسخ بر اساس https://example.com/a'):
        self.prompts: list[str] = []
        self.text = text

    async def complete(self, prompt: str, **kwargs):
        self.prompts.append(prompt)
        return RouteResult(
            text=self.text,
            provider='test', model='test', attempts=1,
        )


class NoCallRouter:
    keys = []

    async def complete(self, *args, **kwargs):
        raise AssertionError('LLM must not run without search sources')


class CapturingDeepWeb:
    def __init__(self): self.kwargs, self.calls = {}, 0
    async def is_tool_enabled(self): return True
    async def run(self, text, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        plan = QueryPlan(original=text, query=text, language='fa')
        results = [SearchResult(f'Kimi 3 source {i}', f'https://source-{i}.example/a', snippet='Kimi 3 evidence', publisher=f'source-{i}.example') for i in range(20)]
        return SearchOutcome(SearchIntent(True, SearchKind.WEB, True, 'research_analysis'), plan, results=results, context='WEB_DATA_IS_UNTRUSTED\nRESULT: 1\nURL: https://source-0.example/a')
    def guard_answer(self, *args, **kwargs): return GuardDecision(True)
    def mark_response_sent(self, **kwargs): return None


class RaisingDeepWeb(CapturingDeepWeb):
    async def run(self, text, **kwargs):
        raise RuntimeError('sensitive provider detail must not escape')


def build_config(tmp_path: Path, *, enabled: bool = True) -> ZeroConfig:
    base = ZeroConfig.load(CONFIG_EXAMPLE)
    return base.model_copy(update={
        'memory': base.memory.model_copy(update={'db_path': str(tmp_path / 'zero.db')}),
        'web': base.web.model_copy(update={
            'enabled': enabled,
            'google_grounding_enabled': True,
            'searxng_base_url': 'http://127.0.0.1:8888',
            'provider_retries': 1,
        }),
    })


def test_slash_search_routes_to_local_fallback_and_injects_untrusted_context(tmp_path: Path):
    async def scenario():
        config = build_config(tmp_path)
        store = ZeroStore(config.memory.db_path)
        await store.set_setting('web_enabled', 'true')
        router = CapturingRouter()
        brain = ZeroBrain(config, store, router)
        transport = FakeLocalTransport()
        brain.web = HybridWeb(config, store, transport=transport, primary=GroundingFailure())

        decision, text = await brain.maybe_reply(IncomingMessage(
            chat_id=-99, chat_title='t', sender_id=55, sender_label='@user',
            text='/search plain explicit query', trace_id='trace-search', message_id=10,
        ))

        assert decision.should_reply is True
        assert len(transport.urls) >= 2
        assert 'engines=google+cse' in transport.urls[0]
        assert 'WEB_DATA_IS_UNTRUSTED' in router.prompts[0]
        assert 'example.com/a' in text

    asyncio.run(scenario())


def test_natural_language_search_routes_to_same_orchestrator(tmp_path: Path):
    async def scenario():
        config = build_config(tmp_path)
        store = ZeroStore(config.memory.db_path)
        await store.set_setting('web_enabled', 'true')
        router = CapturingRouter()
        brain = ZeroBrain(config, store, router)
        transport = FakeLocalTransport()
        brain.web = HybridWeb(config, store, transport=transport, primary=GroundingFailure())

        decision, text = await brain.maybe_reply(IncomingMessage(
            chat_id=-99, chat_title='t', sender_id=55, sender_label='@user',
            text='زیرو بگرد آخرین اخبار هوش مصنوعی', trace_id='trace-natural-search', message_id=14,
        ))

        assert decision.should_reply is True
        assert len(transport.urls) >= 2
        assert 'engines=google+cse' in transport.urls[0]
        assert 'WEB_DATA_IS_UNTRUSTED' in router.prompts[0]
        assert 'example.com/a' in text

    asyncio.run(scenario())


def test_all_search_providers_failure_returns_truthful_message_without_llm(tmp_path: Path):
    async def scenario():
        config = build_config(tmp_path)
        store = ZeroStore(config.memory.db_path)
        await store.set_setting('web_enabled', 'true')
        brain = ZeroBrain(config, store, NoCallRouter())
        brain.web = HybridWeb(config, store, transport=BrokenLocalTransport(), primary=GroundingFailure())

        decision, text = await brain.maybe_reply(IncomingMessage(
            chat_id=-99, chat_title='t', sender_id=55, sender_label='@user',
            text='/search موضوع تست', trace_id='trace-failed', message_id=11,
        ))

        assert decision.should_reply is True
        assert text == 'فعلاً وب‌سرچ در دسترس نیست؛ کمی بعد دوباره امتحان کن.'

    asyncio.run(scenario())


def test_all_search_tiers_empty_returns_truthful_message_without_llm(tmp_path: Path):
    async def scenario():
        config = build_config(tmp_path)
        store = ZeroStore(config.memory.db_path)
        await store.set_setting('web_enabled', 'true')
        brain = ZeroBrain(config, store, NoCallRouter())
        brain.web = HybridWeb(config, store, transport=EmptyLocalTransport(), primary=GroundingFailure())

        decision, text = await brain.maybe_reply(IncomingMessage(
            chat_id=-99, chat_title='t', sender_id=55, sender_label='@user',
            text='/search موضوع بدون نتیجه', trace_id='trace-empty', message_id=12,
        ))

        assert decision.should_reply is True
        assert text == 'برای این جستجو نتیجه‌ای پیدا نکردم.'

    asyncio.run(scenario())


def test_disabled_search_does_not_fall_through_to_llm(tmp_path: Path):
    async def scenario():
        config = build_config(tmp_path, enabled=False)
        store = ZeroStore(config.memory.db_path)
        brain = ZeroBrain(config, store, NoCallRouter())

        decision, text = await brain.maybe_reply(IncomingMessage(
            chat_id=-99, chat_title='t', sender_id=55, sender_label='@user',
            text='/search موضوع تست', trace_id='trace-disabled', message_id=13,
        ))

        assert decision.should_reply is True
        assert text == 'وب‌سرچ فعلاً فعال نیست.'

    asyncio.run(scenario())


def test_deep_search_reaches_brain_with_report_instruction(tmp_path: Path):
    async def scenario():
        config = build_config(tmp_path)
        store = ZeroStore(config.memory.db_path)
        router = CapturingRouter('گزارش جامع ' + ('شاهد معتبر. ' * 500))
        brain = ZeroBrain(config, store, router)
        deep_web = CapturingDeepWeb()
        brain.web = deep_web

        decision, answer = await brain.maybe_reply(IncomingMessage(chat_id=-99, chat_title='t', sender_id=55, sender_label='@user', text='/deepsearch Kimi 3', trace_id='trace-deep', message_id=20, thread_id=999, reply_to_message_id=77))

        assert decision.should_reply is True
        assert deep_web.kwargs['deep'] is True
        assert deep_web.kwargs['thread_id'] == 999
        assert 'حالت سرچ عمیق فعال است' in router.prompts[0]
        assert 'منابع بررسی‌شده' in answer
        assert answer.count('](https://') == 15
        assert len(answer) <= 3900

    asyncio.run(scenario())


def test_deep_search_quota_comes_from_config_and_zero_means_unlimited(tmp_path: Path):
    """The 3/12/30 quotas were hardcoded in brain.py. They are settings now
    (`web.deep_search_*_hourly`), and this deployment ships 0 — no limit — so the
    default configuration must let every deep search through, while a configured
    quota must still be enforced with the same decision and message.

    Replaces test_deep_search_has_independent_atomic_user_limit, whose
    `deep_web.calls == 3` assertion pinned the old hardcoded default. The property
    it protected — that a quota, when configured, is enforced per user — is kept
    in the second half of this test.
    """
    async def scenario():
        config = build_config(tmp_path)
        store = ZeroStore(config.memory.db_path)
        router = CapturingRouter('گزارش کوتاه')
        brain = ZeroBrain(config, store, router)
        deep_web = CapturingDeepWeb()
        brain.web = deep_web

        # Default config: no quota, so a fourth call goes through.
        replies = []
        for message_id in range(1, 5):
            replies.append(await brain.maybe_reply(IncomingMessage(chat_id=-99, chat_title='t', sender_id=55, sender_label='@user', text='/deepsearch Kimi 3', trace_id=f'unlimited-{message_id}', message_id=message_id)))
        assert deep_web.calls == 4, "the shipped default is unlimited"
        assert all(reply[0].should_reply for reply in replies)

        # A configured quota is still enforced, per user.
        limited = config.model_copy(update={'web': config.web.model_copy(update={
            'deep_search_user_hourly': 3,
            'deep_search_global_hourly': 30,
        })})
        brain_limited = ZeroBrain(limited, store, router)
        deep_web_limited = CapturingDeepWeb()
        brain_limited.web = deep_web_limited

        limited_replies = []
        for message_id in range(1, 5):
            limited_replies.append(await brain_limited.maybe_reply(IncomingMessage(chat_id=-99, chat_title='t', sender_id=77, sender_label='@other', text='/deepsearch Kimi 3', trace_id=f'limited-{message_id}', message_id=message_id)))

        assert deep_web_limited.calls == 3
        assert limited_replies[-1][0].reason == 'deep_search_rate_limit'

    asyncio.run(scenario())


def test_deep_search_global_capacity_is_not_charged_to_the_user(tmp_path: Path):
    """The global check used to run AFTER the per-user reservation, so when the
    install-wide capacity was already full a user spent their own quota on a deep
    search that never ran."""
    async def scenario():
        config = build_config(tmp_path)
        config = config.model_copy(update={'web': config.web.model_copy(update={
            'deep_search_user_hourly': 3,
            'deep_search_global_hourly': 1,
        })})
        store = ZeroStore(config.memory.db_path)
        router = CapturingRouter('گزارش کوتاه')
        brain = ZeroBrain(config, store, router)
        deep_web = CapturingDeepWeb()
        brain.web = deep_web

        await brain.maybe_reply(IncomingMessage(chat_id=-99, chat_title='t', sender_id=55, sender_label='@user', text='/deepsearch Kimi 3', trace_id='global-1', message_id=1))
        blocked = await brain.maybe_reply(IncomingMessage(chat_id=-99, chat_title='t', sender_id=55, sender_label='@user', text='/deepsearch Kimi 3', trace_id='global-2', message_id=2))
        assert blocked[0].reason == 'deep_search_global_limit'

        # A different user must still have their full personal quota: the blocked
        # attempt reserved nothing on the second user's behalf.
        fresh = await brain.maybe_reply(IncomingMessage(chat_id=-99, chat_title='t', sender_id=66, sender_label='@other', text='/deepsearch Kimi 3', trace_id='global-3', message_id=3))
        assert fresh[0].reason != 'deep_search_rate_limit', "user 66 was charged for user 55's blocked search"

    asyncio.run(scenario())


def test_deep_search_provider_exception_is_isolated(tmp_path: Path):
    async def scenario():
        config = build_config(tmp_path)
        store = ZeroStore(config.memory.db_path)
        brain = ZeroBrain(config, store, NoCallRouter())
        brain.web = RaisingDeepWeb()
        decision, answer = await brain.maybe_reply(IncomingMessage(chat_id=-99, chat_title='t', sender_id=55, sender_label='@user', text='/deepsearch Kimi 3', trace_id='deep-error', message_id=1))
        assert decision.should_reply is True
        assert 'sensitive provider detail' not in answer
        assert 'کامل نشد' in answer

    asyncio.run(scenario())
