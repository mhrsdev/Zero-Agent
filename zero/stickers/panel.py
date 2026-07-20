from __future__ import annotations

import logging

from telethon.tl import types
from telethon import functions

from zero.config import ZeroConfig
from zero.storage import ZeroStore
from zero.stickers.models import Sticker, StickerStats
from zero.stickers.sender import StickerSender

logger = logging.getLogger('zero.stickers.panel')


class StickerPanel:
    """Admin panel commands for sticker management."""

    def __init__(
        self,
        config: ZeroConfig,
        store,
        client,
        sender: Optional = None,
        account_saver: Optional = None,
    ):
        self.config = config
        self.store = store
        self.client = client
        self.sender = sender
        self.account_saver = account_saver

    def register_commands(self, command_handler):
        """Register all sticker panel commands."""
        self.cmd_status = command_handler('stickers status', self.cmd_status)
        self.cmd_on = command_handler('stickers on', self.cmd_on)
        self.cmd_off = command_handler('stickers off', self.cmd_off)
        self.cmd_list = command_handler('stickers list', self.cmd_list)
        self.cmd_search = command_handler('stickers search', self.cmd_search)
        self.cmd_info = command_handler('stickers info', self.cmd_info)
        self.cmd_saved = command_handler('stickers saved', self.cmd_saved)
        self.cmd_recent = command_handler('stickers recent', self.cmd_recent)
        self.cmd_save = command_handler('stickers save', self.cmd_save)
        self.cmd_unsave = command_handler('stickers unsave', self.cmd_unsave)
        self.cmd_send = command_handler('stickers send', self.cmd_send)
        self.cmd_autosave_on = command_handler('stickers autosave on', self.cmd_autosave_on)
        self.cmd_autosave_off = command_handler('stickers autosave off', self.cmd_autosave_off)
        self.cmd_chance = command_handler('stickers chance', self.cmd_chance)
        self.cmd_cleanup = command_handler('stickers cleanup', self.cmd_cleanup)
        self.cmd_sets = command_handler('stickers sets', self.cmd_sets)
        self.cmd_install = command_handler('stickers install', self.cmd_install)
        self.cmd_uninstall = command_handler('stickers uninstall', self.cmd_uninstall)

    def _parse_args(self, text: str) -> list:
        """Parse command arguments."""
        parts = text.split()
        return parts[2:] if len(parts) > 2 else []

    async def cmd_status(self, event) -> str:
        """Show sticker system status."""
        if not self.config.stickers.enabled:
            return "🔴 Sticker system: **DISABLED**"

        stats = await self.store.get_sticker_stats()

        lines = [
            "📊 **Sticker System Status**",
            "",
            f"🟢 Enabled: **YES**",
            f"📦 Total stickers: **{stats['total']}**",
            f"⭐ Saved to account: **{stats['saved_to_account']}**",
            f"🕐 Recent saved: **{stats['recent_saved']}**",
            f"🎭 Animated: **{stats['animated']}**",
            f"📹 Video: **{stats['video']}**",
            f"📈 Avg quality: **{stats['avg_quality']:.2f}**",
            f"🔁 Total usage: **{stats['total_usage']}**",
            "",
            f"🎯 Send chance: **{self.config.stickers.send_chance*100:.0f}%**",
            f"💾 Auto-save: **{'ON' if self.config.stickers.auto_save_enabled else 'OFF'}**",
            f"🤖 Vision: **{'ON' if self.config.vision.enabled else 'OFF'}**",
        ]
        return '\n'.join(lines)

    async def cmd_on(self, event) -> str:
        """Enable sticker system."""
        self.config.stickers.enabled = True
        await self.store.set_setting('stickers_enabled', True)
        return "🟢 Sticker system **ENABLED**"

    async def cmd_off(self, event) -> str:
        """Disable sticker system."""
        self.config.stickers.enabled = False
        await self.store.set_setting('stickers_enabled', False)
        return "🔴 Sticker system **DISABLED**"

    async def cmd_list(self, event) -> str:
        """List stickers with optional mood filter."""
        args = event.text.split()[2:] if len(event.text.split()) > 2 else []
        mood = args[0] if args else None

        if mood:
            stickers = await self.store.get_sticker_by_mood(mood, limit=20)
        else:
            stickers = await self.store.get_stickers(limit=30, min_quality=0.5)

        if not stickers:
            return "📭 No stickers found"

        lines = ["📋 **Sticker Library**", ""]
        for i, s in enumerate(stickers[:20], 1):
            saved = "⭐" if s.saved_to_account else ""
            animated = "🎭" if s.is_animated else ""
            video = "📹" if s.is_video else ""
            mood_str = f" [{s.mood_tags}]" if s.mood_tags else ""
            lines.append(
                f"{i}. {s.emoji or '📦'} {s.mime_type} {animated}{video}{saved}"
                f" | Q:{s.quality_score:.2f} U:{s.usage_count}{mood_str}"
            )

        if len(stickers) > 20:
            lines.append(f"\n... and {len(stickers) - 20} more")

        return '\n'.join(lines)

    async def cmd_search(self, event) -> str:
        """Search stickers by query."""
        parts = event.text.split(maxsplit=2)
        if len(parts) < 3:
            return "Usage: /zero stickers search <query>"

        query = parts[2].strip().lower()

        # Search by mood tag
        stickers = await self.store.get_stickers(
            mood_filter=query,
            limit=20,
            min_quality=0.5
        )

        if not stickers:
            return f"🔍 No stickers found for '{query}'"

        lines = [f"🔍 **Search: {query}**", ""]
        for i, s in enumerate(stickers[:15], 1):
            lines.append(f"{i}. {s.emoji or '📦'} Q:{s.quality_score:.2f} U:{s.usage_count}")

        return '\n'.join(lines)

    async def cmd_info(self, event) -> str:
        """Show detailed info about a sticker."""
        args = event.text.split()[2:] if len(event.text.split()) > 2 else []
        if not args:
            return "Usage: /zero stickers info <doc_id>"

        try:
            doc_id = int(args[0])
        except ValueError:
            return "❌ Invalid doc_id"

        sticker = await self.store.get_sticker(doc_id)
        if not sticker:
            return f"❌ Sticker {doc_id} not found"

        lines = [
            "📋 **Sticker Info**",
            "",
            f"**ID:** `{sticker.doc_id}`",
            f"**Access Hash:** `{sticker.access_hash}`",
            f"**Emoji:** {sticker.emoji or 'none'}",
            f"**MIME:** {sticker.mime_type}",
            f"**Animated:** {'Yes' if sticker.is_animated else 'No'}",
            f"**Video:** {'Yes' if sticker.is_video else 'No'}",
            f"**Size:** {sticker.file_reference.__len__() if sticker.file_reference else 0} bytes ref",
            "",
            f"**Stickerset ID:** {sticker.stickerset_id or 'N/A'}",
            f"**Pack Short Name:** {sticker.stickerset_short_name or 'N/A'}",
            f"**Emoji:** {sticker.emoji or 'N/A'}",
            "",
            f"**Usage Count:** {sticker.usage_count}",
            f"**First Seen:** {sticker.first_seen}",
            f"**Last Seen:** {sticker.last_seen}",
            f"**First Sender:** {sticker.first_sender_id or 'N/A'}",
            "",
            f"⭐ Saved: {'Yes' if sticker.saved_to_account else 'No'}",
            f"🕐 Recent: {'Yes' if sticker.recent_saved else 'No'}",
            "",
            f"**Quality:** {sticker.quality_score:.2f}",
            f"**NSFW:** {sticker.nsfw_score:.2f}",
            f"**Spam:** {sticker.spam_score:.2f}",
            f"**Mood Tags:** {sticker.mood_tags or 'none'}",
            "",
            f"**Vision:** {sticker.vision_summary[:100] if sticker.vision_summary else 'N/A'}...",
            f"**Tags:** {sticker.vision_tags or 'none'}",
            f"**NSFW Score:** {sticker.nsfw_score:.2f}",
        ]
        return '\n'.join(lines)

    async def cmd_saved(self, event) -> str:
        """List saved stickers."""
        stickers = await self.store.get_saved_stickers(limit=30)

        if not stickers:
            return "📭 No saved stickers"

        lines = ["⭐ **Saved Stickers**", ""]
        for i, s in enumerate(stickers[:20], 1):
            lines.append(f"{i}. {s.emoji or '📦'} Q:{s.quality_score:.2f} U:{s.usage_count}")

        if len(stickers) > 20:
            lines.append(f"\n... and {len(stickers) - 20} more")

        return '\n'.join(lines)

    async def cmd_recent(self, event) -> str:
        """List recently used/saved stickers."""
        stickers = await self.store.get_recent_stickers(limit=20)

        if not stickers:
            return "📭 No recent stickers"

        lines = ["🕐 **Recent Stickers**", ""]
        for i, s in enumerate(stickers, 1):
            lines.append(f"{i}. {s.emoji or '📦'} Q:{s.quality_score:.2f} U:{s.usage_count}")

        return '\n'.join(lines)

    async def cmd_save(self, event) -> str:
        """Save a sticker to favorites."""
        args = event.text.split()[2:] if len(event.text.split()) > 2 else []
        if not args:
            return "Usage: /zero stickers save <doc_id>"

        try:
            doc_id = int(args[0])
        except ValueError:
            return "❌ Invalid doc_id"

        sticker = await self.store.get_sticker(doc_id)
        if not sticker:
            return f"❌ Sticker {doc_id} not found"

        if not self.account_saver:
            return "❌ Account saver not available"

        success = await self.account_saver.save_to_favorites(sticker)
        if success:
            await self.store.mark_sticker_saved(doc_id)
            return f"✅ Sticker {doc_id} saved to favorites"
        else:
            return f"❌ Failed to save sticker {doc_id}"

    async def cmd_unsave(self, event) -> str:
        """Remove a sticker from favorites."""
        args = event.text.split()[2:] if len(event.text.split()) > 2 else []
        if not args:
            return "Usage: /zero stickers unsave <doc_id>"

        try:
            doc_id = int(args[0])
        except ValueError:
            return "❌ Invalid doc_id"

        sticker = await self.store.get_sticker(doc_id)
        if not sticker:
            return f"❌ Sticker {doc_id} not found"

        if not self.account_saver:
            return "❌ Account saver not available"

        success = await self.account_saver.remove_from_favorites(sticker)
        if success:
            await self.store.update_sticker_saved(doc_id, False)
            return f"✅ Sticker {doc_id} removed from favorites"
        else:
            return f"❌ Failed to unsave sticker {doc_id}"

    async def cmd_send(self, event) -> str:
        """Manually send a sticker."""
        args = event.text.split()[2:] if len(event.text.split()) > 2 else []
        if not args:
            return "Usage: /zero stickers send <doc_id> [chat_id]"

        try:
            doc_id = int(args[0])
            chat_id = int(args[1]) if len(args) > 1 else event.chat_id
        except ValueError:
            return "❌ Invalid doc_id or chat_id"

        sticker = await self.store.get_sticker(doc_id)
        if not sticker:
            return f"❌ Sticker {doc_id} not found"

        if not self.sender:
            return "❌ Sender not available"

        try:
            success = await self.sender.send_sticker(
                chat_id=chat_id,
                sticker=StickerCandidate(sticker=sticker, score=sticker.quality_score),
                reply_to=None,
            )
            if success:
                return f"✅ Sticker {doc_id} sent to {chat_id}"
            else:
                return f"❌ Failed to send sticker {doc_id}"
        except Exception as e:
            return f"❌ Error: {e}"

    async def cmd_autosave_on(self, event) -> str:
        """Enable auto-save."""
        self.config.stickers.auto_save_enabled = True
        await self.store.set_setting('stickers_auto_save', True)
        return "💾 Auto-save **ENABLED**"

    async def cmd_autosave_off(self, event) -> str:
        """Disable auto-save."""
        self.config.stickers.auto_save_enabled = False
        await self.store.set_setting('stickers_auto_save', False)
        return "💾 Auto-save **DISABLED**"

    async def cmd_chance(self, event) -> str:
        """Set sticker send chance."""
        args = event.text.split()[2:] if len(event.text.split()) > 2 else []
        if not args:
            return f"Current chance: {self.config.stickers.send_chance*100:.0f}%\nUsage: /zero stickers chance <0-100>"

        try:
            chance = int(args[0]) / 100
            if not 0 <= chance <= 1:
                return "❌ Chance must be 0-100"
            self.config.stickers.send_chance = chance
            await self.store.set_setting('stickers_send_chance', chance)
            return f"🎯 Send chance set to **{chance*100:.0f}%**"
        except ValueError:
            return "❌ Invalid number"

    async def cmd_cleanup(self, event) -> str:
        """Clean up low quality / NSFW / spam stickers."""
        # This would delete low quality stickers
        # For safety, just report stats
        stats = await self.store.get_sticker_stats()
        return (
            "🧹 **Cleanup Report**\n"
            f"Total: {stats['total']}\n"
            f"Saved: {stats['saved_to_account']}\n"
            f"Avg Quality: {stats['avg_quality']:.2f}\n"
            "\nActual deletion not implemented for safety."
        )

    async def cmd_sets(self, event) -> str:
        """List installed sticker sets."""
        sets = await self.store.get_installed_sticker_sets()

        if not sets:
            return "📭 No sticker sets installed"

        lines = ["📚 **Installed Sticker Sets**", ""]
        for s in sets[:20]:
            anim = "🎭" if s.is_animated else ""
            video = "📹" if s.is_video else ""
            lines.append(f"{s.title} (@{s.short_name}) {anim}{video} - {s.count} stickers")

        return '\n'.join(lines)

    async def cmd_install(self, event) -> str:
        """Install a sticker set."""
        args = event.text.split()[2:] if len(event.text.split()) > 2 else []
        if not args:
            return "Usage: /zero stickers install <short_name>"

        short_name = args[0]

        if not self.account_saver:
            return "❌ Account saver not available"

        success = await self.account_saver.install_stickerset(short_name)
        if success:
            return f"✅ Sticker set `{short_name}` installed"
        else:
            return f"❌ Failed to install `{short_name}`"

    async def cmd_uninstall(self, event) -> str:
        """Uninstall a sticker set."""
        args = event.text.split()[2:] if len(event.text.split()) > 2 else []
        if not args:
            return "Usage: /zero stickers uninstall <short_name>"

        short_name = args[0]

        if not self.account_saver:
            return "❌ Account saver not available"

        success = await self.account_saver.uninstall_stickerset(short_name)
        if success:
            return f"✅ Sticker set `{short_name}` uninstalled"
        else:
            return f"❌ Failed to uninstall `{short_name}`"