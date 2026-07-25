from __future__ import annotations

import logging

from telethon import functions
from telethon.tl import types

from zero.config import ZeroConfig
from zero.storage import ZeroStore
from zero.stickers.models import Sticker

logger = logging.getLogger('zero.stickers.account_saver')


class StickerAccountSaver:
    """Handles saving stickers to the account's Saved/Favorites/Recent stickers."""

    def __init__(
        self,
        config: ZeroConfig,
        store,
        client,
    ):
        self.config = config
        self.store = store
        self.client = client

    async def remove_from_favorites(self, sticker) -> bool:
        """Remove sticker from favorites."""
        try:
            await self.client(functions.messages.FaveStickerRequest(
                id=types.InputDocument(
                    id=sticker.doc_id,
                    access_hash=sticker.access_hash,
                    file_reference=sticker.file_reference,
                ),
                unfave=True
            ))

            logger.info(f"Sticker {sticker.doc_id} removed from favorites")
            return True

        except Exception as e:
            logger.warning(f"Failed to remove from favorites: {e}")
            return False

    async def save_to_recent(self, sticker) -> bool:
        """Add sticker to Recent Stickers panel."""
        try:
            input_doc = types.InputDocument(
                id=sticker.doc_id,
                access_hash=sticker.access_hash,
                file_reference=sticker.file_reference,
            )

            await self.client(functions.messages.SaveRecentStickerRequest(
                id=types.InputDocument(
                    id=sticker.doc_id,
                    access_hash=sticker.access_hash,
                    file_reference=sticker.file_reference,
                ),
                unsave=False,
                attached=False
            ))

            logger.debug(f"Sticker {sticker.doc_id} added to recent")
            return True

        except Exception as e:
            logger.warning(f"Failed to save to recent: {e}")
            return False

    async def remove_from_recent(self, sticker) -> bool:
        """Remove sticker from Recent Stickers."""
        try:
            await self.client(functions.messages.SaveRecentStickerRequest(
                id=types.InputDocument(
                    id=sticker.doc_id,
                    access_hash=sticker.access_hash,
                    file_reference=sticker.file_reference,
                ),
                unsave=True,
                attached=False
            ))

            return True

        except Exception as e:
            logger.warning(f"Failed to remove from recent: {e}")
            return False

    async def get_favorites(self):
        """Get list of favorited stickers."""
        from telethon import functions

        result = await self.client(functions.messages.GetFavedStickersRequest(hash=0))
        return result

    async def get_recent_stickers(self, attached: bool = False):
        """Get recent stickers."""
        from telethon import functions

        result = await self.client(functions.messages.GetRecentStickersRequest(
            hash=0,
            attached=attached
        ))
        return result

    async def clear_recent_stickers(self, attached: bool = False) -> bool:
        """Clear recent stickers."""
        try:
            from telethon import functions
            await self.client(functions.messages.ClearRecentStickersRequest(
                attached=attached
            ))
            return True
        except Exception as e:
            logger.warning(f"Failed to clear recent stickers: {e}")
            return False

    async def get_stickerset(self, short_name: str):
        """Get full sticker set info."""
        from telethon import functions
        from telethon.tl import types

        result = await self.client(functions.messages.GetStickerSetRequest(
            stickerset=types.InputStickerSetShortName(short_name=short_name),
            hash=0
        ))
        return result

    async def install_stickerset(self, short_name: str, archived: bool = False) -> bool:
        """Install a sticker set to the account."""
        try:
            from telethon import functions
            from telethon.tl import types

            await self.client(functions.messages.InstallStickerSetRequest(
                stickerset=types.InputStickerSetShortName(short_name=short_name),
                archived=archived
            ))
            return True
        except Exception as e:
            logger.warning(f"Failed to install stickerset {short_name}: {e}")
            return False

    async def uninstall_stickerset(self, short_name: str) -> bool:
        """Uninstall a sticker set from the account."""
        try:
            from telethon import functions
            from telethon.tl import types

            await self.client(functions.messages.UninstallStickerSetRequest(
                stickerset=types.InputStickerSetShortName(short_name=short_name)
            ))
            return True
        except Exception as e:
            logger.warning(f"Failed to uninstall stickerset {short_name}: {e}")
            return False

    async def save_to_favorites(self, sticker) -> bool:
        """Save sticker to favorites and update database.

        Args:
            sticker: Sticker object to save

        Returns:
            bool: True if saved successfully
        """
        if not self.config.stickers.auto_save_enabled or not self.client:
            if not self.config.stickers.auto_save_enabled:
                logger.info('STICKER_SAVE_SKIPPED reason=auto_save_disabled doc_id=%s', sticker.doc_id)
            else:
                logger.warning("No Telegram client available for saving to favorites")
            return False

        try:
            # Build InputDocument
            input_doc = types.InputDocument(
                id=sticker.doc_id,
                access_hash=sticker.access_hash,
                file_reference=sticker.file_reference
            )

            # Call Telegram API to save to favorites
            await self.client(functions.messages.FaveStickerRequest(
                id=input_doc,
                unfave=False
            ))

            # Update database
            await self.store.update_sticker_saved(sticker.doc_id, saved=True)
            logger.info(f"Saved sticker {sticker.doc_id} to favorites")
            return True

        except Exception as e:
            logger.error(f"Failed to save sticker to favorites: {e}", exc_info=True)
            return False