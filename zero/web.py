from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

from .config import ZeroConfig
from .google_grounding import GoogleGroundingSearch
from .router import IndependentRouter
from .web_search.cache import TTLCache
from .web_search.context import WebContextBuilder
from .web_search.extraction import WebExtractor
from .web_search.intent import SearchIntentDetector, is_current_market_query
from .web_search.models import SearchIntent, SearchOutcome, SearchResult
from .web_search.orchestrator import SearchOrchestrator
from .web_search.pipeline import SearchPipeline
from .web_search.providers.base import ProviderRegistry
from .web_search.providers.searxng import SearXNGProvider
from .web_search.providers.tavily import TavilyProvider
from .web_search.providers.wigolo import WigoloProvider
from .web_search.query import QueryRewriter
from .web_search.transport import ConnectionPoolTransport
from .web_search.truth import TruthfulnessGuard

logger = logging.getLogger('zero.web')


@dataclass(slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str = ''
    source: str = 'web'
    publisher: str = ''
    date: str = ''
    relevant_extract: str = ''


class ExplicitCommandIntentDetector:
    """Shared natural-language detector used by every web-capable feature."""

    def detect(self, text: str, *, reply_text: str = '') -> SearchIntent:
        return SearchIntentDetector().detect(text, reply_text=reply_text)


def needs_web_search(text: str, *, reply_text: str = '') -> bool:
    """Return whether the shared research planner needs fresh web evidence."""
    return SearchIntentDetector().detect(text, reply_text=reply_text).needed


def is_deep_search_request(text: str) -> bool:
    low = (text or '').casefold().replace('‌', ' ')
    return bool(re.search(r'(?:^|\s)/deep(?:_|-)?search\b|\bdeep\s+search\b|(?:سرچ|جستجو|جستجوی|جست\s+وجو|جست\s+وجوی|تحقیق)\s+(?:خیلی\s+)?عمیق', low))


def is_current_price_or_market_query(text: str) -> bool:
    return is_current_market_query(text)


def build_search_query(current_text: str, *, reply_text: str = '', recent_messages: list[dict] | None = None) -> str:
    return QueryRewriter().rewrite(current_text, reply_text=reply_text, recent_messages=recent_messages).query


class HybridWeb:
    """Provider-neutral web search with direct URL inspection and local fallback."""

    def __init__(self, config: ZeroConfig, store=None, *, transport=None, primary=None, **_ignored):
        self.config = config
        self.store = store
        self._transport = transport or ConnectionPoolTransport(
            user_agent='Zero-LocalSearch/1.0',
            allowed_private_endpoints={
                ('http', '127.0.0.1', 8888),
                ('http', '127.0.0.1', 3333),
            },
        )
        self._primary = primary or GoogleGroundingSearch(config, IndependentRouter(config), store)

        registry = ProviderRegistry()
        tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
        self._tavily_configured = bool(config.web.tavily_enabled)
        self._tavily_available = self._tavily_configured and bool(tavily_key)
        if self._tavily_available:
            registry.register(TavilyProvider(
                tavily_key,
                self._transport,
                max_results=config.web.max_search_results,
                timeout=config.web.request_timeout_seconds,
            ))
        elif self._tavily_configured:
            logger.warning("TAVILY_UNAVAILABLE reason=missing_api_key")
        if config.web.wigolo_enabled and config.web.wigolo_base_url:
            registry.register(WigoloProvider(
                config.web.wigolo_base_url,
                self._transport,
                max_results=config.web.max_search_results,
                timeout=config.web.request_timeout_seconds,
            ))
        provider_args = {
            'base_url': config.web.searxng_base_url,
            'transport': self._transport,
            'max_results': config.web.max_search_results,
            'timeout': config.web.request_timeout_seconds,
        }
        if config.web.searxng_base_url:
            registry.register(SearXNGProvider(
                **provider_args, engines=('google cse',), name='searxng-google', priority=10,
            ))
            registry.register(SearXNGProvider(
                **provider_args, engines=('brave', 'startpage'),
                name='searxng-brave-startpage', priority=20,
            ))
            registry.register(SearXNGProvider(
                **provider_args, engines=('duckduckgo',),
                name='searxng-duckduckgo', priority=30,
            ))
        self._local_pipeline = SearchPipeline(
            registry=registry,
            retries=config.web.provider_retries,
            provider_timeout=config.web.request_timeout_seconds,
            max_results=config.web.max_search_results,
            max_fetch_pages=config.web.max_fetch_pages_per_query,
            max_parallel_providers=1,
            cache=TTLCache(config.web.cache_ttl_seconds),
            context_builder=WebContextBuilder(config.web.context_max_chars),
            intent_detector=ExplicitCommandIntentDetector(),
            extractor=WebExtractor(self._transport, max_extract_chars=1800, request_timeout=config.web.request_timeout_seconds),
            truth_guard=TruthfulnessGuard(),
        )
        self._orchestrator = SearchOrchestrator(self._primary, self._local_pipeline)
        self.truth_guard = self._local_pipeline.truth_guard

    def enabled(self) -> bool:
        return bool(self.config.web.enabled)

    async def is_tool_enabled(self) -> bool:
        if self.store:
            db_val = await self.store.get_setting('web_enabled')
            if db_val is not None and db_val not in ('null', 'None', ''):
                return str(db_val).lower() == 'true'
        return self.enabled()

    async def run(self, text: str, **kwargs) -> SearchOutcome:
        kwargs.setdefault('force_search', True)
        return await self._orchestrator.run(text, **kwargs)

    async def search_hits(self, raw_query: str, enabled_override: Optional[bool] = None) -> list[SearchHit]:
        """Legacy hit-shaped view of :meth:`run`, awaited on the caller's loop.

        This deliberately has no synchronous counterpart. The previous
        ``search()`` wrapper called ``asyncio.run(self.run(...))`` and its only
        caller reached it through ``asyncio.to_thread``. That started a second,
        throwaway event loop while the same ``HybridWeb`` was in use on the main
        loop, and the shared ``ConnectionPoolTransport`` caches one
        ``asyncio.Semaphore`` per host: whichever loop contended first owned it,
        so the semaphore could end up bound to a closed loop and every later
        request raised ``RuntimeError: ... is bound to a different event loop``.
        The pipeline recorded that as a generic provider failure, so news and
        knowledge digests returned empty results for the life of the process.
        """
        if enabled_override is False or (enabled_override is None and not self.enabled()):
            return []
        outcome = await self.run(raw_query)
        return [self._compat_hit(result) for result in outcome.results]

    async def health_check(self) -> tuple[bool, str]:
        if not self.enabled():
            return False, 'web search disabled'
        if self.config.web.wigolo_enabled and self.config.web.wigolo_base_url:
            try:
                raw = await self._transport.get_text(
                    f'{self.config.web.wigolo_base_url.rstrip("/")}/health',
                    self.config.web.request_timeout_seconds,
                    64_000,
                )
                if json.loads(raw).get('status') == 'healthy':
                    return True, 'wigolo'
            except Exception:
                pass
        if self._tavily_configured:
            if self._tavily_available:
                return True, 'tavily'
            return False, 'tavily API key missing'
        if self.config.web.searxng_base_url:
            return True, 'local-fallback'
        primary_ok, primary_error = await self._primary.health_check()
        if primary_ok:
            return True, 'google-grounding'
        return False, primary_error or 'search providers unavailable'

    def invalidate_cache(self) -> None:
        self._orchestrator.invalidate_cache()

    def guard_answer(self, answer: str, results: list[SearchResult], *, trace_id: str = '-', trusted_text: str = ''):
        return self.truth_guard.guard_answer(answer, results, trusted_text=trusted_text)

    def mark_response_sent(self, *, trace_id: str, result_count: int, guarded: bool) -> None:
        logger.info(
            'WEB_SEARCH_RESPONSE_SENT trace_id=%s result_count=%s guarded=%s',
            trace_id, result_count, guarded,
        )

    def close(self) -> None:
        close = getattr(self._transport, 'close', None)
        if close:
            close()

    @staticmethod
    def _compat_hit(result: SearchResult) -> SearchHit:
        return SearchHit(
            title=result.title,
            url=result.url,
            snippet=result.snippet,
            source=result.provider,
            publisher=result.publisher,
            date=result.published_at,
            relevant_extract=result.relevant_extract,
        )
