from __future__ import annotations

import logging
import random
import time
from typing import Optional

from telethon import functions
from telethon.tl import types

from zero.config import ZeroConfig
from zero.storage import ZeroStore
from zero.stickers.models import Sticker, StickerCandidate

logger = logging.getLogger('zero.stickers.sender')


class StickerSender:
    """Handles sending stickers with proper metadata and fallback strategies."""

    def __init__(
        self,
        config: ZeroConfig,
        store,
        client,
    ):
        self.config = config
        self.store = store
        self.client = client

    async def send_sticker(
        self,
        chat_id: int,
        sticker_candidate,
        reply_to: Optional[int] = None,
    ) -> bool:
        """
        Send a sticker to the specified chat.

        Args:
            chat_id: Target chat ID
            sticker_candidate: StickerCandidate object
            reply_to: Optional message ID to reply to

        Returns:
            bool: True if sent successfully
        """
        sticker = sticker_candidate.sticker

        # Try primary method: send_file with InputDocument + DocumentAttributeSticker
        success = await self._send_via_input_document(
            chat_id=chat_id,
            sticker=sticker,
            reply_to=reply_to,
        )

        if success:
            await self._post_send_updates(sticker)
            return True

        # Fallback: try forwarding if we have a message ID
        if sticker.last_message_id:
            try:
                await self.client.forward_messages(
                    entity=chat_id,
                    messages=sticker.last_message_id,
                    from_peer='me'  # From our saved messages
                )
                logger.info(f"Sent sticker via forward: {sticker.doc_id}")
                await self._post_send_updates(sticker)
                return True
            except Exception as e:
                logger.warning(f"Forward fallback failed: {e}")

        # Fallback: try to resolve from stickerset
        if sticker.stickerset_short_name or (sticker.stickerset_id and sticker.stickerset_access_hash):
            if await self._send_via_stickerset(chat_id, sticker):
                await self._post_send_updates(sticker)
                return True

        logger.error(f"All send methods failed for sticker {sticker.doc_id}")
        return False

    async def send_media(self, chat_id: int, sticker_candidate, reply_to: Optional[int] = None) -> bool:
        """Send a stored animation/GIF document without sticker attributes."""
        media = sticker_candidate.sticker
        try:
            document = types.InputDocument(id=media.doc_id, access_hash=media.access_hash, file_reference=media.file_reference)
            await self.client.send_file(chat_id, document, reply_to=reply_to)
            await self._post_send_updates(media)
            return True
        except Exception as exc:
            logger.warning('GIF_SEND_FAILED doc_id=%s error=%s', media.doc_id, type(exc).__name__)
            return False
    async def _send_via_input_document(
        self,
        chat_id: int,
        sticker,
        reply_to: Optional[int] = None,
    ) -> bool:
        """Send sticker using InputDocument + DocumentAttributeSticker."""
        try:
            # Build InputDocument
            input_doc = types.InputDocument(
                id=sticker.doc_id,
                access_hash=sticker.access_hash,
                file_reference=sticker.file_reference,
            )

            # Get sticker attribute
            sticker_attr = sticker.get_document_attribute_sticker()

            # Send
            msg = await self.client.send_file(
                entity=chat_id,
                file=sticker.to_input_document(),
                attributes=[sticker.get_document_attribute_sticker()],
                reply_to=reply_to,
            )

            # Update last_message_id
            await self.store.update_sticker_last_message(sticker.doc_id, msg.id)

            logger.info(f"Sent sticker {sticker.doc_id} to {chat_id}: {msg.id}")
            return True

        except Exception as e:
            logger.warning(f"Failed to send via InputDocument: {e}")
            return False

    async def _send_via_stickerset(self, chat_id: int, sticker) -> bool:
        """Try to send by resolving from stickerset."""
        if not (sticker.stickerset_short_name or (sticker.stickerset_id and sticker.stickerset_access_hash)):
            return False

        try:
            # Get fresh sticker from the set
            from telethon import functions
            from telethon.tl import types

            stickerset = (
                types.InputStickerSetShortName(short_name=sticker.stickerset_short_name)
                if sticker.stickerset_short_name else
                types.InputStickerSetID(id=sticker.stickerset_id, access_hash=sticker.stickerset_access_hash)
            )
            result = await self.client(functions.messages.GetStickerSetRequest(
                stickerset=stickerset,
                hash=0
            ))

            # Find our sticker in the set
            for doc in result.documents:
                if doc.id == sticker.doc_id:
                    # Send with fresh file_reference
                    fresh_input = types.InputDocument(
                        id=doc.id,
                        access_hash=doc.access_hash,
                        file_reference=doc.file_reference,
                    )
                    sticker_attr = next(
                        (a for a in doc.attributes if isinstance(a, types.DocumentAttributeSticker)),
                        None
                    )

                    if not sticker_attr:
                        return False
                    msg = await self.client.send_file(
                        entity=chat_id,
                        file=types.InputDocument(
                            id=doc.id,
                            access_hash=doc.access_hash,
                            file_reference=doc.file_reference,
                        ),
                        attributes=[sticker_attr],
                    )
                    await self.store.update_sticker_file_reference(sticker.doc_id, doc.file_reference, doc.access_hash)
                    logger.info(f"Sent via fresh stickerset ref: {sticker.doc_id}")
                    return True

        except Exception as e:
            logger.warning(f"Stickerset fallback failed: {e}")

        return False

    async def _post_send_updates(self, sticker):
        """Update database after successful send."""
        await self.store.increment_sticker_usage(sticker.doc_id, 0)  # sender_id=0 for bot sends
        await self.store.mark_sticker_recent_saved(sticker.doc_id)
        # Update last_message_id if we have it
        # This would need the message ID from the send result