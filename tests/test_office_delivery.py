from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from zero.office.db import OfficeRepository
from zero.office.delivery import DeliveryCoordinator


def completed(repo, tmp_path, *, output=True):
    path = tmp_path / "result.docx"
    if output: path.write_bytes(b"valid")
    repo.reserve_and_create(
        job_id="j1", trace_id="t", user_id=10, chat_id=-100, message_id=50,
        operation_type="create_document", office_format="docx", request_text="بساز",
        input_filename="", input_path="", detected_mime="", input_size_bytes=0,
        uncompressed_size_bytes=0, extracted_characters=4, quota_date="2026-07-19",
        jobs_limit=1, character_limit=40000, installation_id="inst-test", group_id="group-test",
    )
    repo.transition("j1", "queued", expected="quota_reserved")
    repo.transition("j1", "planning", expected="queued")
    repo.transition("j1", "processing", expected="planning")
    repo.transition("j1", "validating_output", expected="processing")
    repo.transition("j1", "completed", expected="validating_output")
    repo.update_job_artifacts("j1", output_path=str(path) if output else "", result_text='{"data":"سلام"}')


class Router:
    async def complete_structured(self, prompt, max_output_tokens=1000): return SimpleNamespace(text="خلاصه فارسی")


class Client:
    def __init__(self, fail=False): self.calls=[]; self.fail=fail
    async def send_file(self, *args, **kwargs):
        self.calls.append(("file", args, kwargs))
        if self.fail: raise ConnectionError("down")
        return SimpleNamespace(id=101)
    async def send_message(self, *args, **kwargs):
        self.calls.append(("message", args, kwargs))
        if self.fail: raise ConnectionError("down")
        return SimpleNamespace(id=102)


@pytest.mark.asyncio
async def test_successful_delivery_is_idempotent_and_commits_quota_after_receipt(tmp_path):
    repo = OfficeRepository(tmp_path / "db.sqlite"); completed(repo, tmp_path)
    client = Client(); delivery = DeliveryCoordinator(repo, Router(), client)
    result = await delivery.tick()
    assert result["status"] == "sent"
    assert repo.get_job("j1")["quota_state"] == "committed"
    assert await delivery.tick() is None
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_definite_upload_failure_remains_retryable_and_does_not_commit(tmp_path):
    repo = OfficeRepository(tmp_path / "db.sqlite"); completed(repo, tmp_path)
    result = await DeliveryCoordinator(repo, Router(), Client(fail=True)).tick()
    assert result["status"] == "retryable_failed"
    assert repo.get_job("j1")["quota_state"] == "reserved"


def test_expired_inflight_delivery_becomes_ambiguous_not_replayed(tmp_path):
    repo = OfficeRepository(tmp_path / "db.sqlite"); completed(repo, tmp_path)
    assert repo.reserve_delivery("j1", "a", lease_seconds=1, now=100)
    assert repo.reserve_delivery("j1", "b", lease_seconds=1, now=102) is None
    with repo.connect() as conn:
        state = conn.execute("SELECT status FROM office_delivery_outbox WHERE job_id='j1'").fetchone()[0]
    assert state == "ambiguous"


@pytest.mark.asyncio
async def test_restart_after_sent_receipt_reconciles_quota_without_resend(tmp_path):
    repo = OfficeRepository(tmp_path / "db.sqlite"); completed(repo, tmp_path)
    key = repo.reserve_delivery("j1", "crashed", lease_seconds=300)
    repo.complete_delivery(key, status="sent", telegram_message_id=777)
    client = Client()
    result = await DeliveryCoordinator(repo, Router(), client).tick()
    assert result["status"] == "reconciled"
    assert repo.get_job("j1")["quota_state"] == "committed"
    assert client.calls == []


def test_delivery_receipt_and_quota_commit_are_one_database_transaction(tmp_path):
    repo = OfficeRepository(tmp_path / "db.sqlite"); completed(repo, tmp_path)
    key = repo.reserve_delivery("j1", "sender", lease_seconds=300)
    assert repo.complete_delivery_and_commit_quota(key, telegram_message_id=778)
    with repo.connect() as conn:
        outbox = conn.execute("SELECT status,telegram_message_id FROM office_delivery_outbox WHERE job_id='j1'").fetchone()
    assert tuple(outbox) == ("sent", 778)
    assert repo.get_job("j1")["quota_state"] == "committed"
