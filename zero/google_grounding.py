from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from .router import IndependentRouter
from .web_search.context import WebContextBuilder
from .web_search.models import QueryPlan, SearchIntent, SearchKind, SearchOutcome, SearchResult
from .web_search.query import QueryRewriter

logger = logging.getLogger("zero.google_grounding")


class GoogleGroundingSearch:
    """Google Search grounding through Zero's existing Gemini API key pool."""

    def __init__(self, config, router: IndependentRouter, store=None):
        self.config, self.router, self.store = config, router, store
        self.enabled = bool(getattr(config.web, "google_grounding_enabled", True))

    async def run(self, text: str, *, trace_id: str = "-", **scope) -> SearchOutcome:
        plan = QueryRewriter().rewrite(
            text,
            reply_text=str(scope.get('reply_text') or ''),
            recent_messages=scope.get('recent_messages'),
        )
        intent = SearchIntent(bool(plan.query), SearchKind.WEB, True, "explicit_web_search" if plan.query else "none")
        outcome = SearchOutcome(intent=intent, plan=plan)
        if not intent.needed:
            return outcome
        if not self.enabled:
            outcome.all_providers_failed = True
            outcome.context = "WEB_STATUS: GOOGLE_GROUNDING_DISABLED"
            logger.info("GOOGLE_GROUNDING_UNAVAILABLE trace_id=%s reason=disabled", trace_id)
            return outcome
        logger.info("GOOGLE_GROUNDING_START trace_id=%s query_hash=%s", trace_id, _hash(plan.query))
        result = await self.router.complete_search(plan.query, max_output_tokens=900)
        if not result.text:
            outcome.all_providers_failed = True
            outcome.context = "WEB_STATUS: GOOGLE_GROUNDING_FAILED"
            logger.warning("GOOGLE_GROUNDING_FAILED trace_id=%s reason=%s", trace_id, result.metadata.get("error", "unavailable"))
            return outcome
        raw = result.metadata.get("raw", {})
        chunks = (raw.get("candidates") or [{}])[0].get("groundingMetadata", {}).get("groundingChunks", [])
        for chunk in chunks:
            web = chunk.get("web") or {}
            url, title = str(web.get("uri") or ""), str(web.get("title") or "").strip()
            parsed = urlparse(url)
            if title and parsed.scheme in {"http", "https"} and parsed.netloc:
                outcome.results.append(SearchResult(
                    title=title, url=url, publisher=parsed.netloc.lower(),
                    provider="google-grounding", kind=SearchKind.WEB, score=1.0,
                    metadata={"grounding": True},
                ))
        if not outcome.results:
            outcome.all_providers_failed = True
            outcome.context = "WEB_STATUS: GOOGLE_GROUNDING_SOURCES_MISSING"
            logger.warning("GOOGLE_GROUNDING_REJECTED trace_id=%s reason=missing_sources", trace_id)
            return outcome
        outcome.results[0].snippet = result.text[:1200]
        outcome.results[0].relevant_extract = result.text[:1200]
        searched_at = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        outcome.context = WebContextBuilder(self.config.web.context_max_chars).build(plan, outcome.results, searched_at)
        logger.info("GOOGLE_GROUNDING_OK trace_id=%s source_count=%d model=%s", trace_id, len(outcome.results), result.model)
        return outcome

    async def close(self) -> None:
        return None

    async def health_check(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, "google grounding disabled"
        return (True, "") if self.router.pools["gemini"].states else (False, "gemini API keys missing")

    def invalidate_cache(self) -> None:
        return None

    def mark_response_sent(self, **kwargs) -> None:
        logger.info("GOOGLE_GROUNDING_RESPONSE_SENT trace_id=%s result_count=%s", kwargs.get("trace_id", "-"), kwargs.get("result_count", 0))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
