from __future__ import annotations

import errno
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from zero.config import OfficeConfig
from zero.office.adapter import AdapterError
from zero.office.cleanup import cleanup_expired_workspaces
from zero.office.db import OfficeRepository
from zero.office.delivery import VisualReviewCoordinator
from zero.office.intake import OfficeIntakeService, OfficeRequest
from zero.office.worker import OfficeWorker


def queued_job(tmp_path, *, max_attempts=1):
    cfg = OfficeConfig(enabled=True, workspace_root=str(tmp_path / "jobs"), cli_path="/usr/local/lib/zero-office/officecli", max_attempts=max_attempts, visual_review_enabled=False)
    repo = OfficeRepository(tmp_path / "db.sqlite")
    result = OfficeIntakeService(cfg, repo, owner_user_id=1).handle(OfficeRequest(
        text="/docx گزارش بساز", user_id=10, chat_id=-100, message_id=50,
        bot_username="ZeroBot", received_at=datetime(2026, 7, 18, 20, 45, tzinfo=timezone.utc),
    ))
    plan = {"version":1,"format":"docx","operation":"create_document","output_required":True,"operations":[]}
    repo.update_job_artifacts(result.job_id, plan_json=json.dumps(plan))
    repo.transition(result.job_id, "queued", expected="quota_reserved")
    return cfg, repo, result.job_id


@pytest.mark.parametrize("error", [OSError(errno.ENOSPC, "disk full"), PermissionError(errno.EACCES, "denied")])
def test_worker_filesystem_failure_is_contained_and_refunds(tmp_path, error):
    cfg, repo, job_id = queued_job(tmp_path)
    class Broken:
        def __init__(self, workspace): pass
        def execute(self, plan, input_path=None): raise error
    result = OfficeWorker(repo, cfg, adapter_factory=Broken).tick()
    assert result["status"] == "failed"
    assert repo.get_job(job_id)["quota_state"] == "refunded"


def test_malformed_or_render_failure_is_not_recorded_as_success(tmp_path):
    cfg, repo, job_id = queued_job(tmp_path)
    class Broken:
        def __init__(self, workspace): pass
        def execute(self, plan, input_path=None): raise AdapterError("no_screenshot_backend")
    result = OfficeWorker(repo, cfg, adapter_factory=Broken).tick()
    assert result["status"] == "failed"
    assert repo.get_job(job_id)["status"] == "failed"


@pytest.mark.asyncio
async def test_vision_provider_failure_degrades_without_crashing_job(tmp_path):
    cfg, repo, job_id = queued_job(tmp_path)
    repo.transition(job_id, "planning", expected="queued")
    repo.transition(job_id, "processing", expected="planning")
    repo.transition(job_id, "validating_output", expected="processing")
    repo.transition(job_id, "rendering", expected="validating_output")
    repo.transition(job_id, "reviewing", expected="rendering")
    async def broken(_paths, _request): raise TimeoutError("vision unavailable")
    result = await VisualReviewCoordinator(repo, review=broken).tick()
    assert result["visual_review"] == "unavailable"
    assert repo.get_job(job_id)["status"] == "completed"


def test_database_locked_does_not_create_partial_quota(monkeypatch, tmp_path):
    cfg = OfficeConfig(enabled=True, workspace_root=str(tmp_path / "jobs"), cli_path="/usr/local/lib/zero-office/officecli")
    repo = OfficeRepository(tmp_path / "db.sqlite")
    def locked(): raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(repo, "connect", locked)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        OfficeIntakeService(cfg, repo, owner_user_id=1).handle(OfficeRequest(
            text="/docx گزارش بساز", user_id=10, chat_id=1, message_id=1,
            bot_username="ZeroBot", received_at=datetime.now(timezone.utc),
        ))
    with sqlite3.connect(tmp_path / "db.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM office_jobs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM office_quota_usage").fetchone()[0] == 0


def test_cleanup_failure_is_reported_and_active_jobs_are_never_deleted(monkeypatch, tmp_path):
    cfg, repo, job_id = queued_job(tmp_path)
    workspace = Path(cfg.workspace_root) / "-100" / job_id
    assert workspace.exists()
    result = cleanup_expired_workspaces(repo, cfg, now=10**10)
    assert result["removed"] == 0
    assert workspace.exists()
