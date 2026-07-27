"""End-to-end contract verification for the Telegram office adapter.

The TelegramOfficeBridge is the thin Telegram boundary in front of OfficeIntakeService.
It must honour three operating modes and never emit duplicate replies:

* **bot mode**      — Telegram *bot* senders (``sender.bot == True``) are dropped
  fail-closed before any download, intake call or reply.
* **user session mode** — a direct (``is_private``) message from a real user is
  accepted as a command and produces exactly one reply.
* **hybrid mode**   — inside a *group*, only explicit slash commands trigger
  handling; an unsolicited plain caption is rejected with guidance, never a
  duplicate queue acknowledgement.
* **duplicate replies** — the same ``(account_scope, chat_id, message_id)`` tuple
  must never produce two queue confirmations. Intake de-duplicates by message_id
  and the bridge replies exactly once.

These tests use real ``TelegramOfficeBridge`` + ``OfficeIntakeService`` +
``OfficeRepository`` objects (no mocks) and exercise the public ``handle_event``
contract directly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from zero.config import OfficeConfig
from zero.office.db import OfficeRepository
from zero.office.intake import OfficeIntakeService
from zero.office.telegram import TelegramOfficeBridge


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "source.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        z.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>سلام</w:t></w:r></w:p></w:body></w:document>",
        )
    return path.read_bytes()


class FakeEvent:
    """Minimal async Telegram event surface used by TelegramOfficeBridge."""

    def __init__(
        self,
        *,
        text: str = "",
        payload: bytes = b"",
        filename: str = "input.docx",
        document: bool = True,
        sender_id: int = 10,
        chat_id: int = -100,
        message_id: int = 50,
        is_private: bool = False,
        is_reply: bool = False,
        reply_document: "SimpleNamespace | None" = None,
        sender_bot: bool = False,
        fwd_from: object | None = None,
    ) -> None:
        self.raw_text = text
        self.sender_id = sender_id
        self.chat_id = chat_id
        self.id = message_id
        self.is_private = is_private
        self.is_reply = is_reply
        self.document = (
            SimpleNamespace(mime_type=DOCX_MIME, size=len(payload), attributes=[])
            if document
            else None
        )
        self.file = SimpleNamespace(name=filename, size=len(payload)) if document else None
        self.message = SimpleNamespace(fwd_from=fwd_from)
        self.date = datetime(2026, 7, 18, 20, 45, tzinfo=timezone.utc)
        self.payload = payload
        self.download_count = 0
        self.replies: list[str] = []
        self._reply_document = reply_document
        self._sender_bot = sender_bot

    async def get_sender(self) -> SimpleNamespace:
        return SimpleNamespace(bot=self._sender_bot)

    async def get_reply_message(self) -> "SimpleNamespace | None":
        return self._reply_document

    async def download_media(self, file):
        self.download_count += 1
        Path(file).write_bytes(self.payload)
        return file

    async def reply(self, text):
        self.replies.append(text)
        return SimpleNamespace(id=999)


def _bridge(tmp_path: Path, *, enabled: bool = True, bot_username: str = "ZeroBot"):
    cfg = OfficeConfig(
        enabled=enabled,
        workspace_root=str(tmp_path / "jobs"),
        cli_path="/usr/local/lib/zero-office/officecli",
    )
    repo = OfficeRepository(tmp_path / "db.sqlite")
    intake = OfficeIntakeService(cfg, repo, owner_user_id=1)
    return (
        TelegramOfficeBridge(
            cfg,
            intake,
            bot_username=bot_username,
            installation_id="inst-test",
            group_id="group-test",
        ),
        repo,
    )


# ---------------------------------------------------------------------------
# 1. Bot mode
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bot_sender_is_dropped_fail_closed_with_no_reply(tmp_path):
    """A Telegram bot-sender must never reach intake, never reply."""
    tool, repo = _bridge(tmp_path)
    event = FakeEvent(
        text="/docx بخوان",
        payload=_docx_bytes(tmp_path),
        sender_bot=True,
    )
    handled = await tool.handle_event(event)

    assert handled is False, "bot senders must be dropped before handling"
    assert event.download_count == 0, "bot content must never be downloaded"
    assert event.replies == [], "bot senders must receive no reply text"
    assert repo.list_jobs({"quota_reserved"}) == [], "bot content must not be queued"


# ---------------------------------------------------------------------------
# 2. User session mode (direct / private)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_user_session_mode_direct_message_accepted_exactly_once(tmp_path):
    """A private message with an explicit command is accepted once per message_id."""
    tool, repo = _bridge(tmp_path)
    event = FakeEvent(
        text="/docx متن را اصلاح کن",
        payload=_docx_bytes(tmp_path),
        is_private=True,
        chat_id=10,
        sender_id=10,
        message_id=777,
    )
    handled = await tool.handle_event(event)

    assert handled is True
    assert event.download_count == 1, "explicit command must download the attachment"
    jobs = repo.list_jobs({"quota_reserved"})
    assert len(jobs) == 1
    assert jobs[0]["message_id"] == 777
    # Exactly one reply acknowledgement.
    assert len(event.replies) == 1
    assert "صف" in event.replies[0] or "received" in event.replies[0].lower()


# ---------------------------------------------------------------------------
# 3. Hybrid mode (group requires explicit command + unsolicited rejects)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hybrid_mode_group_plain_caption_rejected_without_queuing(tmp_path):
    """In a group, an unsolicited plain attachment is not handled as a job."""
    tool, repo = _bridge(tmp_path)
    # No explicit slash command, just a plain doc caption.
    event = FakeEvent(
        text="فقط متن تست",
        payload=_docx_bytes(tmp_path),
        is_private=False,
        chat_id=-100,
    )
    handled = await tool.handle_event(event)

    assert handled is True, "the bridge still replies with usage guidance"
    assert event.download_count == 0, "group must not download uncommanded files"
    assert repo.list_jobs({"quota_reserved"}) == []
    assert len(event.replies) == 1


@pytest.mark.asyncio
async def test_hybrid_mode_group_explicit_command_queues_once(tmp_path):
    """Inside a group an explicit slash command queues exactly one job."""
    tool, repo = _bridge(tmp_path)
    event = FakeEvent(
        text="/docx این را بساز",
        payload=_docx_bytes(tmp_path),
        is_private=False,
        chat_id=-100,
        sender_id=10,
        message_id=9001,
    )
    handled = await tool.handle_event(event)

    assert handled is True
    assert event.download_count == 1
    jobs = repo.list_jobs({"quota_reserved"})
    assert len(jobs) == 1
    assert jobs[0]["message_id"] == 9001
    assert len(event.replies) == 1


# ---------------------------------------------------------------------------
# 4. No duplicate replies for the same message_id across scopes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_duplicate_message_id_does_not_produce_second_worker_job(tmp_path):
    """Intake dedups by (account_scope, chat_id, message_id); a re-send is acknowledged once,
    no second queue slot and no more than one reply is emitted."""
    tool, repo = _bridge(tmp_path)
    payload = _docx_bytes(tmp_path)

    first = FakeEvent(
        text="/docx اول",
        payload=payload,
        chat_id=-100,
        sender_id=10,
        message_id=555,
    )
    second = FakeEvent(
        text="/docx دوم",
        payload=payload,
        chat_id=-100,
        sender_id=10,
        message_id=555,  # same account_scope + chat_id + message_id
    )

    assert await tool.handle_event(first) is True
    assert await tool.handle_event(second) is True

    jobs = repo.list_jobs({"quota_reserved"})
    assert len(jobs) == 1, "duplicate message_id must not create a second job"
    # Each event received exactly one reply — no double-acknowledgements.
    assert len(first.replies) == 1
    assert len(second.replies) == 1
    # The second acknowledgement mentions the already-in-queue state.
    assert "صف" in second.replies[0] or "صف پردازش" in second.replies[0]


@pytest.mark.asyncio
async def test_different_chat_same_message_id_is_a_separate_job(tmp_path):
    """Sanity: dedup is keyed on chat_id as well as message_id."""
    tool, repo = _bridge(tmp_path)
    payload = _docx_bytes(tmp_path)

    event_a = FakeEvent(
        text="/docx الف",
        payload=payload,
        chat_id=-100,
        sender_id=10,
        message_id=333,
    )
    event_b = FakeEvent(
        text="/docx ب",
        payload=payload,
        chat_id=-200,  # different chat, same message_id → not a duplicate
        sender_id=11,
        message_id=333,
    )

    assert await tool.handle_event(event_a) is True
    assert await tool.handle_event(event_b) is True

    jobs = repo.list_jobs({"quota_reserved"})
    assert len(jobs) == 2
    assert {job["chat_id"] for job in jobs} == {-100, -200}
