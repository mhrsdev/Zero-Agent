from __future__ import annotations

import pytest

from zero.web_search.models import QueryPlan, SearchIntent, SearchKind, SearchOutcome, SearchResult
from zero.web_search.orchestrator import SearchOrchestrator
from zero.web_search.pipeline import SearchPipeline
from zero.web_search.providers.base import ProviderRegistry, SearchProvider
from zero.web_search.providers.searxng import SearXNGProvider
from zero.web_search.transport import ConnectionPoolTransport
from zero.config import ZeroConfig
from zero.web import HybridWeb
from zero.google_grounding import GoogleGroundingSearch
from zero.models import RouteResult


class ForcedIntent:
    def detect(self, text: str) -> SearchIntent:
        return SearchIntent(bool(text.strip()), SearchKind.WEB, True, 'explicit_web_search')


class FakePrimary:
    def __init__(self, calls: list[str], *, succeeds: bool):
        self.calls = calls
        self.succeeds = succeeds

    async def run(self, text: str, **kwargs) -> SearchOutcome:
        self.calls.append('google-grounding')
        plan = QueryPlan(original=text, query=text, language='fa')
        intent = SearchIntent(True, SearchKind.WEB, True, 'explicit_web_search')
        if self.succeeds:
            return SearchOutcome(intent, plan, results=[SearchResult('G', 'https://google.example/a', provider='google-grounding')], context='grounded')
        return SearchOutcome(intent, plan, all_providers_failed=True)


class EmptyLocal:
    def __init__(self):
        self.calls = 0
        self.invalidations = 0

    async def run(self, text: str, **kwargs):
        self.calls += 1
        plan = QueryPlan(original=text, query=text, language='fa')
        return SearchOutcome(SearchIntent(True, SearchKind.WEB, True, 'explicit_web_search'), plan, all_providers_failed=True)

    def invalidate_cache(self):
        self.invalidations += 1


class TierProvider(SearchProvider):
    def __init__(self, name: str, priority: int, calls: list[str], *, succeeds: bool):
        self.name = name
        self.priority = priority
        self.calls = calls
        self.succeeds = succeeds

    async def search(self, request):
        self.calls.append(self.name)
        if self.succeeds:
            return [SearchResult(f'{request.query} {self.name}', f'https://{self.name}.example/a', snippet=request.query, provider=self.name)]
        return []


class JsonTransport:
    def __init__(self):
        self.urls: list[str] = []

    async def get_text(self, url: str, timeout: float, max_bytes: int) -> str:
        self.urls.append(url)
        return '{"results":[{"title":"T","url":"https://example.com/a","content":"S","engine":"google cse"}]}'


class RecordingExtractor:
    def __init__(self):
        self.limit = 0

    async def extract_many(self, results, query, limit):
        self.limit = limit
        for result in results[:limit]:
            result.relevant_extract = f'extracted:{result.title}'
        return results


class ManyDomainProvider(SearchProvider):
    def __init__(self, name: str, priority: int, delay: float = 0.0):
        self.name, self.priority, self.delay, self.calls = name, priority, delay, 0

    async def search(self, request):
        import asyncio
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return [
            SearchResult(f'Kimi 3 report {self.name} {i}', f'https://{self.name}-{self.calls}-{i}.example/article', snippet='Kimi 3 Moonshot AI model research', provider=self.name)
            for i in range(12)
        ] + [SearchResult('Gold price', f'https://irrelevant-{self.name}.example/gold', snippet='قیمت طلا', provider=self.name)]


def local_pipeline(calls: list[str]) -> SearchPipeline:
    registry = ProviderRegistry()
    registry.register(TierProvider('searxng-google', 10, calls, succeeds=False))
    registry.register(TierProvider('searxng-brave-startpage', 20, calls, succeeds=False))
    registry.register(TierProvider('searxng-duckduckgo', 30, calls, succeeds=True))
    return SearchPipeline(registry=registry, retries=1, intent_detector=ForcedIntent())


@pytest.mark.asyncio
async def test_orchestrator_uses_duckduckgo_only_after_every_earlier_tier_failed():
    calls: list[str] = []
    orchestrator = SearchOrchestrator(FakePrimary(calls, succeeds=False), local_pipeline(calls))

    outcome = await orchestrator.run('موضوع تست', trace_id='tier-test')

    assert calls == [
        'google-grounding',
        'searxng-google',
        'searxng-brave-startpage',
        'searxng-duckduckgo',
    ]
    assert outcome.results[0].provider == 'searxng-duckduckgo'


@pytest.mark.asyncio
async def test_orchestrator_retries_six_times_when_all_search_tiers_are_empty():
    calls = []
    local = EmptyLocal()
    orchestrator = SearchOrchestrator(FakePrimary(calls, succeeds=False), local)
    outcome = await orchestrator.run('موضوع تست', trace_id='retry-test')
    assert outcome.all_providers_failed is True
    assert calls == ['google-grounding'] * 6
    assert local.calls == 6
    assert local.invalidations == 5


@pytest.mark.asyncio
async def test_orchestrator_stops_when_google_grounding_succeeds():
    calls: list[str] = []
    orchestrator = SearchOrchestrator(FakePrimary(calls, succeeds=True), local_pipeline(calls))

    outcome = await orchestrator.run('موضوع تست', trace_id='primary-test')

    assert calls == ['google-grounding']
    assert outcome.results[0].provider == 'google-grounding'


