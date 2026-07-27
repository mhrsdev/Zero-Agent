from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import time

import pytest

from zero.office.db import OfficeRepository, QuotaExceeded, StateTransitionError


def make_repo(tmp_path):
    return OfficeRepository(tmp_path / "zero.db")


def new_job(repo, *, job_id="j1", user_id=10, message_id=100, chars=12, quota_date="2026-07-19", limit=1, unlimited=False):
    return repo.reserve_and_create(
        job_id=job_id,
        trace_id="trace",
        user_id=user_id,
        chat_id=-1001,
        message_id=message_id,
        operation_type="create_document",
        office_format="docx",
        request_text="درخواست",
        input_filename="",
        input_path="",
        detected_mime="",
        input_size_bytes=0,
        uncompressed_size_bytes=0,
        extracted_characters=chars,
        quota_date=quota_date,
        jobs_limit=limit,
        character_limit=40_000,
        unlimited=unlimited,
        installation_id="inst-test",
        group_id="group-test",
    )


def test_migration_is_additive_idempotent_and_has_required_constraints(tmp_path):
    db = tmp_path / "zero.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE existing_data(id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO existing_data(value) VALUES ('keep')")
    repo = OfficeRepository(db)
    OfficeRepository(db)
    with repo.connect() as conn:
        assert conn.execute("SELECT value FROM existing_data").fetchone()[0] == "keep"
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"office_jobs", "office_quota_usage", "office_job_events", "office_delivery_outbox"} <= tables
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_quota_reserve_commit_and_refund_are_accounted(tmp_path):
    repo = make_repo(tmp_path)
    new_job(repo)
    usage = repo.quota_usage(10, "2026-07-19")
    assert (usage["jobs_reserved"], usage["jobs_committed"]) == (1, 0)
    assert repo.commit_quota("j1") is True
    assert repo.commit_quota("j1") is False
    usage = repo.quota_usage(10, "2026-07-19")
    assert (usage["jobs_reserved"], usage["jobs_committed"]) == (0, 1)

    new_job(repo, job_id="j2", user_id=11, message_id=101)
    assert repo.refund_quota("j2", "internal_failure") is True
    assert repo.refund_quota("j2", "internal_failure") is False
    usage = repo.quota_usage(11, "2026-07-19")
    assert usage["jobs_reserved"] == usage["jobs_committed"] == 0


def test_refund_reopens_daily_slot_but_commit_does_not(tmp_path):
    repo = make_repo(tmp_path)
    new_job(repo)
    with pytest.raises(QuotaExceeded):
        new_job(repo, job_id="j2", message_id=101)
    repo.refund_quota("j1", "timeout")
    new_job(repo, job_id="j2", message_id=101)
    repo.commit_quota("j2")
    with pytest.raises(QuotaExceeded):
        new_job(repo, job_id="j3", message_id=102)


def test_unlimited_admin_does_not_mutate_usage(tmp_path):
    repo = make_repo(tmp_path)
    for index in range(3):
        new_job(repo, job_id=f"j{index}", message_id=100 + index, unlimited=True)
    assert repo.quota_usage(10, "2026-07-19") == {
        "jobs_reserved": 0,
        "jobs_committed": 0,
        "characters_reserved": 0,
        "characters_committed": 0,
    }


def test_atomic_quota_allows_only_one_of_ten_concurrent_requests(tmp_path):
    db = tmp_path / "zero.db"
    OfficeRepository(db)

    def attempt(index):
        repo = OfficeRepository(db)
        try:
            new_job(repo, job_id=f"j{index}", message_id=100 + index)
            return True
        except QuotaExceeded:
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        outcomes = list(pool.map(attempt, range(10)))
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 9


def test_duplicate_telegram_update_is_idempotent_and_does_not_double_reserve(tmp_path):
    repo = make_repo(tmp_path)
    first = new_job(repo)
    second = new_job(repo, job_id="different")
    assert second["id"] == first["id"]
    assert repo.quota_usage(10, "2026-07-19")["jobs_reserved"] == 1


def test_state_machine_records_events_and_rejects_invalid_or_terminal_transition(tmp_path):
    repo = make_repo(tmp_path)
    new_job(repo)
    assert repo.transition("j1", "queued", expected="quota_reserved")
    with pytest.raises(StateTransitionError):
        repo.transition("j1", "completed")
    repo.transition("j1", "planning", expected="queued")
    repo.transition("j1", "processing", expected="planning")
    repo.transition("j1", "validating_output", expected="processing")
    repo.transition("j1", "completed", expected="validating_output")
    with pytest.raises(StateTransitionError):
        repo.transition("j1", "processing")
    events = repo.events("j1")
    assert [event["to_status"] for event in events][-5:] == ["queued", "planning", "processing", "validating_output", "completed"]


def test_two_workers_cannot_claim_same_job(tmp_path):
    db = tmp_path / "zero.db"
    repo = OfficeRepository(db)
    new_job(repo)
    repo.transition("j1", "queued", expected="quota_reserved")

    def claim(worker):
        return OfficeRepository(db).claim_next(worker, lease_seconds=60, global_limit=1, per_user_limit=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["a", "b"]))
    assert sum(result is not None for result in results) == 1
    assert repo.get_job("j1")["status"] == "planning"


def test_expired_lease_is_recovered_once_and_attempt_budget_is_bounded(tmp_path):
    repo = make_repo(tmp_path)
    new_job(repo)
    repo.transition("j1", "queued", expected="quota_reserved")
    claimed = repo.claim_next("worker-a", lease_seconds=1, now=100, global_limit=1, per_user_limit=1)
    assert claimed and claimed["attempt_count"] == 1
    assert repo.recover_expired_leases(now=102, max_attempts=2) == {"requeued": 1, "failed": 0}
    claimed = repo.claim_next("worker-b", lease_seconds=1, now=103, global_limit=1, per_user_limit=1)
    assert claimed and claimed["attempt_count"] == 2
    assert repo.recover_expired_leases(now=105, max_attempts=2) == {"requeued": 0, "failed": 1}
    assert repo.get_job("j1")["status"] == "failed"
    assert repo.quota_usage(10, "2026-07-19")["jobs_reserved"] == 0


def test_delivery_outbox_is_idempotent_and_ambiguous_is_fail_closed(tmp_path):
    repo = make_repo(tmp_path)
    new_job(repo)
    key = repo.reserve_delivery("j1", "worker-a", lease_seconds=60, now=100)
    assert key
    assert repo.reserve_delivery("j1", "worker-b", lease_seconds=60, now=101) is None
    repo.complete_delivery(key, status="ambiguous", error_code="crash_window")
    assert repo.reserve_delivery("j1", "worker-c", lease_seconds=60, now=200) is None
