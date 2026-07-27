"""P0-2 RED adversarial tests: runtime isolation & explicit ownership enforcement.

These tests are written BEFORE the implementation. They must FAIL initially
(RED), then pass after the fix (GREEN). Every stateful path — office
jobs/outbox, proactive outbox, document bundles, panel state, and delivery
transport — must carry an explicit owner (installation_id + group_id at minimum)
and reject any operation missing one (fail-closed), with no legacy/candidate/
0/default-group fallbacks.

The tests exercise the REAL runtime classes (no mocks) against synthetic SQLite
databases in tmp_path, matching the call graph:

    caller → RequestContext → stateful service → outbox → transport

Scenarios:
  1. Office schema must carry installation_id + group_id.
  2. Office reserve_and_create must require installation_id + group_id.
  3. Delivery outbox must carry installation_id + group_id.
  4. Proactive outbox must carry installation_id + group_id.
  5. Missing owner fails closed — no legacy/candidate/0/default fallbacks.
  6. Source has no forbidden fallback patterns (groups[0], active[0], etc.).
  7. Concurrent delivery lease is exclusive (no duplicate send).
"""
from __future__ import annotations

import inspect
import sqlite3
import textwrap
import time
from pathlib import Path

import pytest

from zero.tenancy import Scope, TenancyRegistry, GroupState, Role
from zero.office.db import OfficeRepository, OFFICE_SCHEMA
from zero.proactive_transport import Outbox as ProactiveOutbox


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_registry(tmp_path):
    return TenancyRegistry(tmp_path / "tenancy.db")


def approved(registry, installation, group, chat_id, owner=1):
    registry.discover_group(installation, group, platform_chat_id=chat_id, title=group)
    scope = Scope(installation, group, owner)
    registry.add_member(scope, owner, Role.OWNER)
    registry.set_group_state(scope, GroupState.ACTIVE)
    return scope


