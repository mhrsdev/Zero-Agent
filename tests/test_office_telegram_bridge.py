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


def docx_bytes(tmp_path):
    path = tmp_path / "source.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>سلام</w:t></w:r></w:p></w:body></w:document>')
    return path.read_bytes()


class FakeEvent:
    def __init__(self, *, text, payload=b"", filename="input.docx", document=True):
        self.raw_text = text
        self.sender_id = 10
        self.chat_id = -100
        self.id = 50
        self.is_private = False
        self.is_reply = False
        self.document = SimpleNamespace(mime_type=DOCX_MIME, size=len(payload), attributes=[] ) if document else None
        self.file = SimpleNamespace(name=filename, size=len(payload)) if document else None
        self.message = SimpleNamespace(fwd_from=None)
        self.date = datetime(2026, 7, 18, 20, 45, tzinfo=timezone.utc)
        self.payload = payload
        self.download_count = 0
        self.replies = []
    async def get_sender(self): return SimpleNamespace(bot=False)
    async def get_reply_message(self): return None
    async def download_media(self, file):
        self.download_count += 1
        Path(file).write_bytes(self.payload)
        return file
    async def reply(self, text):
        self.replies.append(text)
        return SimpleNamespace(id=999)


def bridge(tmp_path, *, enabled=True):
    cfg = OfficeConfig(enabled=enabled, workspace_root=str(tmp_path / "jobs"), cli_path="/usr/local/lib/zero-office/officecli")
    repo = OfficeRepository(tmp_path / "db.sqlite")
    return TelegramOfficeBridge(cfg, OfficeIntakeService(cfg, repo, owner_user_id=1), bot_username="ZeroBot"), repo


@pytest.mark.asyncio
async def test_disabled_bridge_never_downloads_or_handles(tmp_path):
    tool, repo = bridge(tmp_path, enabled=False)
    event = FakeEvent(text="/docx بخوان", payload=docx_bytes(tmp_path))
    assert await tool.handle_event(event) is False
    assert event.download_count == 0
    assert repo.list_jobs({"quota_reserved"}) == []


@pytest.mark.asyncio
async def test_limited_rollout_nonmember_is_fail_closed_before_download(tmp_path):
    cfg = OfficeConfig(
        enabled=True, rollout_required=True, rollout_user_ids=[99], rollout_chat_ids=[-100],
        workspace_root=str(tmp_path / "jobs"), cli_path="/usr/local/lib/zero-office/officecli",
    )
    repo = OfficeRepository(tmp_path / "db.sqlite")
    tool = TelegramOfficeBridge(cfg, OfficeIntakeService(cfg, repo, owner_user_id=1), bot_username="ZeroBot")
    event = FakeEvent(text="/docx بخوان", payload=docx_bytes(tmp_path))
    assert await tool.handle_event(event) is False
    assert event.download_count == 0
    assert repo.list_jobs({"quota_reserved"}) == []


@pytest.mark.asyncio
async def test_file_without_command_is_metadata_only_and_never_downloaded(tmp_path):
    tool, repo = bridge(tmp_path)
    event = FakeEvent(text="", payload=docx_bytes(tmp_path))
    assert await tool.handle_event(event) is True
    assert event.download_count == 0
    assert "/docx" in event.replies[0]
    assert repo.list_jobs({"quota_reserved"}) == []


@pytest.mark.asyncio
async def test_explicit_matching_command_downloads_once_and_queues(tmp_path):
    tool, repo = bridge(tmp_path)
    event = FakeEvent(text="/docx متن را اصلاح کن", payload=docx_bytes(tmp_path))
    assert await tool.handle_event(event) is True
    assert event.download_count == 1
    jobs = repo.list_jobs({"quota_reserved"})
    assert len(jobs) == 1
    assert jobs[0]["user_id"] == 10


@pytest.mark.asyncio
async def test_command_in_middle_of_caption_does_not_download_or_queue(tmp_path):
    tool, repo = bridge(tmp_path)
    event = FakeEvent(text="متن /docx اصلاح کن", payload=docx_bytes(tmp_path))
    assert await tool.handle_event(event) is True
    assert event.download_count == 0
    assert repo.list_jobs({"quota_reserved"}) == []
