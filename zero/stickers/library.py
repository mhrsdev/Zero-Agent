from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Optional

from telethon import functions
from telethon.tl import types

from zero.config import ZeroConfig
from zero.storage import ZeroStore
from zero.vision import VisionProcessor
from zero.stickers.models import Sticker, StickerSet, StickerCandidate, StickerStats

logger = logging.getLogger('zero.stickers.library')


class StickerLibrary:
    """Manages sticker search, ranking, and retrieval for the sticker library."""

    def __init__(
        self,
        config: ZeroConfig,
        store,
        config_stickers,
    ):
        self.config = config
        self.store = store
        self.stickers_config = config_stickers

    async def search(
        self,
        query: str = '',
        mood: str = '',
        limit: int = 20,
        min_quality: float = 0.0,
        saved_only: bool = False,
        exclude_nsfw: bool = True,
    ) -> list:
        """Search stickers by query and mood."""
        stickers = await self.store.get_stickers(
            min_quality=0.0,
            mood_filter='' if not mood else mood,
            saved_only=saved_only,
            limit=100,
            min_usage=0
        )

        # Filter by mood
        if mood:
            filtered = []
            for s in stickers:
                mood_tags = s.mood_tags.split(',') if s.mood_tags else []
                if mood in mood_tags:
                    filtered.append(s)
            stickers = filtered

        # Filter by NSFW
        if exclude_nsfw:
            stickers = [s for s in stickers if s.nsfw_score < 0.5]

        # Filter by quality
        stickers = [s for s in stickers if s.quality_score >= min_quality]

        # Sort by relevance
        stickers.sort(
            key=lambda s: (
                s.saved_to_account,
                s.quality_score * (1 + s.usage_count * 0.1),
                -s.nsfw_score
            ),
            reverse=True
        )

        return stickers[:limit]

    async def get_random_sticker(
        self,
        mood: str = '',
        min_quality: float = 0.3,
        chat_id: int | None = None,
    ) -> Sticker | None:
        """Get a random sticker suitable for the given mood."""
        candidates = await self.search(
            mood=mood,
            limit=20,
            min_quality=min_quality,
        )
        if not candidates:
            return None

        recent_ids = set(await self.store.get_recent_sticker_doc_ids(chat_id, int(getattr(self.stickers_config, 'repeat_window', 20)))) if chat_id else set()
        recent_sets = {s.stickerset_id for s in candidates if s.doc_id in recent_ids and s.stickerset_id}
        recent_emojis = {s.emoji for s in candidates if s.doc_id in recent_ids and s.emoji}
        scored = []
        for s in candidates:
            score = (1.0 if s.saved_to_account else 0.0) + s.quality_score + (s.reaction_score * 0.05)
            score += 1.0 / (1.0 + s.usage_count)
            if s.doc_id in recent_ids:
                score -= 4.0
                logger.info('STICKER_DIVERSITY_PENALTY doc_id=%s reason=recent_send', s.doc_id)
            if s.stickerset_id and s.stickerset_id in recent_sets:
                score -= 0.75
            if s.emoji and s.emoji in recent_emojis:
                score -= 0.5
            scored.append((score, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        logger.info('STICKER_SELECTION_CANDIDATES mood=%s chat_id=%s count=%s', mood, chat_id, len(scored))
        chosen = random.choice(scored[:min(5, len(scored))])[1] if scored else None
        if chosen:
            logger.info('STICKER_DIVERSITY_SCORE mood=%s chat_id=%s doc_id=%s score=%.3f recent_penalty=%s', mood, chat_id, chosen.doc_id, next(score for score, sticker in scored if sticker.doc_id == chosen.doc_id), chosen.doc_id in recent_ids)
            logger.info('STICKER_SELECTED mood=%s chat_id=%s doc_id=%s', mood, chat_id, chosen.doc_id)
        return chosen

    async def get_random_saved_sticker(self) -> Optional:
        """Get a random saved sticker."""
        stickers = await self.store.get_saved_stickers(limit=50)
        if not stickers:
            return None

        import random
        return random.choice(stickers)

    async def get_stats(self) -> dict:
        """Get library statistics."""
        return await self.store.get_sticker_stats()

    async def cleanup(
        self,
        nsfw_threshold: float = 0.5,
        spam_threshold: float = 0.5,
        min_quality: float = 0.3,
    ) -> dict:
        """Clean up low-quality/NSFW/spam stickers."""
        async with self.store._lock:
            with self.store._conn() as conn:
                # Count before
                total_before = conn.execute('SELECT COUNT(*) FROM stickers').fetchone()[0]

                # Delete NSFW
                deleted_nsfw = conn.execute(
                    'DELETE FROM stickers WHERE nsfw_score > ?', (nsfw_threshold,)
                ).rowcount

                # Delete spam
                deleted_spam = conn.execute(
                    'DELETE FROM stickers WHERE spam_score > ?', (spam_threshold,)
                ).rowcount

                # Delete low quality with low usage
                deleted_low_quality = conn.execute(
                    'DELETE FROM stickers WHERE quality_score < ? AND usage_count < 2',
                    (min_quality,)
                ).rowcount

                conn.commit()

                total_after = conn.execute('SELECT COUNT(*) FROM stickers').fetchone()[0]

                return {
                    'deleted_nsfw': deleted_nsfw,
                    'deleted_spam': deleted_spam,
                    'deleted_low_quality': deleted_low_quality,
                    'total_before': total_before,
                    'total_after': total_after,
                    'deleted_total': deleted_nsfw + deleted_spam + deleted_low_quality,
                }