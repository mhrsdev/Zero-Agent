from __future__ import annotations

import logging
from typing import Optional

from zero.config import ZeroConfig
from zero.storage import ZeroStore
from zero.vision import VisionProcessor
from zero.stickers.models import Sticker

logger = logging.getLogger('zero.stickers.classifier')


class StickerClassifier:
    """Classifies stickers for mood, quality, NSFW, and spam."""

    def __init__(
        self,
        config: ZeroConfig,
        store,
        vision: Optional = None,
    ):
        self.config = config
        self.store = store
        self.vision = vision

    # Emoji to mood mapping
    EMOJI_MOODS = {
        '😂': 'funny', '🤣': 'funny', '😭': 'sad', '😢': 'sad',
        '😍': 'love', '❤️': 'love', '😎': 'cool', '😏': 'smirk',
        '😡': 'angry', '🤬': 'angry', '😱': 'shock', '😲': 'surprise',
        '🤔': 'thinking', '💀': 'dead', '👍': 'approve', '👎': 'disapprove',
        '🙏': 'pray', '🎉': 'celebrate', '🔥': 'fire', '💯': 'perfect',
        '✨': 'sparkle', '💩': 'funny', '😂': 'funny', '🤣': 'funny',
        '😭': 'sad', '😢': 'sad', '😍': 'love', '❤️': 'love',
        '😎': 'cool', '😏': 'smirk', '😡': 'angry', '🤬': 'angry',
        '😱': 'shock', '😲': 'surprise', '🤔': 'thinking', '💀': 'dead',
        '👍': 'approve', '👎': 'disapprove', '🙏': 'pray', '🎉': 'celebrate',
        '🔥': 'fire', '💯': 'perfect', '✨': 'sparkle', '💩': 'funny',
    }

    # Vision-based tag keywords
    VISION_TAG_KEYWORDS = {
        'meme': ['meme', 'funny', 'lol', 'laugh', 'humor'],
        'text': ['text', 'writing', 'caption', 'quote', 'word'],
        'character': ['character', 'anime', 'cartoon', 'figure', 'person'],
        'animal': ['cat', 'dog', 'animal', 'pet', 'cute'],
        'reaction': ['reaction', 'react', 'face', 'expression'],
        'code': ['code', 'programming', 'terminal', 'developer', 'bug'],
        'gaming': ['game', 'gaming', 'play', 'controller'],
        'crypto': ['crypto', 'bitcoin', 'eth', 'blockchain', 'token'],
    }

    NSFW_KEYWORDS = [
        'nude', 'naked', 'sex', 'porn', 'explicit', 'adult', 'nsfw',
        'sexual', 'erotic', 'breast', 'genital', 'penis', 'vagina',
    ]

    async def classify_sticker(self, sticker) -> None:
        """Classify sticker mood, quality, and spam score."""
        mood_tags = []
        quality_score = 0.5
        spam_score = 0.0

        # Vision-based classification
        if sticker.vision_tags:
            vision_tag_list = sticker.vision_tags.split(',')
            mood_tags.extend(vision_tag_list)

        # Emoji-based classification
        for emoji, mood in self.EMOJI_MOODS.items():
            if emoji in (sticker.emoji or ''):
                mood_tags.append(mood)

        # Vision tag based mood
        if sticker.vision_tags:
            vision_tags = sticker.vision_tags.split(',')
            for tag in vision_tags:
                if tag in ['meme', 'funny', 'funny']:
                    mood_tags.append('funny')
                elif tag in ['character', 'anime', 'cartoon']:
                    mood_tags.append('character')
                elif tag in ['animal', 'cute']:
                    mood_tags.append('cute')
                elif tag in ['reaction', 'face', 'expression']:
                    mood_tags.append('reaction')

        # Quality based on vision analysis
        if sticker.vision_summary:
            summary_len = len(sticker.vision_summary)
            if summary_len > 100:
                quality_score = min(0.5 + len(sticker.vision_summary) / 500, 1.0)
            else:
                quality_score = 0.5

        # Spam detection - simple heuristics
        if sticker.usage_count > 10 and sticker.quality_score < 0.3:
            spam_score = 0.5

        # NSFW boost
        if sticker.nsfw_score > 0.5:
            quality_score = 0.1
            spam_score = max(spam_score, 0.8)

        # Update in database
        mood_tags_str = ','.join(set(mood_tags)) if mood_tags else ''
        await self.store.update_sticker_classification(
            sticker.doc_id,
            mood_tags_str,
            quality_score,
            spam_score
        )

    def _extract_vision_tags(self, summary: str) -> str:
        """Extract tags from vision summary."""
        tags = []
        summary_lower = summary.lower()

        for tag, keywords in self.VISION_TAG_KEYWORDS.items():
            if any(k in summary_lower for k in keywords):
                tags.append(tag)

        return ','.join(tags) if tags else ''

    def _calculate_nsfw_score(self, summary: str) -> float:
        """Calculate NSFW score from vision summary."""
        summary_lower = summary.lower()
        score = sum(1 for kw in self.NSFW_KEYWORDS if kw in summary_lower)
        return min(score * 0.2, 1.0)

    def extract_vision_tags(self, summary: str) -> str:
        """Public method to extract tags from vision summary."""
        return self._extract_vision_tags(summary)

    def calculate_nsfw_score(self, summary: str) -> float:
        """Public method to calculate NSFW score."""
        return self._calculate_nsfw_score(summary)