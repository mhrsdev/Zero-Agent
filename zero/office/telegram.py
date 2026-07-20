from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import uuid

from zero.config import OfficeConfig
from .command_gate import CommandGateError, parse_office_command
from .intake import LocalAttachment, OfficeIntakeService, OfficeRequest
from .workspace import sanitize_filename


_OFFICE_SUFFIXES = {".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm", ".doc", ".xls", ".ppt"}


class TelegramOfficeBridge:
    """Thin Telegram boundary; all policy remains deterministic in intake."""

    def __init__(self, config: OfficeConfig, intake: OfficeIntakeService, *, bot_username: str, account_scope: str = "telegram"):
        self.config, self.intake = config, intake
        self.bot_username = (bot_username or "").lstrip("@")
        self.account_scope = account_scope

    @staticmethod
    def _filename(message) -> str:
        name = str(getattr(getattr(message, "file", None), "name", "") or "")
        if name:
            return name
        for attr in getattr(getattr(message, "document", None), "attributes", ()) or ():
            candidate = getattr(attr, "file_name", "")
            if candidate:
                return str(candidate)
        return "attachment"

    @staticmethod
    def _mime(message) -> str:
        return str(getattr(getattr(message, "document", None), "mime_type", "") or "")

    @staticmethod
    def _date(message) -> datetime:
        value = getattr(message, "date", None)
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc)

    async def _download(self, message, suffix: str) -> tuple[Path, Path]:
        ingest_root = Path(self.config.workspace_root).resolve().parent / "office_ingest" / uuid.uuid4().hex
        ingest_root.mkdir(parents=True, mode=0o700)
        target = ingest_root / f"upload{suffix}"
        downloaded = await message.download_media(file=str(target))
        path = Path(downloaded or target)
        if not path.is_file():
            shutil.rmtree(ingest_root, ignore_errors=True)
            raise OSError("telegram_download_failed")
        path.chmod(0o400)
        return path, ingest_root

    async def handle_event(self, event) -> bool:
        if not self.config.enabled:
            return False
        if self.config.rollout_required:
            user_id = int(getattr(event, "sender_id", 0) or 0)
            chat_id = int(getattr(event, "chat_id", 0) or 0)
            if (
                not self.config.rollout_user_ids or not self.config.rollout_chat_ids
                or user_id not in self.config.rollout_user_ids
                or chat_id not in self.config.rollout_chat_ids
            ):
                return False
        sender = await event.get_sender()
        if bool(getattr(sender, "bot", False)):
            return False
        text = str(getattr(event, "raw_text", "") or "")
        direct_document = event if getattr(event, "document", None) is not None else None
        replied = await event.get_reply_message() if bool(getattr(event, "is_reply", False)) else None
        reply_document = replied if replied is not None and getattr(replied, "document", None) is not None else None

        explicit = False
        try:
            parse_office_command(text, bot_username=self.bot_username)
            explicit = True
        except CommandGateError:
            pass

        selected = direct_document or (reply_document if explicit else None)
        local_attachment = None
        cleanup_root: Path | None = None
        if selected is not None:
            filename = self._filename(selected)
            suffix = Path(filename).suffix.casefold()
            owner = int(getattr(selected, "sender_id", 0) or 0)
            chat_id = int(getattr(selected, "chat_id", getattr(event, "chat_id", 0)) or 0)
            if explicit:
                size = int(getattr(getattr(selected, "file", None), "size", 0) or getattr(getattr(selected, "document", None), "size", 0) or 0)
                if size > self.config.limits.max_file_size_mb * 1024 * 1024:
                    await event.reply("حجم فایل از سقف مجاز بیشتر است و سهمیه‌ای مصرف نشد.")
                    return True
                try:
                    local_path, cleanup_root = await self._download(selected, suffix if suffix in _OFFICE_SUFFIXES else ".bin")
                except OSError:
                    await event.reply("دریافت فایل کامل نشد و سهمیه‌ای مصرف نشد.")
                    return True
            else:
                # Metadata-only placeholder. Intake returns usage guidance before
                # touching the path, so files without commands are never fetched.
                local_path = Path("")
            local_attachment = LocalAttachment(
                path=local_path, filename=filename, declared_mime=self._mime(selected),
                owner_user_id=owner, chat_id=chat_id,
                message_id=int(getattr(selected, "id", 0) or 0), created_at=self._date(selected),
            )
        now = self._date(event)
        request = OfficeRequest(
            text=text, user_id=int(getattr(event, "sender_id", 0) or 0),
            chat_id=int(getattr(event, "chat_id", 0) or 0), message_id=int(getattr(event, "id", 0) or 0),
            bot_username=self.bot_username, received_at=now,
            is_group=not bool(getattr(event, "is_private", False)),
            trigger_valid=bool(explicit), is_forwarded=bool(getattr(getattr(event, "message", None), "fwd_from", None)),
            account_scope=self.account_scope,
            attachment=local_attachment if direct_document is not None else None,
            reply_attachment=local_attachment if direct_document is None and reply_document is not None else None,
            reply_context_present=bool(getattr(event, "is_reply", False)),
        )
        try:
            result = self.intake.handle(request)
        finally:
            if cleanup_root is not None:
                shutil.rmtree(cleanup_root, ignore_errors=True)
        if result.handled and result.message:
            await event.reply(result.message)
        return result.handled
