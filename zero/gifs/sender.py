from __future__ import annotations

from zero.stickers.models import StickerCandidate
from zero.stickers.sender import StickerSender


class GifSender:
    """GIF-only transport adapter; never attaches sticker attributes."""

    def __init__(self, config, store, client):
        self._sender = StickerSender(config, store, client)

    async def send(self, chat_id: int, media, *, relevance_score: float) -> bool:
        candidate = StickerCandidate(
            sticker=media,
            score=float(media.quality_score or 0.0),
            match_reason=f"gif:{media.mood_tags or 'unknown'}",
            relevance_score=relevance_score,
            fallback_level="exact",
        )
        return await self._sender.send_media(chat_id, candidate)
