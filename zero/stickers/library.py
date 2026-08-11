from __future__ import annotations

import logging
import random
from typing import Optional

from zero.config import ZeroConfig
from zero.stickers.decision import canonicalize_mood_tags, normalize_mood

logger = logging.getLogger("zero.stickers.library")


class StickerLibrary:
    """Safe, mood-aware sticker retrieval with deterministic RNG injection."""

    def __init__(self, config: ZeroConfig, store, config_stickers, *, rng=None):
        self.config = config
        self.store = store
        self.stickers_config = config_stickers
        self._rng = rng or random

    async def search(
        self,
        query: str = "",
        mood: str = "",
        limit: int = 20,
        min_quality: float = 0.0,
        saved_only: bool = False,
        exclude_nsfw: bool = True,
        media_kind: str = "sticker",
    ) -> list:
        """Return safe candidates; mood aliases are normalized before matching."""
        requested_mood = normalize_mood(mood)
        if media_kind not in {"sticker", "gif"}:
            raise ValueError(f"unsupported media_kind: {media_kind}")
        stickers = await self.store.get_stickers(
            min_quality=0.0,
            mood_filter="",
            saved_only=saved_only,
            limit=max(100, limit * 5),
            min_usage=0,
        )

        safe: list = []
        query_text = (query or "").casefold().strip()
        query_mood = normalize_mood(query_text)
        for sticker in stickers:
            is_gif = (
                bool(getattr(sticker, "is_video", False))
                and not getattr(sticker, "stickerset_id", None)
                and not getattr(sticker, "stickerset_short_name", None)
                and str(getattr(sticker, "mime_type", "") or "").casefold()
                in {"video/mp4", "image/gif"}
            )
            if (media_kind == "gif") != is_gif:
                continue
            tags = canonicalize_mood_tags(sticker.mood_tags)
            if requested_mood and requested_mood not in tags:
                continue
            if query_mood and query_mood not in tags:
                continue
            if query_text and not query_mood:
                haystack = " ".join(
                    str(value or "") for value in (
                        sticker.emoji,
                        sticker.mood_tags,
                        sticker.vision_tags,
                        sticker.vision_summary,
                    )
                ).casefold()
                if query_text not in haystack:
                    continue
            if exclude_nsfw and float(sticker.nsfw_score or 0.0) >= 0.5:
                continue
            if float(sticker.spam_score or 0.0) >= 0.5:
                continue
            if float(sticker.quality_score or 0.0) < min_quality:
                continue
            if not bool(getattr(sticker, "is_available", True)):
                continue
            if int(getattr(sticker, "failure_count", 0) or 0) >= 3:
                continue
            safe.append(sticker)

        safe.sort(
            key=lambda sticker: (
                bool(sticker.saved_to_account),
                float(sticker.quality_score or 0.0) * (1 + sticker.usage_count * 0.1),
                float(sticker.reaction_score or 0.0),
                -float(sticker.nsfw_score or 0.0),
                -int(sticker.doc_id),
            ),
            reverse=True,
        )
        return safe[:limit]

    async def get_random_sticker(
        self,
        mood: str = "",
        min_quality: float = 0.3,
        chat_id: int | None = None,
        *,
        exclude_doc_ids: set[int] | None = None,
        media_kind: str = "sticker",
    ):
        """Choose among relevant safe candidates, penalizing recent repetition."""
        canonical_mood = normalize_mood(mood, default="") or ""
        candidates = await self.search(
            mood=canonical_mood,
            limit=20,
            min_quality=min_quality,
            media_kind=media_kind,
        )
        excluded = set(exclude_doc_ids or ())
        if excluded:
            candidates = [candidate for candidate in candidates if candidate.doc_id not in excluded]
        if not candidates:
            return None

        recent_ids = set(
            await (
                self.store.get_recent_gif_doc_ids
                if media_kind == "gif"
                else self.store.get_recent_sticker_doc_ids
            )(
                chat_id,
                int(getattr(self.stickers_config, "repeat_window", 20)),
            )
        ) if chat_id else set()
        recent_sets = {
            sticker.stickerset_id for sticker in candidates
            if sticker.doc_id in recent_ids and sticker.stickerset_id
        }
        recent_emojis = {
            sticker.emoji for sticker in candidates
            if sticker.doc_id in recent_ids and sticker.emoji
        }
        scored: list[tuple[float, object]] = []
        for sticker in candidates:
            score = (
                (1.0 if sticker.saved_to_account else 0.0)
                + float(sticker.quality_score or 0.0)
                + float(sticker.reaction_score or 0.0) * 0.05
                + 1.0 / (1.0 + sticker.usage_count)
            )
            if sticker.doc_id in recent_ids:
                score -= 4.0
            if sticker.stickerset_id and sticker.stickerset_id in recent_sets:
                score -= 0.75
            if sticker.emoji and sticker.emoji in recent_emojis:
                score -= 0.5
            scored.append((score, sticker))
        scored.sort(key=lambda item: (item[0], -item[1].doc_id), reverse=True)
        pool = scored[: min(5, len(scored))]
        chosen = self._rng.choice(pool)[1]
        logger.info(
            "%s_SELECTED mood=%s chat_id=%s doc_id=%s score=%.3f candidate_count=%s",
            media_kind.upper(), canonical_mood or "generic", chat_id, chosen.doc_id,
            next(score for score, sticker in scored if sticker.doc_id == chosen.doc_id),
            len(scored),
        )
        return chosen

    async def get_random_gif(
        self,
        mood: str = "",
        min_quality: float = 0.3,
        chat_id: int | None = None,
        *,
        exclude_doc_ids: set[int] | None = None,
    ):
        return await self.get_random_sticker(
            mood=mood,
            min_quality=min_quality,
            chat_id=chat_id,
            exclude_doc_ids=exclude_doc_ids,
            media_kind="gif",
        )

    async def get_random_saved_sticker(self) -> Optional:
        stickers = await self.store.get_saved_stickers(limit=50)
        return self._rng.choice(stickers) if stickers else None

    async def get_stats(self) -> dict:
        return await self.store.get_sticker_stats()

    async def cleanup(
        self,
        nsfw_threshold: float = 0.5,
        spam_threshold: float = 0.5,
        min_quality: float = 0.3,
    ) -> dict:
        async with self.store._lock:
            with self.store._conn() as conn:
                total_before = conn.execute("SELECT COUNT(*) FROM stickers").fetchone()[0]
                deleted_nsfw = conn.execute(
                    "DELETE FROM stickers WHERE nsfw_score > ?", (nsfw_threshold,)
                ).rowcount
                deleted_spam = conn.execute(
                    "DELETE FROM stickers WHERE spam_score > ?", (spam_threshold,)
                ).rowcount
                deleted_low_quality = conn.execute(
                    "DELETE FROM stickers WHERE quality_score < ? AND usage_count < 2",
                    (min_quality,),
                ).rowcount
                conn.commit()
                total_after = conn.execute("SELECT COUNT(*) FROM stickers").fetchone()[0]
        return {
            "deleted_nsfw": deleted_nsfw,
            "deleted_spam": deleted_spam,
            "deleted_low_quality": deleted_low_quality,
            "total_before": total_before,
            "total_after": total_after,
            "deleted_total": deleted_nsfw + deleted_spam + deleted_low_quality,
        }
