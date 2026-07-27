from __future__ import annotations

import asyncio
import logging

from .models import SearchOutcome

logger = logging.getLogger('zero.web')


class SearchOrchestrator:
    """Google Grounding first; bounded local SearXNG tiers only on failure."""

    def __init__(self, primary, local_pipeline):
        self.primary = primary
        self.local_pipeline = local_pipeline

    async def run(self, text: str, *, trace_id: str = '-', **scope) -> SearchOutcome:
        if scope.get('deep'):
            local = await self.local_pipeline.run(text, trace_id=trace_id, **scope)
            if local.results:
                logger.info('WEB_ORCHESTRATOR_SELECTED trace_id=%s provider=multi-source-local deep=true result_count=%d', trace_id, len(local.results))
                return local
            primary = await self.primary.run(text, trace_id=trace_id, **scope)
            logger.info('WEB_ORCHESTRATOR_SELECTED trace_id=%s provider=google-grounding deep_fallback=true result_count=%d', trace_id, len(primary.results))
            return primary if primary.results else local
        last = None
        for attempt in range(1, 7):
            primary = await self.primary.run(text, trace_id=trace_id, **scope)
            if primary.results:
                logger.info('WEB_ORCHESTRATOR_SELECTED trace_id=%s provider=google-grounding fallback=false attempt=%d result_count=%d', trace_id, attempt, len(primary.results))
                return primary

            reason = 'provider_failure' if primary.all_providers_failed else 'no_results'
            logger.info('WEB_LOCAL_FALLBACK_STARTED trace_id=%s reason=%s attempt=%d', trace_id, reason, attempt)
            local = await self.local_pipeline.run(text, trace_id=trace_id, **scope)
            if local.results:
                logger.info('WEB_ORCHESTRATOR_SELECTED trace_id=%s provider=%s fallback=true attempt=%d result_count=%d', trace_id, local.results[0].provider, attempt, len(local.results))
                return local
            last = local
            logger.info('WEB_ORCHESTRATOR_ATTEMPT_EMPTY trace_id=%s attempt=%d remaining=%d', trace_id, attempt, 6 - attempt)
            if attempt < 6:
                self.local_pipeline.invalidate_cache()
                await asyncio.sleep(min(0.25 * attempt, 1.0))

        logger.info('WEB_ORCHESTRATOR_EXHAUSTED trace_id=%s attempts=6 primary_failed=%s local_failed=%s', trace_id, last.all_providers_failed, last.all_providers_failed)
        return last

    def invalidate_cache(self) -> None:
        self.local_pipeline.invalidate_cache()
