from __future__ import annotations

import logging

from .models import SearchOutcome

logger = logging.getLogger('zero.web')


class SearchOrchestrator:
    """Optional grounding primary plus the shared provider registry fallback."""

    def __init__(self, primary, local_pipeline):
        self.primary = primary
        self.local_pipeline = local_pipeline

    async def run(self, text: str, *, trace_id: str = '-', **scope) -> SearchOutcome:
        targets_url = getattr(self.local_pipeline, 'targets_url', None)
        if callable(targets_url) and targets_url(
            text,
            reply_text=str(scope.get('reply_text') or ''),
            recent_messages=scope.get('recent_messages'),
        ):
            local = await self.local_pipeline.run(text, trace_id=trace_id, **scope)
            logger.info('WEB_ORCHESTRATOR_SELECTED trace_id=%s provider=url-inspection result_count=%d', trace_id, len(local.results))
            return local
        # When the configured primary is disabled, do not burn retry budget on it.
        # The local registry is the configured provider path in that deployment.
        if getattr(self.primary, 'enabled', True) is False:
            local = await self.local_pipeline.run(text, trace_id=trace_id, **scope)
            logger.info('WEB_ORCHESTRATOR_SELECTED trace_id=%s provider=local-disabled-primary result_count=%d', trace_id, len(local.results))
            return local
        if scope.get('deep'):
            local = await self.local_pipeline.run(text, trace_id=trace_id, **scope)
            if local.results:
                logger.info('WEB_ORCHESTRATOR_SELECTED trace_id=%s provider=multi-source-local deep=true result_count=%d', trace_id, len(local.results))
                return local
            primary = await self.primary.run(text, trace_id=trace_id, **scope)
            logger.info('WEB_ORCHESTRATOR_SELECTED trace_id=%s provider=google-grounding deep_fallback=true result_count=%d', trace_id, len(primary.results))
            return primary if primary.results else local
        primary = await self.primary.run(text, trace_id=trace_id, **scope)
        if primary.results:
            logger.info('WEB_ORCHESTRATOR_SELECTED trace_id=%s provider=google-grounding fallback=false result_count=%d', trace_id, len(primary.results))
            return primary

        reason = 'provider_failure' if primary.all_providers_failed else 'no_results'
        logger.info('WEB_LOCAL_FALLBACK_STARTED trace_id=%s reason=%s', trace_id, reason)
        local = await self.local_pipeline.run(text, trace_id=trace_id, **scope)
        if local.results:
            logger.info('WEB_ORCHESTRATOR_SELECTED trace_id=%s provider=%s fallback=true result_count=%d', trace_id, local.results[0].provider, len(local.results))
            return local

        logger.info('WEB_ORCHESTRATOR_EXHAUSTED trace_id=%s primary_failed=%s local_failed=%s', trace_id, primary.all_providers_failed, local.all_providers_failed)
        return local

    def invalidate_cache(self) -> None:
        self.local_pipeline.invalidate_cache()
