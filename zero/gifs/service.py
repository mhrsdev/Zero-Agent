from __future__ import annotations

import logging
import random
import time

from zero.gifs.decision import GifSendOutcome
from zero.gifs.library import GifLibrary
from zero.gifs.sender import GifSender
from zero.stickers.decision import normalize_mood

logger = logging.getLogger("zero.gifs.service")


def _setting_bool(value, default: bool) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().casefold() in {"1", "true", "yes", "on", "enabled"}


class GifService:
    """Independent GIF intent-policy-selection-transport pipeline."""

    def __init__(self, config, store, client=None, *, rng=None):
        self.config = config
        self.store = store
        self.client = client
        self.rng = rng or random.Random()

    async def send(
        self,
        chat_id: int,
        mood: str,
        *,
        direct_request: bool = False,
        retry_request: bool = False,
    ) -> GifSendOutcome:
        canonical_mood = normalize_mood(mood, default="react") or "react"
        cfg = self.config.gifs
        candidate_count = 0
        probability = 1.0
        sample = None
        threshold = float(cfg.min_relevance_score)

        def finish(
            reason: str,
            *,
            candidate_id: int | None = None,
            relevance_score: float = 0.0,
            transport: str = "not_attempted",
        ) -> GifSendOutcome:
            outcome = GifSendOutcome(
                reason=reason,
                mood=canonical_mood,
                direct_request=direct_request,
                candidate_id=candidate_id,
                relevance_score=relevance_score,
                fallback_level="exact" if candidate_id is not None else "none",
                transport=transport,
                candidate_count=candidate_count,
                send_probability=probability,
                random_sample=sample,
                confidence_threshold=threshold,
            )
            logger.info(
                "GIF_DECISION chat_id=%s mood=%s direct=%s retry=%s reason=%s "
                "candidate_id=%s candidates=%s relevance=%.3f threshold=%.3f "
                "probability=%.3f sample=%s transport=%s",
                chat_id, canonical_mood, direct_request, retry_request, reason,
                candidate_id, candidate_count, relevance_score, threshold,
                probability, sample, transport,
            )
            return outcome

        try:
            enabled_setting = await self.store.get_setting("gif_enabled", "")
            if not _setting_bool(enabled_setting, bool(cfg.enabled)):
                return finish("disabled")
            if not direct_request and not bool(cfg.auto_enabled):
                return finish("auto_disabled")
            negative_until = int(await self.store.get_setting(
                f"gif_negative_until:{chat_id}", "0"
            ) or 0)
            if negative_until > int(time.time()):
                return finish("negative_feedback")

            trigger_type = "retry" if retry_request else ("direct" if direct_request else "auto")
            policy = await self.store.get_gif_send_policy(chat_id, trigger_type=trigger_type)
            if direct_request:
                if policy["sent_last_hour"] >= int(cfg.direct_limit_per_hour):
                    return finish("hourly_limit")
                if int(time.time()) - policy["last_sent_at"] < int(cfg.direct_cooldown_seconds):
                    return finish("direct_cooldown")
            else:
                if policy["sent_last_hour"] >= int(cfg.limit_per_hour):
                    return finish("hourly_limit")
                if int(time.time()) - policy["last_sent_at"] < int(cfg.cooldown_seconds):
                    return finish("cooldown")
                if policy["messages_since_last"] < int(cfg.min_messages_between):
                    return finish("min_messages_between")
                probability = max(0.0, min(1.0, float(cfg.send_chance)))
                sample = float(self.rng.random())
                if sample >= probability:
                    return finish("chance_rejected")

            library = GifLibrary(self.config, self.store, cfg, rng=self.rng)
            base_pool = await library.search(
                mood=canonical_mood,
                min_quality=0.45,
                limit=200,
            )
            excluded: set[int] = set()
            if retry_request or not direct_request:
                excluded.update(await self.store.get_recent_gif_doc_ids(
                    chat_id, int(cfg.repeat_window)
                ))
            eligible = [item for item in base_pool if item.doc_id not in excluded]
            candidate_count = len(eligible)
            if not eligible:
                return finish("repeat_window" if base_pool and excluded else "no_relevant_candidate")

            candidate = await library.choose(
                mood=canonical_mood,
                min_quality=0.45,
                chat_id=chat_id,
                exclude_doc_ids=excluded,
            )
            if candidate is None:
                return finish("no_relevant_candidate")
            relevance = min(1.0, 0.70 + 0.30 * float(candidate.quality_score or 0.0))
            if relevance < threshold:
                return finish(
                    "below_relevance_threshold",
                    candidate_id=candidate.doc_id,
                    relevance_score=relevance,
                )
            if not self.client:
                return finish(
                    "no_client",
                    candidate_id=candidate.doc_id,
                    relevance_score=relevance,
                )

            sent = await GifSender(self.config, self.store, self.client).send(
                chat_id, candidate, relevance_score=relevance
            )
            if not sent:
                await self.store.record_sticker_send_failure(candidate.doc_id)
                return finish(
                    "transport_failed",
                    candidate_id=candidate.doc_id,
                    relevance_score=relevance,
                    transport="failed",
                )
            await self.store.record_gif_send(
                candidate.doc_id,
                chat_id,
                trigger_type=trigger_type,
            )
            return finish(
                "sent",
                candidate_id=candidate.doc_id,
                relevance_score=relevance,
                transport="sent",
            )
        except Exception as exc:
            logger.warning(
                "GIF_SEND_EXCEPTION mood=%s error=%s",
                canonical_mood, type(exc).__name__,
            )
            return finish("transport_exception", transport=type(exc).__name__)
