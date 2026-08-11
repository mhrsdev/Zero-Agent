from __future__ import annotations

import asyncio
import logging
import os
import random
import tempfile
import time
from typing import Optional

from telethon import functions
from telethon.tl import types

from zero.config import ZeroConfig
from zero.storage import ZeroStore
from zero.vision import VisionProcessor
from zero.stickers.models import Sticker, StickerSet, StickerCandidate
from zero.stickers.account_saver import StickerAccountSaver
from zero.stickers.sender import StickerSender
from zero.stickers.classifier import StickerClassifier

logger = logging.getLogger('zero.stickers.observer')


class StickerObserver:
    """Handles incoming sticker messages and processes them for the sticker library."""

    def __init__(
        self,
        config: ZeroConfig,
        store,
        client,
        vision: Optional = None,
    ):
        self.config = config
        self.store = store
        self.client = client
        self.vision = vision
        self._temp_dir = tempfile.gettempdir()
        self.account_saver = StickerAccountSaver(config, store, client)
        self.sender = StickerSender(config, store, client)
        self.classifier = StickerClassifier(config, store, vision)

    def is_sticker_media(self, event) -> bool:
        """Check if the event contains a sticker."""
        if not event.media:
            return False
        if event.sticker:
            return True
        if event.document:
            for attr in event.document.attributes:
                if isinstance(attr, types.DocumentAttributeSticker):
                    return True
        return False

    async def process_sticker(
        self,
        event,
        sender_id: int,
        sender_label: str,
        chat_id: int,
    ):
        """Process a sticker message: extract metadata, run vision, classify, store."""
        if not self.is_sticker_media(event):
            return None

        doc = event.document
        if not doc:
            return None

        # Extract sticker attribute
        sticker_attr = None
        for attr in doc.attributes:
            if isinstance(attr, types.DocumentAttributeSticker):
                sticker_attr = attr
                break

        if not sticker_attr:
            logger.warning("Document has no sticker attribute")
            return None

        # Extract stickerset info
        stickerset_id = None
        stickerset_access_hash = None
        stickerset_short_name = None

        if sticker_attr.stickerset:
            if isinstance(sticker_attr.stickerset, types.InputStickerSetID):
                stickerset_id = sticker_attr.stickerset.id
                stickerset_access_hash = sticker_attr.stickerset.access_hash
            elif isinstance(sticker_attr.stickerset, types.InputStickerSetShortName):
                stickerset_short_name = sticker_attr.stickerset.short_name

        # Build Sticker object
        now = int(time.time())
        sticker = Sticker(
            doc_id=doc.id,
            access_hash=doc.access_hash,
            file_reference=doc.file_reference,
            mime_type=doc.mime_type,
            emoji=sticker_attr.alt or '',
            stickerset_id=stickerset_id,
            stickerset_access_hash=stickerset_access_hash,
            stickerset_short_name=stickerset_short_name,
            is_animated=doc.mime_type == 'application/x-tgsticker',
            is_video=doc.mime_type == 'video/webm',
            first_seen=now,
            last_seen=now,
            first_sender_id=sender_id,
            usage_count=1,
        )

        # Check if exists and update
        existing = await self.store.get_sticker(doc.id)
        if existing:
            # Refresh file_reference and increment usage
            await self.store.increment_sticker_usage(doc.id, sender_id)
            await self.store.update_sticker_file_reference(
                doc.id,
                doc.file_reference,
                doc.access_hash
            )
            await self.store.record_sticker_observation(doc.id, chat_id, sender_id, int(getattr(event, 'id', 0) or 0))
            logger.info('STICKER_EXISTING_UPDATED doc_id=%s chat_id=%s sender_id=%s', doc.id, chat_id, sender_id)
            updated = await self.store.get_sticker(doc.id)
            await self.maybe_auto_save(updated)
            return updated

        # Save new sticker
        await self.store.add_sticker(sticker)
        await self.store.record_sticker_observation(doc.id, chat_id, sender_id, int(getattr(event, 'id', 0) or 0))
        logger.info('STICKER_OBSERVED doc_id=%s chat_id=%s sender_id=%s message_id=%s', doc.id, chat_id, sender_id, getattr(event, 'id', 0))
        logger.info('STICKER_NEW_SAVED doc_id=%s chat_id=%s', doc.id, chat_id)

        # Process stickerset if we have short name
        if stickerset_short_name:
            await self._process_stickerset(stickerset_short_name)

        # Run vision analysis if enabled and applicable
        if self.vision and self.config.vision.enabled and not sticker.is_animated and not sticker.is_video:
            await self._process_vision(sticker, event)

        # Classify mood and quality
        await self._classify_sticker(sticker)
        await self.maybe_auto_save(sticker)

        return sticker

    async def process_gif(self, event, sender_id: int, sender_label: str, chat_id: int):
        """Store a Telegram animation with bounded, caption-derived semantics."""
        doc = getattr(event, "document", None)
        if not doc:
            logger.info("GIF_INGESTION_SKIPPED reason=unsupported_media")
            return None
        now = int(time.time())
        caption = (getattr(event, "raw_text", "") or "").strip()[:1000]
        vision_tags = self.classifier.extract_vision_tags(caption) if caption else ""
        nsfw_score = self.classifier.calculate_nsfw_score(caption) if caption else 0.0
        existing = await self.store.get_sticker(doc.id)
        if existing:
            await self.store.increment_sticker_usage(doc.id, sender_id)
            await self.store.update_sticker_file_reference(
                doc.id, doc.file_reference, doc.access_hash
            )
            await self.store.record_sticker_observation(
                doc.id, chat_id, sender_id, int(getattr(event, "id", 0) or 0)
            )
            if caption and (not existing.vision_summary or not existing.mood_tags):
                await self.store.update_sticker_vision(
                    doc.id, caption, vision_tags, nsfw_score
                )
                existing.vision_summary = caption
                existing.vision_tags = vision_tags
                existing.nsfw_score = nsfw_score
                await self.classifier.classify_sticker(existing)
                logger.info(
                    "GIF_SEMANTICS_BACKFILLED doc_id=%s tags=%s",
                    doc.id, vision_tags or "none",
                )
            return await self.store.get_sticker(doc.id)
        media = Sticker(
            doc_id=doc.id,
            access_hash=doc.access_hash,
            file_reference=doc.file_reference,
            mime_type=doc.mime_type or "video/mp4",
            emoji="",
            stickerset_id=None,
            stickerset_access_hash=None,
            stickerset_short_name=None,
            is_animated=False,
            is_video=True,
            vision_summary=caption or None,
            vision_tags=vision_tags or None,
            nsfw_score=nsfw_score,
            first_seen=now,
            last_seen=now,
            first_sender_id=sender_id,
            usage_count=1,
        )
        await self.store.add_sticker(media)
        await self.store.record_sticker_observation(
            doc.id, chat_id, sender_id, int(getattr(event, "id", 0) or 0)
        )
        await self.classifier.classify_sticker(media)
        logger.info(
            "GIF_OBSERVED doc_id=%s chat_id=%s sender_id=%s message_id=%s tags=%s",
            doc.id, chat_id, sender_id, getattr(event, "id", 0),
            vision_tags or "none",
        )
        return await self.store.get_sticker(doc.id)

    async def _process_stickerset(self, short_name: str) -> None:
        """Fetch and store stickerset info."""
        try:
            result = await self.client(functions.messages.GetStickerSetRequest(
                stickerset=types.InputStickerSetShortName(short_name=short_name),
                hash=0
            ))
            if result and result.set:
                sticker_set = StickerSet(
                    set_id=result.set.id,
                    access_hash=result.set.access_hash,
                    short_name=result.set.short_name,
                    title=result.set.title,
                    count=result.set.count or 0,
                    is_animated=bool(getattr(result.set, 'animated', False)),
                    is_video=bool(getattr(result.set, 'videostickers', False)),
                    is_official=bool(getattr(result.set, 'official', False)),
                    updated_at=int(time.time()),
                )
                await self.store.add_sticker_set(sticker_set)
                logger.info(f"Stickerset saved: {short_name}")
        except Exception as e:
            logger.warning(f"Failed to process stickerset {short_name}: {e}")

    async def _process_vision(self, sticker, event) -> None:
        """Run vision analysis on sticker image."""
        if not self.vision:
            return

        # Only process static images
        if not self.config.vision.enabled or sticker.is_animated or sticker.is_video:
            return

        temp_path = None
        try:
            # Download to a unique temp file; concurrent observations must not share paths.
            fd, temp_path = tempfile.mkstemp(prefix=f"sticker_{sticker.doc_id}_", suffix='.webp', dir=self._temp_dir)
            os.close(fd)
            await event.download_media(file=temp_path)

            # Analyze with vision
            question = "Describe this sticker/image. What text, objects, emotions, or memes does it contain? Is it NSFW, offensive, or political?"
            result = await self.vision.analyze(temp_path, question=question)
            if not (result or "").strip():
                logger.info("STICKER_VISION_SKIPPED doc_id=%s reason=no_semantic_signature", sticker.doc_id)
                return
            result = result.strip()

            # Update sticker with vision results
            vision_summary = result[:500] if result else ''
            vision_tags = self._extract_vision_tags(vision_summary)
            nsfw_score = self._calculate_nsfw_score(vision_summary)

            await self.store.update_sticker_vision(
                sticker.doc_id, vision_summary, vision_tags, nsfw_score
            )

            # Update sticker object
            sticker.vision_summary = vision_summary
            sticker.vision_tags = vision_tags
            sticker.nsfw_score = nsfw_score

            logger.debug(f"Vision analysis for {sticker.doc_id}: tags={vision_tags}, nsfw={nsfw_score:.2f}")

        except Exception as e:
            logger.warning(f"Vision processing failed for sticker {sticker.doc_id}: {e}")
        finally:
            # Cleanup temp file
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

    def _extract_vision_tags(self, summary: str) -> str:
        return self.classifier.extract_vision_tags(summary)

    def _calculate_nsfw_score(self, summary: str) -> float:
        return self.classifier.calculate_nsfw_score(summary)

    async def _classify_sticker(self, sticker) -> None:
        await self.classifier.classify_sticker(sticker)

    async def should_auto_save(self, sticker) -> bool:
        """Check if sticker should be auto-saved to account."""
        if sticker.saved_to_account:
            return False
        if sticker.nsfw_score > 0.5:
            return False
        if sticker.spam_score > 0.5:
            return False
        if sticker.quality_score < 0.7:
            return False
        if sticker.usage_count < 3:
            return False
        return True

    async def maybe_auto_save(self, sticker) -> bool:
        """Auto-save sticker to account if criteria met."""
        if not self.config.stickers.auto_save_enabled:
            return False

        if await self.should_auto_save(sticker):
            try:
                saved = await self.account_saver.save_to_favorites(sticker)
                if not saved:
                    logger.warning('STICKER_AUTO_SAVE_FAILED doc_id=%s reason=telegram_api', sticker.doc_id)
                    return False
                await self.store.mark_sticker_saved(sticker.doc_id)
                logger.info(f"Auto-saved sticker {sticker.doc_id} to favorites")
                return True
            except Exception as e:
                logger.warning(f"Auto-save failed for {sticker.doc_id}: {e}")
        return False

    async def maybe_send_sticker(
        self,
        chat_id: int,
        mood: str,
        sender_id: int,
        reply_to: Optional[int] = None,
    ) -> bool:
        """Try to send an appropriate sticker based on mood."""
        # Check if we should send a sticker (rate limit, chance, context)
        if not self._should_send_sticker(chat_id, sender_id):
            return False

        # Find appropriate sticker
        candidate = await self._find_sticker_for_mood(mood)
        if not candidate:
            return False

        # Send the sticker
        try:
            await self.sender.send_sticker(
                chat_id=chat_id,
                sticker=candidate,
                reply_to=reply_to,
            )
            await self.store.increment_sticker_usage(candidate.sticker.doc_id, sender_id)
            await self.store.mark_sticker_recent_saved(candidate.sticker.doc_id)
            return True
        except Exception as e:
            logger.warning(f"Failed to send sticker: {e}")
            return False

    def _should_send_sticker(self, chat_id: int, sender_id: int) -> bool:
        """Check if we should send a sticker (rate limits, chance, context)."""
        # Check rate limit
        if not self._check_rate_limit():
            return False

        # Check chance
        if random.random() > self.config.stickers.send_chance:
            return False

        # Don't send in serious/technical discussions
        # This could be enhanced with context analysis
        return True

    def _check_rate_limit(self) -> bool:
        """Check if we're within sticker rate limits."""
        # Could implement a more sophisticated rate limiter
        return True

    async def _find_sticker_for_mood(self, mood: str):
        """Find a suitable sticker for the given mood."""
        stickers = await self.store.get_sticker_by_mood(
            mood=mood,
            limit=20,
            min_quality=0.6
        )
        if not stickers:
            return None

        # Filter out NSFW, low quality, spam
        candidates = [
            s for s in stickers
            if s.nsfw_score < 0.5 and s.quality_score >= 0.6 and s.spam_score < 0.5
        ]

        if not candidates:
            return None

        # Prefer saved stickers, then by quality * recency
        candidates.sort(
            key=lambda s: (s.saved_to_account, s.quality_score * (1 + s.usage_count * 0.1)),
            reverse=True
        )

        # Pick from top 3 with some randomness
        chosen = random.choice(candidates[:3])
        return StickerCandidate(sticker=chosen, score=chosen.quality_score, match_reason=f"mood:{mood}")


from telethon.tl import types as t
from zero.stickers.models import StickerStats
