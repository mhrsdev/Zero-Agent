from __future__ import annotations

import logging
from typing import Optional

from telethon import functions
from telethon.tl import types

logger = logging.getLogger("zero.stickers.sender")


class StickerSender:
    """Send a sticker with fresh-reference and provenance-aware fallbacks."""

    def __init__(self, config, store, client):
        self.config = config
        self.store = store
        self.client = client

    async def send_sticker(self, chat_id: int, sticker_candidate, reply_to: Optional[int] = None) -> bool:
        sticker = sticker_candidate.sticker
        if await self._send_via_input_document(chat_id, sticker, reply_to):
            await self._post_send_updates(sticker)
            return True

        source_chat_id = getattr(sticker, "source_chat_id", None)
        source_message_id = getattr(sticker, "source_message_id", None)
        if source_chat_id is not None and source_message_id:
            try:
                forwarded = await self.client.forward_messages(
                    entity=chat_id,
                    messages=source_message_id,
                    from_peer=source_chat_id,
                )
                message = forwarded[0] if isinstance(forwarded, (list, tuple)) and forwarded else forwarded
                if getattr(message, "id", None):
                    await self.store.update_sticker_last_message(sticker.doc_id, message.id)
                logger.info(
                    "STICKER_TRANSPORT_FALLBACK doc_id=%s method=source_forward",
                    sticker.doc_id,
                )
                await self._post_send_updates(sticker)
                return True
            except Exception as exc:
                logger.warning(
                    "STICKER_SOURCE_FORWARD_FAILED doc_id=%s error=%s",
                    sticker.doc_id,
                    type(exc).__name__,
                )

        if sticker.stickerset_short_name or (
            sticker.stickerset_id and sticker.stickerset_access_hash
        ):
            if await self._send_via_stickerset(chat_id, sticker, reply_to):
                await self._post_send_updates(sticker)
                return True

        logger.error("STICKER_TRANSPORT_FAILED doc_id=%s", sticker.doc_id)
        return False

    async def send_media(self, chat_id: int, sticker_candidate, reply_to: Optional[int] = None) -> bool:
        media = sticker_candidate.sticker
        try:
            document = types.InputDocument(
                id=media.doc_id,
                access_hash=media.access_hash,
                file_reference=media.file_reference,
            )
            message = await self.client.send_file(chat_id, document, reply_to=reply_to)
            if getattr(message, "id", None):
                await self.store.update_sticker_last_message(media.doc_id, message.id)
            await self._post_send_updates(media)
            logger.info("GIF_TRANSPORT_SENT doc_id=%s method=input_document", media.doc_id)
            return True
        except Exception as exc:
            logger.warning(
                "GIF_INPUT_DOCUMENT_FAILED doc_id=%s error=%s",
                media.doc_id, type(exc).__name__,
            )

        source_chat_id = getattr(media, "source_chat_id", None)
        source_message_id = getattr(media, "source_message_id", None)
        if source_chat_id is not None and source_message_id:
            try:
                forwarded = await self.client.forward_messages(
                    entity=chat_id,
                    messages=source_message_id,
                    from_peer=source_chat_id,
                )
                message = forwarded[0] if isinstance(forwarded, (list, tuple)) and forwarded else forwarded
                if getattr(message, "id", None):
                    await self.store.update_sticker_last_message(media.doc_id, message.id)
                await self._post_send_updates(media)
                logger.info("GIF_TRANSPORT_FALLBACK doc_id=%s method=source_forward", media.doc_id)
                return True
            except Exception as exc:
                logger.warning(
                    "GIF_SOURCE_FORWARD_FAILED doc_id=%s error=%s",
                    media.doc_id, type(exc).__name__,
                )
        logger.error("GIF_TRANSPORT_FAILED doc_id=%s", media.doc_id)
        return False

    async def _send_via_input_document(self, chat_id: int, sticker, reply_to: Optional[int] = None) -> bool:
        try:
            message = await self.client.send_file(
                entity=chat_id,
                file=sticker.to_input_document(),
                attributes=[sticker.get_document_attribute_sticker()],
                reply_to=reply_to,
            )
            if getattr(message, "id", None):
                await self.store.update_sticker_last_message(sticker.doc_id, message.id)
            logger.info(
                "STICKER_TRANSPORT_SENT doc_id=%s method=input_document", sticker.doc_id
            )
            return True
        except Exception as exc:
            logger.warning(
                "STICKER_INPUT_DOCUMENT_FAILED doc_id=%s error=%s",
                sticker.doc_id,
                type(exc).__name__,
            )
            return False

    async def _send_via_stickerset(
        self,
        chat_id: int,
        sticker,
        reply_to: Optional[int] = None,
    ) -> bool:
        if not (
            sticker.stickerset_short_name
            or (sticker.stickerset_id and sticker.stickerset_access_hash)
        ):
            return False
        try:
            sticker_set = (
                types.InputStickerSetShortName(short_name=sticker.stickerset_short_name)
                if sticker.stickerset_short_name
                else types.InputStickerSetID(
                    id=sticker.stickerset_id,
                    access_hash=sticker.stickerset_access_hash,
                )
            )
            result = await self.client(
                functions.messages.GetStickerSetRequest(stickerset=sticker_set, hash=0)
            )
            for document in result.documents:
                if document.id != sticker.doc_id:
                    continue
                sticker_attribute = next(
                    (
                        attribute
                        for attribute in document.attributes
                        if isinstance(attribute, types.DocumentAttributeSticker)
                    ),
                    None,
                )
                if sticker_attribute is None:
                    return False
                message = await self.client.send_file(
                    entity=chat_id,
                    file=types.InputDocument(
                        id=document.id,
                        access_hash=document.access_hash,
                        file_reference=document.file_reference,
                    ),
                    attributes=[sticker_attribute],
                    reply_to=reply_to,
                )
                await self.store.update_sticker_file_reference(
                    sticker.doc_id, document.file_reference, document.access_hash
                )
                if getattr(message, "id", None):
                    await self.store.update_sticker_last_message(sticker.doc_id, message.id)
                logger.info(
                    "STICKER_TRANSPORT_FALLBACK doc_id=%s method=stickerset_refresh",
                    sticker.doc_id,
                )
                return True
        except Exception as exc:
            logger.warning(
                "STICKER_STICKERSET_REFRESH_FAILED doc_id=%s error=%s",
                sticker.doc_id,
                type(exc).__name__,
            )
        return False

    async def _post_send_updates(self, sticker) -> None:
        await self.store.increment_sticker_usage(sticker.doc_id, 0)
        await self.store.mark_sticker_recent_saved(sticker.doc_id)
