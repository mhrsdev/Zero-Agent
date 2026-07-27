from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zero.config import OfficeConfig
from zero.office.db import OfficeRepository
from zero.office.delivery import DeliveryCoordinator
from zero.office.intake import OfficeIntakeService
from zero.office.planner import OfficePlanner
from zero.office.telegram import TelegramOfficeBridge
from zero.office.worker import OfficeWorker, PlanningCoordinator


class TelegramEvent:
    def __init__(self):
        self.raw_text = "/docx یک گزارش فارسی کوتاه بساز"
        self.sender_id = 7001
        self.chat_id = -10077
        self.id = 901
        self.is_private = False
        self.is_reply = False
        self.document = None
        self.file = None
        self.message = SimpleNamespace(fwd_from=None)
        self.date = datetime(2026, 7, 18, 20, 45, tzinfo=timezone.utc)
        self.replies = []
    async def get_sender(self): return SimpleNamespace(bot=False)
    async def get_reply_message(self): return None
    async def reply(self, text): self.replies.append(text); return SimpleNamespace(id=902)


class PlanningRouter:
    async def complete_structured(self, prompt, max_output_tokens=4000):
        plan = {
            "version": 1, "format": "docx", "operation": "create_document", "output_required": True,
            "operations": [
                {"command": "add", "parent": "/body", "type": "paragraph", "props": {"text": "گزارش فارسی", "style": "Heading1", "direction": "rtl"}},
                {"command": "add", "parent": "/body", "type": "paragraph", "props": {"text": "این گزارش به‌صورت امن تولید شد.", "direction": "rtl"}},
            ],
        }
        return SimpleNamespace(text=json.dumps(plan, ensure_ascii=False))


class DeliveryClient:
    def __init__(self): self.files = []
    async def send_file(self, chat_id, path, **kwargs):
        assert Path(path).is_file() and Path(path).suffix == ".docx"
        self.files.append((chat_id, path, kwargs))
        return SimpleNamespace(id=903)
    async def send_message(self, *args, **kwargs): raise AssertionError("output job must send a file")


@pytest.mark.asyncio
async def test_telegram_like_create_to_real_officecli_render_delivery_and_quota_commit(tmp_path):
    cfg = OfficeConfig(
        enabled=True, cli_path="/usr/local/lib/zero-office/officecli",
        workspace_root=str(tmp_path / "office_jobs"), visual_review_enabled=False,
    )
    repo = OfficeRepository(tmp_path / "zero.db")
    event = TelegramEvent()
    bridge = TelegramOfficeBridge(cfg, OfficeIntakeService(cfg, repo, owner_user_id=1), bot_username="ZeroBot")
    assert await bridge.handle_event(event) is True
    assert "صف پردازش" in event.replies[-1]

    planned = await PlanningCoordinator(repo, cfg, OfficePlanner(PlanningRouter())).tick()
    assert planned["status"] == "queued"
    processed = OfficeWorker(repo, cfg).tick()
    assert processed["status"] == "completed"

    client = DeliveryClient()
    delivered = await DeliveryCoordinator(repo, PlanningRouter(), client).tick()
    assert delivered["status"] == "sent"
    job = repo.get_job(delivered["job_id"])
    assert job["quota_state"] == "committed"
    assert len(client.files) == 1
    usage = repo.quota_usage(7001, "2026-07-19")
    assert usage["jobs_reserved"] == 0 and usage["jobs_committed"] == 1
