import asyncio

import pytest

from zero.web_search.models import SearchResult
from zero.web_search.providers.base import ProviderRegistry, SearchProvider
from zero.web_search.query import QueryRewriter
from zero.web_search.state import SearchConversationState


class Provider(SearchProvider):
    name = 'state-test-provider'
    priority = 10

    async def search(self, request):
        return [SearchResult(title=request.query, url='https://example.com/result', snippet=request.query)]


def test_state_is_scoped_by_chat_and_sender_and_expires():
    now = [1000.0]
    state = SearchConversationState(ttl_seconds=300, clock=lambda: now[0])
    state.record(1, 10, 'OpenAI', 'Latest OpenAI news', 'Latest OpenAI news', '', 'trace-a')
    state.record(1, 11, 'طلا', 'قیمت طلا امروز', 'قیمت طلا امروز', '', 'trace-b')
    assert state.lookup(1, 10).subject == 'Latest OpenAI news'
    assert state.lookup(1, 10).subject != state.lookup(1, 11).subject
    now[0] += 301
    assert state.lookup(1, 10) is None


def test_reply_target_state_has_priority_over_root_state():
    state = SearchConversationState(ttl_seconds=300, clock=lambda: 1000.0)
    state.record(1, 10, 'OpenAI', 'Latest OpenAI news', 'Latest OpenAI news', '', 'root', message_id=40)
    state.record(1, 10, 'طلا', 'قیمت طلا امروز', 'قیمت طلا امروز', '', 'reply', message_id=41)
    assert state.lookup(1, 10, reply_to_message_id=41).subject == 'قیمت طلا امروز'
    assert state.lookup(1, 10, reply_to_message_id=40).subject == 'Latest OpenAI news'


def test_domain_override_preserves_explicit_followup_subject():
    plan = QueryRewriter().rewrite('از milli.gold ببین', followup_subject='قیمت طلا امروز')
    assert plan.query == 'قیمت طلا امروز site:milli.gold'
    assert 'OpenAI' not in plan.query


def test_domain_only_without_state_is_not_rewritten_from_recent_global_text():
    assert QueryRewriter().is_domain_only_followup('از milli.gold ببین')
    assert QueryRewriter().is_domain_only_followup('از milli.gold ببین [WEBV2_TEST_abc]')
    assert QueryRewriter().is_domain_only_followup('از milli.gold ببین [WEBV2_PASS_abc]')


@pytest.mark.asyncio
async def test_pipeline_domain_followup_requires_scoped_state():
    from zero.web_search.cache import TTLCache
    from zero.web_search.pipeline import SearchPipeline

    registry = ProviderRegistry()
    registry.register(Provider())
    state = SearchConversationState(ttl_seconds=300)
    pipeline = SearchPipeline(registry=registry, cache=TTLCache(60), state=state)
    missing = await pipeline.run('از milli.gold ببین', chat_id=1, sender_id=10, trace_id='missing')
    assert missing.clarification_required is True
    state.record(1, 10, 'قیمت طلا چنده؟', 'قیمت طلا امروز', 'قیمت طلا امروز', '', 'prior', message_id=5)
    good = await pipeline.run('از milli.gold ببین', chat_id=1, sender_id=10, trace_id='good')
    assert good.plan.query == 'قیمت طلا امروز site:milli.gold'
    other = await pipeline.run('از milli.gold ببین', chat_id=1, sender_id=11, trace_id='other')
    assert other.clarification_required is True
