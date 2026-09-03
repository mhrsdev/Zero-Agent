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
from .extraction import _safe_public_url_async, has_usable_evidence
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
        extract_timeout: float | None = None,
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
        # Page extraction had no overall bound, only a per-socket timeout on each
        # fetch, so a set of slow hosts could outlast the caller's entire search
        # budget. Defaults to one provider timeout; deep search gets three times
        # that because it extracts up to 15 pages instead of two.
        self.extract_timeout = max(0.001, float(extract_timeout if extract_timeout is not None else provider_timeout))
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

    def targets_url(self, text: str, *, reply_text: str = '', recent_messages: list[dict] | None = None) -> bool:
        plan = self.query_rewriter.rewrite(text, reply_text=reply_text, recent_messages=recent_messages)
        return _is_http_url(plan.query)

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
        try:
            intent = self.intent_detector.detect(text, reply_text=reply_text)
        except TypeError:
            # Compatibility for narrowly scoped test/custom detectors predating reply URLs.
            intent = self.intent_detector.detect(text)
        if force_search and (text.strip() or reply_text.strip()):
            # Forcing a search means "search anyway", not "forget what this is
            # about". Overwriting the category with 'explicit_web_search' cost a
            # price query its numeric fallback, its live-market disclosure, and
            # the `live` freshness discriminator in the cache key — so a /search
            # price query was cached for the full TTL like any other question.
            # A category the detector could not determine still becomes explicit.
            category = intent.category if intent.category and intent.category not in {'none', ''} else 'explicit_web_search'
            intent = SearchIntent(True, SearchKind.WEB, True, category)
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
        if not intent.supported:
            outcome.context = f'WEB_STATUS: UNSUPPORTED_KIND ({intent.kind.value})'
            return outcome
        if not plan.query:
            outcome.clarification_required = True
            outcome.context = 'WEB_STATUS: CLARIFICATION_REQUIRED (empty query)'
            logger.info('WEB_SEARCH_CLARIFICATION_REQUIRED trace_id=%s reason=empty_query', trace_id)
            return outcome
        if _is_http_url(plan.query):
            return await self._inspect_url(
                SearchIntent(True, SearchKind.WEB, True, 'url_inspection'),
                plan,
                trace_id,
            )
        if domain_followup and chat_id is not None and sender_id is not None and state_entry is None:
            outcome.clarification_required = True
            outcome.context = 'WEB_STATUS: CLARIFICATION_REQUIRED (missing scoped search subject)'
            logger.info('WEB_FOLLOWUP_CLARIFICATION_REQUIRED trace_id=%s chat_id=%s sender_id=%s domain=%s', trace_id, chat_id, sender_id, plan.preferred_domain or '-')
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
        provider_slots = asyncio.Semaphore(max(self.max_parallel_providers, 6) if deep else self.max_parallel_providers)
        search_plans = self.query_rewriter.expand_deep(plan) if deep else (plan,)
        # Deep search asks every provider at once instead of walking the priority
        # tiers, so the groups collapse into one. Empty groups are dropped rather
        # than flattened blindly: with no provider registered — which is the
        # shipped Google-Grounding-only configuration — the old flatten produced
        # `[()]`, one empty group, and the next line read `group[0].priority`.
        # SearchOrchestrator runs the local pipeline first for deep, so that
        # IndexError escaped before the working primary was ever tried.
        rounds = [tuple(p for providers in provider_groups for p in providers)] if deep else provider_groups
        for group in (g for g in rounds if g):
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
            # Stop only once something SURVIVES validation. The old check tested
            # the raw provider output, so a single unusable row in the top tier —
            # an empty title, a magnet: URL, an intranet host — ended the walk and
            # the healthy priority-20 and priority-30 providers were never asked.
            if not deep and self.truth_guard.filter_results(collected):
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
            # Only the deep path runs a relevance gate; on the normal path an
            # empty `ranked` means validation dropped everything, and saying
            # "relevance gate" sent whoever read the log looking at the wrong
            # code.
            reason = 'relevance_gate' if deep else 'validation_rejected_all_results'
            outcome.context = f'WEB_STATUS: NO_RESULTS ({reason})'
            logger.info('WEB_RESULTS_EMPTY trace_id=%s reason=%s collected=%d valid=%d unique=%d', trace_id, reason, len(collected), len(valid), len(unique))
            return outcome
        logger.info('WEB_RESULTS_RANKED trace_id=%s count=%d top_score=%.4f', trace_id, len(ranked), ranked[0].score if ranked else 0.0)
        if self.extractor is not None and ranked:
            # extract_many gathers one fetch per result and had no overall bound —
            # only per-socket timeouts — so a set of slow hosts could outlast the
            # caller's whole search budget. On timeout the results are kept and
            # rendered from their snippets, which is what the extractor's own
            # per-result failure path already does.
            budget = self.extract_timeout * (3 if deep else 1)
            try:
                ranked = await asyncio.wait_for(
                    self.extractor.extract_many(ranked, plan.query, 15 if deep else self.max_fetch_pages),
                    timeout=budget,
                )
            except asyncio.TimeoutError:
                logger.warning('WEB_EXTRACTION_TIMEOUT trace_id=%s budget_seconds=%.1f result_count=%d', trace_id, budget, len(ranked))
        outcome.results = ranked
        searched_at = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        context_builder = WebContextBuilder(20000) if deep else self.context_builder
        outcome.context = context_builder.build(plan, ranked, searched_at)
        logger.info('WEB_CONTEXT_BUILT trace_id=%s chars=%d result_count=%d extract_count=%d extract_tokens=%d numeric_entities_found=%d top_source=%s', trace_id, len(outcome.context), len(ranked), sum(bool(r.relevant_extract.strip()) for r in ranked), sum(len((r.relevant_extract or '').split()) for r in ranked), sum(len(numeric_entities(r.relevant_extract or '')) for r in ranked), (ranked[0].publisher or ranked[0].title) if ranked else '')
        if len(outcome.context) >= context_builder.max_chars:
            logger.info('WEB_CONTEXT_TRIMMED trace_id=%s max_chars=%d', trace_id, context_builder.max_chars)
        self.cache.set(cache_key, outcome)
        return outcome

    async def _inspect_url(self, intent: SearchIntent, plan, trace_id: str) -> SearchOutcome:
        outcome = SearchOutcome(intent=intent, plan=plan)
        # Awaited, not called inline: this resolves DNS, which is synchronous and
        # takes no timeout, so on the event loop an unreachable resolver stalled
        # every other coroutine for the OS resolver timeout.
        if not await _safe_public_url_async(plan.query):
            outcome.all_providers_failed = True
            outcome.context = 'WEB_STATUS: URL_REJECTED'
            logger.info('WEB_URL_REJECTED trace_id=%s', trace_id)
            return outcome

        readable = False
        if self.extractor is not None:
            try:
                direct = await self.extractor.extract_url(plan.query, plan.original)
                readable = True
                if self._usable_url_result(direct):
                    return self._url_outcome(intent, plan, direct, trace_id)
            except Exception as exc:
                outcome.failures.append(ProviderFailure('direct-url', type(exc).__name__))
                logger.info('WEB_URL_DIRECT_FETCH_FAILED trace_id=%s exception_type=%s', trace_id, type(exc).__name__)

        for provider in self.registry.fetch_providers(intent.kind):
            result, failure, succeeded = await self._call_fetch_provider(provider, plan, trace_id)
            readable = readable or succeeded
            if failure:
                outcome.failures.append(failure)
            if result is not None and self._usable_url_result(result):
                return self._url_outcome(intent, plan, result, trace_id)

        outcome.all_providers_failed = not readable
        outcome.no_results = readable
        outcome.context = 'WEB_STATUS: URL_UNREADABLE'
        logger.info(
            'WEB_URL_UNREADABLE trace_id=%s all_providers_failed=%s fetch_provider_count=%d',
            trace_id,
            outcome.all_providers_failed,
            len(self.registry.fetch_providers(intent.kind)),
        )
        return outcome

    def _url_outcome(self, intent: SearchIntent, plan, result: SearchResult, trace_id: str) -> SearchOutcome:
        outcome = SearchOutcome(intent=intent, plan=plan, results=[result])
        outcome.context = self.context_builder.build(plan, outcome.results)
        logger.info(
            'WEB_URL_INSPECTION_OK trace_id=%s provider=%s extract_chars=%d',
            trace_id,
            result.provider,
            len(result.relevant_extract or result.snippet),
        )
        return outcome

    def _usable_url_result(self, result: SearchResult) -> bool:
        return bool(self.truth_guard.filter_results([result])) and has_usable_evidence(
            result.relevant_extract or result.snippet
        )

    async def _call_fetch_provider(self, provider, plan, trace_id: str):
        last_failure = None
        for attempt in range(1, self.retries + 1):
            try:
                result = await asyncio.wait_for(
                    provider.fetch_url(
                        plan.query,
                        query=plan.original,
                        max_chars=self.context_builder.max_chars,
                    ),
                    timeout=self.provider_timeout,
                )
                if result is not None:
                    result.provider = result.provider or f'{provider.name}-fetch'
                logger.info(
                    'WEB_FETCH_PROVIDER_SUCCESS trace_id=%s provider=%s attempt=%d usable=%s',
                    trace_id,
                    provider.name,
                    attempt,
                    bool(result and self._usable_url_result(result)),
                )
                return result, None, True
            except asyncio.TimeoutError:
                last_failure = ProviderFailure(provider.name, 'timeout', True, attempt)
                logger.warning('WEB_FETCH_PROVIDER_TIMEOUT trace_id=%s provider=%s attempt=%d', trace_id, provider.name, attempt)
            except Exception as exc:
                last_failure = ProviderFailure(provider.name, type(exc).__name__, False, attempt)
                logger.warning('WEB_FETCH_PROVIDER_FAILED trace_id=%s provider=%s attempt=%d exception_type=%s', trace_id, provider.name, attempt, type(exc).__name__)
        return None, last_failure, False

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
                reason = _failure_reason(exc)
                last_failure = ProviderFailure(provider.name, reason, False, attempt)
                # The reason, not just the class name: every transport failure
                # used to be a bare RuntimeError, so a 403 from a SearXNG
                # instance with JSON disabled, a 400 from a bad payload, a 502
                # and a blocked private destination were one indistinguishable
                # log line.
                logger.warning('WEB_PROVIDER_FAILED trace_id=%s provider=%s attempt=%d exception_type=%s reason=%s', trace_id, provider.name, attempt, type(exc).__name__, reason)
        return [], last_failure, False

    def invalidate_cache(self) -> None:
        self.cache.invalidate()


def _failure_reason(exc: BaseException) -> str:
    """A short, secret-free label for why a provider call failed.

    Carries the HTTP status when there is one. Never includes the URL: a private
    endpoint's address is not something to write into a log that may be shipped.
    """
    status = getattr(exc, 'status', None)
    if status is not None:
        return f'{type(exc).__name__}:{status}'
    return type(exc).__name__


def _is_http_url(value: str) -> bool:
    parts = urlsplit((value or '').strip())
    return parts.scheme in {'http', 'https'} and bool(parts.hostname)
