from __future__ import annotations

import logging
from typing import Optional

from zero.config import ZeroConfig
from zero.stickers.decision import canonicalize_mood_tags

logger = logging.getLogger("zero.stickers.classifier")


class StickerClassifier:
    """Single source of truth for sticker mood, quality and safety labels."""

    EMOJI_MOODS = {
        "😂": "funny", "🤣": "funny", "💩": "funny",
        "😭": "sad", "😢": "sad", "😥": "sad",
        "😍": "love", "❤️": "love", "💕": "love", "💖": "love",
        "😎": "cool", "😏": "smirk",
        "😡": "angry", "🤬": "angry",
        "😱": "shock", "😲": "shock", "😳": "shock",
        "🤔": "thinking", "😐": "react", "🫡": "react", "💀": "dead",
        "👍": "approve", "👎": "disapprove", "👋": "greeting",
        "🙏": "pray", "🎉": "celebrate", "🔥": "fire",
        "💯": "perfect", "✨": "sparkle",
    }

    VISION_TAG_KEYWORDS = {
        "meme": ["meme", "funny", "lol", "laugh", "humor", "میم", "خنده", "خنده‌دار", "خنده دار", "جوک"],
        "text": ["text", "writing", "caption", "quote", "word"],
        "character": ["character", "anime", "cartoon", "figure", "person"],
        "animal": ["cat", "dog", "animal", "pet", "cute"],
        "reaction": ["reaction", "react", "face", "expression", "ری‌اکشن", "ری اکشن", "واکنش"],
        "sad": ["sad", "cry", "crying", "tears", "غمگین", "گریه", "ناراحت"],
        "love": ["love", "heart", "romantic", "عاشقانه", "عشق", "قلب"],
        "angry": ["angry", "rage", "furious", "عصبانی", "خشمگین"],
        "greeting": ["hello", "goodbye", "waving", "greeting"],
        "celebrate": ["party", "celebrate", "celebration", "جشن", "تولد", "مبارک"],
        "code": ["code", "programming", "terminal", "developer", "bug"],
        "gaming": ["game", "gaming", "play", "controller"],
        "crypto": ["crypto", "bitcoin", "eth", "blockchain", "token"],
    }

    NSFW_KEYWORDS = [
        "nude", "naked", "sex", "porn", "explicit", "adult", "nsfw",
        "sexual", "erotic", "breast", "genital", "penis", "vagina",
    ]

    def __init__(self, config: ZeroConfig, store, vision: Optional = None):
        self.config = config
        self.store = store
        self.vision = vision

    async def classify_sticker(self, sticker) -> None:
        tags: list[str] = list(canonicalize_mood_tags(sticker.vision_tags))
        for emoji, mood in self.EMOJI_MOODS.items():
            if emoji in (sticker.emoji or ""):
                tags.append(mood)
        mood_tags = canonicalize_mood_tags(tags)

        quality_score = max(0.55, float(sticker.quality_score or 0.0))
        if sticker.vision_summary and len(sticker.vision_summary) > 100:
            quality_score = max(
                quality_score,
                min(0.55 + len(sticker.vision_summary) / 500, 1.0),
            )
        spam_score = float(sticker.spam_score or 0.0)
        if sticker.usage_count > 10 and quality_score < 0.3:
            spam_score = max(spam_score, 0.5)
        if sticker.nsfw_score > 0.5:
            quality_score = 0.1
            spam_score = max(spam_score, 0.8)

        mood_tags_str = ",".join(mood_tags)
        await self.store.update_sticker_classification(
            sticker.doc_id, mood_tags_str, quality_score, spam_score
        )
        sticker.mood_tags = mood_tags_str
        sticker.quality_score = quality_score
        sticker.spam_score = spam_score

    def _extract_vision_tags(self, summary: str) -> str:
        summary_lower = (summary or "").casefold()
        tags = [
            tag for tag, keywords in self.VISION_TAG_KEYWORDS.items()
            if any(keyword in summary_lower for keyword in keywords)
        ]
        return ",".join(tags)

    def _calculate_nsfw_score(self, summary: str) -> float:
        summary_lower = (summary or "").casefold()
        score = sum(1 for keyword in self.NSFW_KEYWORDS if keyword in summary_lower)
        return min(score * 0.2, 1.0)

    def extract_vision_tags(self, summary: str) -> str:
        return self._extract_vision_tags(summary)

    def calculate_nsfw_score(self, summary: str) -> float:
        return self._calculate_nsfw_score(summary)