@pytest.mark.asyncio
async def test_deep_search_checks_all_provider_tiers_and_extracts_pages():
    calls: list[str] = []
    registry = ProviderRegistry()
    for name, priority in (('google', 10), ('brave', 20), ('duckduckgo', 30)):
        registry.register(TierProvider(name, priority, calls, succeeds=True))
    extractor = RecordingExtractor()
    pipeline = SearchPipeline(registry=registry, retries=1, max_results=6, max_fetch_pages=0, extractor=extractor, intent_detector=ForcedIntent())

    outcome = await pipeline.run('موضوع تحقیق', trace_id='deep-test', deep=True)

    assert set(calls) == {'google', 'brave', 'duckduckgo'}
    assert all(calls.count(name) == 4 for name in set(calls))
    assert len(outcome.results) == 3
    assert extractor.limit == 15
    assert all(result.relevant_extract.startswith('extracted:') for result in outcome.results)


@pytest.mark.asyncio
async def test_deep_search_targets_fifteen_sites_caps_thirty_and_drops_unrelated_results():
    registry = ProviderRegistry()
    providers = [ManyDomainProvider('google', 10, 0.05), ManyDomainProvider('brave', 20, 0.05), ManyDomainProvider('duckduckgo', 30, 0.05)]
    for provider in providers:
        registry.register(provider)
    extractor = RecordingExtractor()
    pipeline = SearchPipeline(registry=registry, retries=1, max_results=6, max_fetch_pages=0, extractor=extractor, intent_detector=ForcedIntent())

    import time
    started = time.monotonic()
    outcome = await pipeline.run('Kimi 3', trace_id='deep-diversity', deep=True)
    elapsed = time.monotonic() - started

    domains = {result.url.split('/')[2] for result in outcome.results}
    assert 15 <= len(outcome.results) <= 30
    assert len(domains) == len(outcome.results)
    assert all('Kimi 3' in f'{result.title} {result.snippet}' for result in outcome.results)
    assert all(provider.calls >= 2 for provider in providers)
    assert extractor.limit == 15
    assert len(outcome.context) <= 20_000
    assert elapsed < 0.35


@pytest.mark.asyncio
async def test_searxng_tier_sends_only_its_declared_engines():
    transport = JsonTransport()
    provider = SearXNGProvider(
        'http://127.0.0.1:8888', transport,
        engines=('google cse',), name='searxng-google', priority=10,
    )

    results = await provider.search(QueryPlan(original='q', query='q', language='fa'))

    assert 'engines=google+cse' in transport.urls[0]
    assert results[0].provider == 'searxng-google'
    assert results[0].metadata['engine'] == 'google cse'


@pytest.mark.asyncio
async def test_searxng_filters_declared_engines_before_result_cap():
    class MixedTransport:
        async def get_text(self, url, timeout, max_bytes):
            rows = [{'title': f'noise-{i}', 'url': f'https://noise{i}.example', 'engine': 'brave'} for i in range(6)]
            rows.append({'title': 'wanted', 'url': 'https://wanted.example', 'engine': 'google cse'})
            return __import__('json').dumps({'results': rows})

    provider = SearXNGProvider('http://127.0.0.1:8888', MixedTransport(), max_results=1, engines=('google cse',))
    results = await provider.search(QueryPlan(original='q', query='q', language='en'))

    assert [result.title for result in results] == ['wanted']


@pytest.mark.asyncio
async def test_google_grounding_uses_reply_url_as_search_query():
    class Router:
        def __init__(self):
            self.prompt = ''

        async def complete_search(self, prompt, *, max_output_tokens):
            self.prompt = prompt
            return RouteResult(
                text='grounded', provider='gemini', model='test', attempts=1,
                metadata={'raw': {'candidates': [{'groundingMetadata': {'groundingChunks': [{'web': {'uri': 'https://example.com/article', 'title': 'Article'}}]}}]}},
            )

    config = ZeroConfig.load('/root/zero/config/zero.example.yaml')
    router = Router()
    outcome = await GoogleGroundingSearch(config, router).run(
        'این لینک رو باز کن و بررسی کن', reply_text='https://example.com/article',
    )

    assert router.prompt == 'https://example.com/article'
    assert outcome.results[0].url == 'https://example.com/article'


@pytest.mark.asyncio
async def test_hybrid_web_wires_grounding_primary_to_google_searxng_fallback():
    calls: list[str] = []
    config = ZeroConfig.load('/root/zero/config/zero.example.yaml')
    config.web.enabled = True
    config.web.searxng_base_url = 'http://127.0.0.1:8888'
    transport = JsonTransport()
    web = HybridWeb(
        config,
        transport=transport,
        primary=FakePrimary(calls, succeeds=False),
    )

    outcome = await web.run('موضوع تست', trace_id='wire-test')

    assert calls == ['google-grounding']
    assert outcome.results[0].provider == 'searxng-google'
    assert 'engines=google+cse' in transport.urls[0]


def test_transport_private_allowlist_is_exact():
    transport = ConnectionPoolTransport(
        allowed_private_endpoints={('http', '127.0.0.1', 8888)},
    )
    connection = transport._new_connection(('http', '127.0.0.1', 8888), 1)
    connection.close()
    with pytest.raises(ValueError, match='private or unresolved'):
        transport._new_connection(('http', '127.0.0.1', 9999), 1)


def test_transport_pins_validated_public_ip(monkeypatch):
    import socket
    monkeypatch.setattr('zero.web_search.extraction._resolved_public_addresses', lambda host, port: ('93.184.216.34',))
    captured = {}
    marker = object()
    def fake_create_connection(address, timeout=None, source_address=None):
        captured['address'] = address
        return marker
    monkeypatch.setattr(socket, 'create_connection', fake_create_connection)
    transport = ConnectionPoolTransport()

    connection = transport._new_connection(('https', 'example.com', 443), 1)
    assert connection._create_connection(('example.com', 443), 1, None) is marker
    assert captured['address'] == ('93.184.216.34', 443)