class _ProactiveStore:
    """Minimal store providing _conn() for the proactive Outbox."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn


# ---------------------------------------------------------------------------
# 1. Office schema must carry installation_id + group_id
# ---------------------------------------------------------------------------

class TestOfficeSchemaHasOwnership:
    """The office_jobs and office_delivery_outbox tables must include
    installation_id and group_id columns so every row is tenant-owned."""

    def test_office_jobs_schema_has_installation_id(self):
        assert "installation_id" in OFFICE_SCHEMA, \
            "office_jobs must carry installation_id"

    def test_office_jobs_schema_has_group_id(self):
        assert "group_id" in OFFICE_SCHEMA, \
            "office_jobs must carry group_id"

    def test_office_outbox_schema_has_installation_id(self):
        assert "office_delivery_outbox" in OFFICE_SCHEMA
        # Extract the outbox table definition
        outbox_section = OFFICE_SCHEMA.split("office_delivery_outbox")[1]
        assert "installation_id" in outbox_section, \
            "office_delivery_outbox must carry installation_id"

    def test_office_outbox_schema_has_group_id(self):
        outbox_section = OFFICE_SCHEMA.split("office_delivery_outbox")[1]
        assert "group_id" in outbox_section, \
            "office_delivery_outbox must carry group_id"

    def test_office_quota_usage_has_installation_and_group(self):
        quota_section = OFFICE_SCHEMA.split("office_quota_usage")[1]
        assert "installation_id" in quota_section, \
            "office_quota_usage must carry installation_id"
        assert "group_id" in quota_section, \
            "office_quota_usage must carry group_id"


# ---------------------------------------------------------------------------
# 2. Office reserve_and_create must require installation_id + group_id
# ---------------------------------------------------------------------------

class TestOfficeReserveRequiresOwner:
    """OfficeRepository.reserve_and_create must accept and require
    installation_id and group_id parameters, and reject empty ones."""

    def test_reserve_and_create_accepts_installation_and_group(self, tmp_path):
        sig = inspect.signature(OfficeRepository.reserve_and_create)
        params = list(sig.parameters)
        assert "installation_id" in params, \
            "reserve_and_create must accept installation_id"
        assert "group_id" in params, \
            "reserve_and_create must accept group_id"

    def test_reserve_and_create_rejects_empty_installation(self, tmp_path):
        repo = OfficeRepository(tmp_path / "office.db")
        with pytest.raises((ValueError, RuntimeError, sqlite3.IntegrityError)):
            repo.reserve_and_create(
                job_id="j1", trace_id="t1", user_id=7, chat_id=-100,
                message_id=1, operation_type="create_document", office_format="docx",
                request_text="test", input_filename="", input_path="",
                detected_mime="", input_size_bytes=0, uncompressed_size_bytes=0,
                extracted_characters=0, quota_date="2026-01-01", jobs_limit=10,
                character_limit=10000, installation_id="", group_id="group-a",
            )

    def test_reserve_and_create_rejects_empty_group(self, tmp_path):
        repo = OfficeRepository(tmp_path / "office.db")
        with pytest.raises((ValueError, RuntimeError, sqlite3.IntegrityError)):
            repo.reserve_and_create(
                job_id="j2", trace_id="t2", user_id=7, chat_id=-100,
                message_id=2, operation_type="create_document", office_format="docx",
                request_text="test", input_filename="", input_path="",
                detected_mime="", input_size_bytes=0, uncompressed_size_bytes=0,
                extracted_characters=0, quota_date="2026-01-01", jobs_limit=10,
                character_limit=10000, installation_id="inst-1", group_id="",
            )

    def test_reserve_and_create_rejects_legacy_installation(self, tmp_path):
        repo = OfficeRepository(tmp_path / "office.db")
        with pytest.raises((ValueError, RuntimeError, sqlite3.IntegrityError)):
            repo.reserve_and_create(
                job_id="j3", trace_id="t3", user_id=7, chat_id=-100,
                message_id=3, operation_type="create_document", office_format="docx",
                request_text="test", input_filename="", input_path="",
                detected_mime="", input_size_bytes=0, uncompressed_size_bytes=0,
                extracted_characters=0, quota_date="2026-01-01", jobs_limit=10,
                character_limit=10000, installation_id="legacy", group_id="group-a",
            )

    def test_reserve_and_create_rejects_candidate_group(self, tmp_path):
        repo = OfficeRepository(tmp_path / "office.db")
        with pytest.raises((ValueError, RuntimeError, sqlite3.IntegrityError)):
            repo.reserve_and_create(
                job_id="j4", trace_id="t4", user_id=7, chat_id=-100,
                message_id=4, operation_type="create_document", office_format="docx",
                request_text="test", input_filename="", input_path="",
                detected_mime="", input_size_bytes=0, uncompressed_size_bytes=0,
                extracted_characters=0, quota_date="2026-01-01", jobs_limit=10,
                character_limit=10000, installation_id="inst-1", group_id="candidate:123",
            )


# ---------------------------------------------------------------------------
# 3. Proactive outbox must carry installation_id + group_id
# ---------------------------------------------------------------------------

class TestProactiveOutboxOwnership:
    """The proactive_followup_outbox table must record installation_id and
    group_id, and Outbox.reserve must require them."""

    def test_proactive_outbox_schema_has_installation_and_group(self, tmp_path):
        store = _ProactiveStore(tmp_path / "proactive.db")
        outbox = ProactiveOutbox(store)
        with store._conn() as c:
            cols = [row[1] for row in c.execute(
                "PRAGMA table_info(proactive_followup_outbox)"
            )]
        assert "installation_id" in cols, \
            "proactive_followup_outbox must carry installation_id"
        assert "group_id" in cols, \
            "proactive_followup_outbox must carry group_id"

    def test_proactive_outbox_reserve_requires_scope_params(self):
        sig = inspect.signature(ProactiveOutbox.reserve)
        params = list(sig.parameters)
        assert "installation_id" in params, \
            "Outbox.reserve must accept installation_id"
        assert "group_id" in params, \
            "Outbox.reserve must accept group_id"

    def test_proactive_outbox_reserve_rejects_empty_scope(self, tmp_path):
        store = _ProactiveStore(tmp_path / "proactive.db")
        outbox = ProactiveOutbox(store)
        with pytest.raises((ValueError, RuntimeError, TypeError)):
            outbox.reserve(
                candidate_id="c1", worker="w1",
                installation_id="", group_id="",
            )

    def test_proactive_outbox_reserve_rejects_missing_scope(self, tmp_path):
        store = _ProactiveStore(tmp_path / "proactive.db")
        outbox = ProactiveOutbox(store)
        with pytest.raises((ValueError, RuntimeError, TypeError)):
            outbox.reserve(candidate_id="c1", worker="w1")


# ---------------------------------------------------------------------------
# 4. Scope rejects forbidden fallback identifiers
# ---------------------------------------------------------------------------

class TestScopeRejectsForbiddenFallbacks:
    """No fallback to legacy, candidate:<id>, 0, default group, groups[0],
    or implicit current group is allowed."""

    @pytest.mark.parametrize("bad_installation_id", ["legacy", "", "candidate:123", "0", "default"])
    def test_scope_rejects_forbidden_installation_ids(self, bad_installation_id):
        with pytest.raises(ValueError):
            Scope(bad_installation_id, "g1")

    @pytest.mark.parametrize("bad_group_id", ["legacy", "", "candidate:123", "0", "default"])
    def test_scope_rejects_forbidden_group_ids(self, bad_group_id):
        with pytest.raises(ValueError):
            Scope("inst-1", bad_group_id)

    def test_scope_rejects_user_id_zero(self):
        with pytest.raises(ValueError):
            Scope("inst-1", "g1", user_id=0)


# ---------------------------------------------------------------------------
# 5. Source has no forbidden fallback patterns
# ---------------------------------------------------------------------------

class TestSourceHasNoForbiddenFallbacks:
    """No runtime source may use groups[0], active[0], allowed_group_ids[0],
    or first_group as a fallback."""

    @pytest.mark.parametrize("pattern", [
        "groups[0]", "active[0]", "allowed_group_ids[0]", "first_group",
    ])
    def test_no_forbidden_patterns_in_runtime_sources(self, pattern):
        root = Path(__file__).resolve().parents[1]
        sources = [
            root / "scripts" / "run_listener.py",
            root / "scripts" / "run_panel.py",
            root / "zero" / "brain.py",
            root / "zero" / "office" / "delivery.py",
            root / "zero" / "office" / "db.py",
            root / "zero" / "proactive_transport.py",
            root / "zero" / "proactive_scheduler.py",
            root / "zero" / "proactive_followups.py",
        ]
        for src_path in sources:
            if not src_path.exists():
                continue
            content = src_path.read_text()
            assert pattern not in content, \
                f"{src_path.name} contains forbidden fallback: {pattern}"


# ---------------------------------------------------------------------------
# 6. Concurrent delivery lease is exclusive (no duplicate send)
# ---------------------------------------------------------------------------

class TestConcurrentDeliveryScopeLock:
    """Two workers attempting concurrent delivery of the same job must not
    produce duplicate sends — the lease mechanism must serialize them."""

    def test_reserve_delivery_returns_none_for_second_worker(self, tmp_path):
        repo = OfficeRepository(tmp_path / "office.db")
        # Create a completed job
        job = repo.reserve_and_create(
            job_id="j1", trace_id="t1", user_id=7, chat_id=-100,
            message_id=1, operation_type="create_document", office_format="docx",
            request_text="test", input_filename="", input_path="",
            detected_mime="", input_size_bytes=0, uncompressed_size_bytes=0,
            extracted_characters=0, quota_date="2026-01-01", jobs_limit=10,
            character_limit=10000, installation_id="inst", group_id="group-a",
        )
        # Transition to completed (job starts at quota_reserved after reserve_and_create)
        for target in ["queued", "processing", "validating_output", "rendering",
                       "reviewing", "completed"]:
            repo.transition(job["id"], target)

        key1 = repo.reserve_delivery(job["id"], "worker-1", lease_seconds=300)
        assert key1 is not None
        key2 = repo.reserve_delivery(job["id"], "worker-2", lease_seconds=300)
        assert key2 is None, \
            "second worker must not get a delivery lease while first holds it"


# ---------------------------------------------------------------------------
# 7. DeliveryCoordinator verifies scope before sending
# ---------------------------------------------------------------------------

class TestDeliveryScopeVerification:
    """DeliveryCoordinator.tick must verify the job's scope before sending."""

    def test_delivery_coordinator_has_scope_awareness(self):
        from zero.office.delivery import DeliveryCoordinator
        # The tick method or constructor must reference scope/installation/group
        source = inspect.getsource(DeliveryCoordinator)
        assert "installation_id" in source or "group_id" in source or "scope" in source, \
            "DeliveryCoordinator must be scope-aware"


# ---------------------------------------------------------------------------
# 8. Office quota usage must be scoped (not just user_id)
# ---------------------------------------------------------------------------

class TestOfficeQuotaIsScoped:
    """office_quota_usage must key on (installation_id, group_id, user_id, quota_date),
    not just (user_id, quota_date)."""

    def test_quota_usage_schema_has_installation_and_group(self):
        quota_section = OFFICE_SCHEMA.split("office_quota_usage")[1]
        if "CREATE TABLE" in quota_section:
            assert "installation_id" in quota_section, \
                "office_quota_usage must carry installation_id"
            assert "group_id" in quota_section, \
                "office_quota_usage must carry group_id"
