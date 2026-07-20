from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .cache import TTLCache
from .context import WebContextBuilder
from .dedup import deduplicate_results
from .intent import SearchIntentDetector
from .models import ProviderFailure, SearchIntent, SearchKind, SearchOutcome, SearchResult
from .query import QueryRewriter
from .ranking import ResultRanker
from .truth import NUMERIC_FALLBACK_CATEGORIES, TruthfulnessGuard, numeric_entities
from .state import SearchConversationState

logger = logging.getLogger('zero.web')


class SearchPipeline:
    def __init__(
        self,
        *,
        registry,
        retries: int = 2,
        provider_timeout: float = 12.0,
        max_results: int = 5,
        max_fetch_pages: int = 2,
        max_parallel_providers: int = 4,
        cache: TTLCache | None = None,
        extractor=None,
        context_builder: WebContextBuilder | None = None,
        intent_detector: SearchIntentDetector | None = None,
        query_rewriter: QueryRewriter | None = None,
        ranker: ResultRanker | None = None,
        truth_guard: TruthfulnessGuard | None = None,
        state: SearchConversationState | None = None,
    ):
        self.registry = registry
        self.retries = max(1, int(retries))
        self.provider_timeout = max(0.001, float(provider_timeout))
        self.max_results = max(1, int(max_results))
        self.max_fetch_pages = max(0, int(max_fetch_pages))
        self.max_parallel_providers = max(1, int(max_parallel_providers))
        self.cache = cache or TTLCache(1800)
        self.extractor = extractor
        self.context_builder = context_builder or WebContextBuilder()
        self.intent_detector = intent_detector or SearchIntentDetector()
        self.query_rewriter = query_rewriter or QueryRewriter()
        self.ranker = ranker or ResultRanker()
        self.truth_guard = truth_guard or TruthfulnessGuard()
        self.state = state or SearchConversationState()

    async def run(
        self,
        text: str,
        *,
        reply_text: str = '',
        recent_messages: list[dict] | None = None,
        trace_id: str = '-',
        chat_id: int | None = None,
        sender_id: int | None = None,
        message_id: int = 0,
        thread_id: int | None = None,
        reply_to_message_id: int | None = None,
        search_session_id: str = '',
        force_search: bool = False,
        deep: bool = False,
    ) -> SearchOutcome:
        intent = self.intent_detector.detect(text)
        if force_search and text.strip():
            intent = SearchIntent(True, SearchKind.WEB, True, 'explicit_web_search')
        logger.info('WEB_INTENT trace_id=%s needed=%s kind=%s supported=%s category=%s', trace_id, intent.needed, intent.kind.value, intent.supported, intent.category)
        domain_followup = self.query_rewriter.is_domain_only_followup(text)
        state_entry = None
        if domain_followup and chat_id is not None and sender_id is not None:
            state_entry = self.state.lookup(
                chat_id, sender_id, thread_id=thread_id,
                reply_to_message_id=reply_to_message_id,
            )
            logger.info('WEB_FOLLOWUP_STATE_%s trace_id=%s chat_id=%s sender_id=%s', 'HIT' if state_entry else 'MISS', trace_id, chat_id, sender_id)
            if state_entry is None:
                logger.info('WEB_CACHE_SCOPE_MISMATCH trace_id=%s reason=missing_followup_state chat_id=%s sender_id=%s', trace_id, chat_id, sender_id)
        plan = self.query_rewriter.rewrite(
            text,
            reply_text=reply_text,
            recent_messages=None if domain_followup else recent_messages,
            kind=intent.kind,
            followup_subject=state_entry.subject if state_entry else '',
        )
        query_hash = hashlib.sha256(plan.query.encode()).hexdigest()[:16]
        logger.info('WEB_QUERY_REWRITE trace_id=%s original_chars=%d query_hash=%s domain=%s language=%s', trace_id, len(text), query_hash, plan.preferred_domain or '-', plan.language)
        outcome = SearchOutcome(intent=intent, plan=plan)
        if not intent.needed:
            return outcome
        if domain_followup and chat_id is not None and sender_id is not None and state_entry is None:
            outcome.clarification_required = True
            outcome.context = 'WEB_STATUS: CLARIFICATION_REQUIRED (missing scoped search subject)'
            logger.info('WEB_FOLLOWUP_CLARIFICATION_REQUIRED trace_id=%s chat_id=%s sender_id=%s domain=%s', trace_id, chat_id, sender_id, plan.preferred_domain or '-')
            return outcome
        if not intent.supported:
            outcome.context = f'WEB_STATUS: UNSUPPORTED_KIND ({intent.kind.value})'
            return outcome
        if not plan.query:
            outcome.clarification_required = True
            outcome.context = 'WEB_STATUS: CLARIFICATION_REQUIRED (empty query)'
            logger.info('WEB_SEARCH_CLARIFICATION_REQUIRED trace_id=%s reason=empty_query', trace_id)
            return outcome
        if chat_id is not None and sender_id is not None:
            subject = re.sub(r'\s+site:\S+', '', plan.query, flags=re.I).strip()
            self.state.record(chat_id, sender_id, text, plan.query, subject, plan.preferred_domain, trace_id, message_id=message_id, thread_id=thread_id, search_session_id=search_session_id)
        provider_groups = self.registry.priority_groups(plan.kind)
        provider_set = ','.join(sorted(p.name for group in provider_groups for p in group)) or 'none'
        freshness = 'live' if intent.category in NUMERIC_FALLBACK_CATEGORIES else 'default'
        scope = f'{chat_id}:{sender_id}:{thread_id}:{reply_to_message_id}:{search_session_id}' if domain_followup or search_session_id else 'public'
        normalized_query = re.sub(r'\s+', ' ', plan.query.strip().casefold())
        cache_key = '|'.join((normalized_query, intent.kind.value, intent.category, plan.language, provider_set, freshness, plan.preferred_domain or '-', scope, 'deep' if deep else 'normal'))
        logger.info('WEB_CACHE_KEY_BUILT trace_id=%s query_hash=%s intent=%s category=%s provider_set=%s scope_hash=%s', trace_id, hashlib.sha256(normalized_query.encode()).hexdigest()[:16], intent.kind.value, intent.category, provider_set, hashlib.sha256(scope.encode()).hexdigest()[:12])
        logger.info('WEB_REQUEST_CONTEXT_CREATED trace_id=%s query_hash=%s intent=%s category=%s cache_hit=false fallback_eligible=%s previous_state_used=%s search_session_id=%s', trace_id, hashlib.sha256(normalized_query.encode()).hexdigest()[:16], intent.kind.value, intent.category, intent.category in {'current_price_or_market_query','market_rate','exchange_rate','numeric_live_value'}, bool(state_entry), search_session_id or '-')

        cached = self.cache.get(cache_key)
        if cached is not None:
            cached.cache_hit = True
            logger.info('WEB_REQUEST_CONTEXT_CREATED trace_id=%s query_hash=%s intent=%s category=%s cache_hit=true fallback_eligible=%s previous_state_used=%s search_session_id=%s', trace_id, hashlib.sha256(normalized_query.encode()).hexdigest()[:16], intent.kind.value, intent.category, intent.category in NUMERIC_FALLBACK_CATEGORIES, bool(state_entry), search_session_id or '-')
            logger.info('WEB_CACHE_HIT trace_id=%s query_hash=%s result_count=%d', trace_id, query_hash, len(cached.results))
            return cached
        logger.info('WEB_CACHE_MISS trace_id=%s query_hash=%s', trace_id, query_hash)

        successes = 0
        collected: list[SearchResult] = []
        groups = provider_groups
        provider_slots = asyncio.Semaphore(max(self.max_parallel_providers, 6) if deep else self.max_parallel_providers)
        search_plans = self.query_rewriter.expand_deep(plan) if deep else (plan,)
        for group in ([tuple(provider for providers in groups for provider in providers)] if deep else groups):
            logger.info('WEB_PROVIDER_SELECTED trace_id=%s priority=%s providers=%s', trace_id, group[0].priority, ','.join(p.name for p in group))

            async def limited_call(provider, request_plan):
                async with provider_slots:
                    return await self._call_provider(provider, request_plan, trace_id)

            batch = await asyncio.gather(*(limited_call(provider, request_plan) for provider in group for request_plan in search_plans))
            for provider_results, failure, succeeded in batch:
                successes += int(succeeded)
                if failure:
                    outcome.failures.append(failure)
                collected.extend(provider_results)
            if collected and not deep:
                break

        if collected:
            logger.info('WEB_RESULTS_FOUND trace_id=%s count=%d', trace_id, len(collected))
        else:
            outcome.all_providers_failed = successes == 0
            outcome.no_results = successes > 0
            marker = 'PROVIDERS_FAILED' if outcome.all_providers_failed else 'NO_RESULTS'
            outcome.context = f'WEB_STATUS: {marker} query={plan.query}'
            logger.info('WEB_RESULTS_EMPTY trace_id=%s all_providers_failed=%s', trace_id, outcome.all_providers_failed)
            logger.info('WEB_RESULTS_DEDUPED trace_id=%s before=0 after=0', trace_id)
            logger.info('WEB_RESULTS_RANKED trace_id=%s count=0 top_score=0.0000', trace_id)
            logger.info('WEB_CONTEXT_BUILT trace_id=%s chars=%d result_count=0 extract_count=0 extract_tokens=0 numeric_entities_found=0 top_source=-', trace_id, len(outcome.context))
            logger.info('WEB_INTERNAL_STATUS_SUPPRESSED trace_id=%s status=%s', trace_id, marker)
            return outcome

        valid = self.truth_guard.filter_results(collected)
        unique = deduplicate_results(valid)
        logger.info('WEB_RESULTS_DEDUPED trace_id=%s before=%d after=%d', trace_id, len(valid), len(unique))
        ranked_all = self.ranker.rank(plan, unique)
        if deep:
            ranked = []
            seen_domains: set[str] = set()
            for result in ranked_all:
                domain = urlsplit(result.url).netloc.lower().removeprefix('www.')
                if not self.ranker.is_relevant(plan, result) or domain in seen_domains:
                    continue
                seen_domains.add(domain)
                ranked.append(result)
                if len(ranked) >= 30:
                    break
        else:
            ranked = ranked_all[:self.max_results]
        if not ranked:
            outcome.no_results = True
            outcome.context = 'WEB_STATUS: NO_RESULTS (relevance gate rejected collected results)'
            logger.info('WEB_RESULTS_EMPTY trace_id=%s reason=relevance_gate', trace_id)
            return outcome
        logger.info('WEB_RESULTS_RANKED trace_id=%s count=%d top_score=%.4f', trace_id, len(ranked), ranked[0].score if ranked else 0.0)
        if self.extractor is not None and ranked:
            ranked = await self.extractor.extract_many(ranked, plan.query, 15 if deep else self.max_fetch_pages)
        outcome.results = ranked
        searched_at = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        context_builder = WebContextBuilder(20000) if deep else self.context_builder
        outcome.context = context_builder.build(plan, ranked, searched_at)
        logger.info('WEB_CONTEXT_BUILT trace_id=%s chars=%d result_count=%d extract_count=%d extract_tokens=%d numeric_entities_found=%d top_source=%s', trace_id, len(outcome.context), len(ranked), sum(bool(r.relevant_extract.strip()) for r in ranked), sum(len((r.relevant_extract or '').split()) for r in ranked), sum(len(numeric_entities(r.relevant_extract or '')) for r in ranked), (ranked[0].publisher or ranked[0].title) if ranked else '')
        if len(outcome.context) >= context_builder.max_chars:
            logger.info('WEB_CONTEXT_TRIMMED trace_id=%s max_chars=%d', trace_id, context_builder.max_chars)
        self.cache.set(cache_key, outcome)
        return outcome

    async def _call_provider(self, provider, plan, trace_id: str):
        last_failure = None
        for attempt in range(1, self.retries + 1):
            try:
                results = await asyncio.wait_for(provider.search(plan), timeout=self.provider_timeout)
                for result in results:
                    result.provider = result.provider or provider.name
                logger.info('WEB_PROVIDER_SUCCESS trace_id=%s provider=%s attempt=%d result_count=%d', trace_id, provider.name, attempt, len(results))
                return results, None, True
            except asyncio.TimeoutError:
                last_failure = ProviderFailure(provider.name, 'timeout', True, attempt)
                logger.warning('WEB_PROVIDER_TIMEOUT trace_id=%s provider=%s attempt=%d timeout=%.3f', trace_id, provider.name, attempt, self.provider_timeout)
            except Exception as exc:
                last_failure = ProviderFailure(provider.name, type(exc).__name__, False, attempt)
                logger.warning('WEB_PROVIDER_FAILED trace_id=%s provider=%s attempt=%d exception_type=%s', trace_id, provider.name, attempt, type(exc).__name__)
        return [], last_failure, False

    def invalidate_cache(self) -> None:
        self.cache.invalidate()
