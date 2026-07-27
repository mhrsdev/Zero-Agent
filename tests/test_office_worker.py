from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import zipfile

import pytest

from zero.config import OfficeConfig
from zero.office.adapter import AdapterError
from zero.office.db import OfficeRepository
from zero.office.intake import OfficeIntakeService, OfficeRequest
from zero.office.planner import OfficePlanner
from zero.office.worker import OfficeWorker, PlanningCoordinator


class RouteResult:
    def __init__(self, text): self.text = text


class Router:
    def __init__(self, payload): self.payload = payload
    async def complete_structured(self, prompt, max_output_tokens=4000): return RouteResult(json.dumps(self.payload, ensure_ascii=False))


def make_docx(path: Path, text="خروجی معتبر"):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("word/document.xml", f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')


def setup_job(tmp_path, *, max_attempts=1):
    cfg = OfficeConfig(enabled=True, workspace_root=str(tmp_path / "jobs"), cli_path="/usr/local/lib/zero-office/officecli", visual_review_enabled=False, max_attempts=max_attempts)
    repo = OfficeRepository(tmp_path / "db.sqlite")
    result = OfficeIntakeService(cfg, repo, owner_user_id=1).handle(OfficeRequest(
        text="/docx گزارش بساز", user_id=10, chat_id=-100, message_id=50,
        bot_username="ZeroBot", received_at=datetime(2026, 7, 18, 20, 45, tzinfo=timezone.utc),
        installation_id="inst-test", group_id="group-test",
    ))
    return cfg, repo, result.job_id


@pytest.mark.asyncio
async def test_planning_then_worker_success_persists_output_without_committing_before_delivery(tmp_path):
    cfg, repo, job_id = setup_job(tmp_path)
    payload = {"version": 1, "format": "docx", "operation": "create_document", "output_required": True,
               "operations": [{"command": "add", "parent": "/body", "type": "paragraph", "props": {"text": "خروجی معتبر"}}]}
    planned = await PlanningCoordinator(repo, cfg, OfficePlanner(Router(payload))).tick()
    assert planned["status"] == "queued"

    class FakeAdapter:
        def __init__(self, workspace): self.workspace = workspace
        def execute(self, plan, input_path=None):
            output = self.workspace.output / "result.docx"; make_docx(output)
            preview = self.workspace.preview / "preview.png"; preview.write_bytes(b"PNG" * 500)
            return {"output_path": str(output), "text": {"data": "خروجی معتبر"}, "validation": {}, "issues": {}, "preview_paths": [str(preview)]}

    outcome = OfficeWorker(repo, cfg, adapter_factory=FakeAdapter).tick()
    assert outcome["status"] == "completed"
    job = repo.get_job(job_id)
    assert Path(job["output_path"]).exists()
    assert job["quota_state"] == "reserved"
    assert repo.commit_quota(job_id) is True


@pytest.mark.asyncio
async def test_internal_adapter_failure_exhaustion_refunds_quota(tmp_path):
    cfg, repo, job_id = setup_job(tmp_path, max_attempts=1)
    payload = {"version": 1, "format": "docx", "operation": "create_document", "output_required": True, "operations": []}
    await PlanningCoordinator(repo, cfg, OfficePlanner(Router(payload))).tick()

    class FailingAdapter:
        def __init__(self, workspace): pass
        def execute(self, plan, input_path=None): raise AdapterError("officecli_timeout")

    outcome = OfficeWorker(repo, cfg, adapter_factory=FailingAdapter).tick()
    assert outcome["status"] == "failed"
    assert repo.get_job(job_id)["quota_state"] == "refunded"
    assert repo.quota_usage(10, "2026-07-19")["jobs_reserved"] == 0
