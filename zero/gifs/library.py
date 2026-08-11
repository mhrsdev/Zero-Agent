from __future__ import annotations

from zero.stickers.library import StickerLibrary


class GifLibrary:
    """Type-safe GIF retrieval; never returns sticker documents."""

    def __init__(self, config, store, gifs_config, *, rng=None):
        self._library = StickerLibrary(config, store, gifs_config, rng=rng)

    async def search(self, *, mood: str, min_quality: float = 0.0, limit: int = 20):
        return await self._library.search(
            mood=mood,
            min_quality=min_quality,
            limit=limit,
            media_kind="gif",
        )

    async def choose(
        self,
        *,
        mood: str,
        min_quality: float,
        chat_id: int,
        exclude_doc_ids: set[int] | None = None,
    ):
        return await self._library.get_random_gif(
            mood=mood,
            min_quality=min_quality,
            chat_id=chat_id,
            exclude_doc_ids=exclude_doc_ids,
        )
