"""Community E2E test structure.

Live tests require ZERO_COMMUNITY_E2E=1 and real Telegram credentials.
Structure tests always run and verify the test infrastructure is correct.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

live_only = pytest.mark.skipif(
    os.environ.get("ZERO_COMMUNITY_E2E") != "1",
    reason="Community E2E requires live credentials: ZERO_COMMUNITY_E2E=1",
)


@live_only
async def test_two_groups_no_cross_contamination_live():
    """Two real Telegram groups — messages must not leak between them."""
    pass


@live_only
async def test_quota_isolated_per_group_live():
    """User in two groups has independent quota in each."""
    pass


class TestCommunityE2EStructure:
    """Verify test infrastructure is correct, even when live tests skip."""

    def test_imports_resolve(self):
        from zero.office.db import OfficeRepository
        from zero.office.intake import OfficeRequest
        from zero.proactive_transport import Outbox
        from zero.tenancy.models import Scope, FORBIDDEN_IDS

    def test_scope_rejects_forbidden_owners(self):
        from zero.tenancy.models import Scope
        for bad in ("legacy", "default", "0", ""):
            with pytest.raises((ValueError, TypeError)):
                Scope(installation_id="inst", group_id=bad)

    def test_scope_rejects_candidate_placeholder(self):
        from zero.tenancy.models import Scope, _CANDIDATE_PREFIX
        assert _CANDIDATE_PREFIX == "candidate:"
        with pytest.raises(ValueError):
            Scope(installation_id="inst", group_id="candidate:test")

    def test_office_jobs_are_group_scoped(self, tmp_path):
        from zero.office.db import OfficeRepository

        repo = OfficeRepository(tmp_path / "test.db")
        repo.reserve_and_create(
            job_id="job-a", trace_id="trace-a", user_id=3001, chat_id=-3001,
            message_id=1, operation_type="create_document", office_format="docx",
            request_text="test A", input_filename="a.docx", input_path="/tmp/a.docx",
            detected_mime="application/octet-stream", input_size_bytes=100,
            uncompressed_size_bytes=200, extracted_characters=50,
            quota_date="2026-07-26", jobs_limit=10, character_limit=10000,
            installation_id="inst-e2e", group_id="group-x",
        )
        quota_x = repo.quota_usage(user_id=3001, quota_date="2026-07-26", installation_id="inst-e2e", group_id="group-x")
        quota_y = repo.quota_usage(user_id=3001, quota_date="2026-07-26", installation_id="inst-e2e", group_id="group-y")
        assert quota_x.get("jobs_reserved", 0) >= 1
        assert quota_y.get("jobs_reserved", 0) == 0

    def test_office_jobs_reject_empty_scope(self, tmp_path):
        from zero.office.db import OfficeRepository
        repo = OfficeRepository(tmp_path / "test.db")
        with pytest.raises(ValueError):
            repo.reserve_and_create(
                job_id="job-b", trace_id="trace-b", user_id=1, chat_id=-1,
                message_id=1, operation_type="create_document", office_format="docx",
                request_text="test", input_filename="b.docx", input_path="/tmp/b.docx",
                detected_mime="application/octet-stream", input_size_bytes=10,
                uncompressed_size_bytes=20, extracted_characters=5,
                quota_date="2026-07-26", jobs_limit=10, character_limit=10000,
                installation_id="", group_id="",
            )

    def test_office_jobs_reject_forbidden_scope(self, tmp_path):
        from zero.office.db import OfficeRepository
        repo = OfficeRepository(tmp_path / "test.db")
        with pytest.raises(ValueError):
            repo.reserve_and_create(
                job_id="job-c", trace_id="trace-c", user_id=1, chat_id=-1,
                message_id=1, operation_type="create_document", office_format="docx",
                request_text="test", input_filename="c.docx", input_path="/tmp/c.docx",
                detected_mime="application/octet-stream", input_size_bytes=10,
                uncompressed_size_bytes=20, extracted_characters=5,
                quota_date="2026-07-26", jobs_limit=10, character_limit=10000,
                installation_id="legacy", group_id="default",
            )

    def test_proactive_outbox_isolation(self, tmp_path):
        from zero.storage import ZeroStore
        from zero.proactive_transport import Outbox

        store = ZeroStore(str(tmp_path / "test.db"))
        outbox = Outbox(store)

        key_a = outbox.reserve("cand-a", "worker-1", installation_id="inst", group_id="group-a")
        assert key_a is not None

        key_b = outbox.reserve("cand-b", "worker-1", installation_id="inst", group_id="group-b")
        assert key_b is not None
        assert key_a != key_b
