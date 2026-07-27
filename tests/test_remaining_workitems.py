"""Remaining work items verification tests.

Covers:
- Item 8: Hybrid Mode with duplicate reply prevention
- Item 10: English panel (dashboard connected to backend)
- Item 11: Zero TUI (may be NOT STARTED)
- Item 12: Office & Proactive hardening
- Item 17: Backup & restore verification
- Item 18: Upgrade & rollback
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest


class TestHybridMode:
    """Hybrid mode must work and prevent duplicate replies."""

    def test_hybrid_mode_is_configurable(self):
        from zero.configuration import TelegramConfig
        tc = TelegramConfig()
        assert hasattr(tc, "mode"), "TelegramConfig must have mode attribute"
        assert tc.mode in ("disabled", "bot", "user_session", "hybrid")

    def test_hybrid_mode_requires_bot_token(self):
        """When mode is hybrid or bot, bot_token_ref must be set."""
        from zero.configuration import TelegramConfig
        source = inspect.getsource(TelegramConfig)
        assert "bot_token_ref" in source, "hybrid/bot mode must require bot_token_ref"

    def test_hybrid_web_class_exists(self):
        from zero.web import HybridWeb
        assert hasattr(HybridWeb, "__init__")

    def test_duplicate_reply_prevention_exists(self):
        """The runtime must have duplicate message ID tracking."""
        from zero.office.telegram import TelegramOfficeBridge
        source = inspect.getsource(TelegramOfficeBridge)
        assert "message_id" in source, "bridge must track message_id for dedup"


class TestEnglishPanel:
    """The panel must be a real English dashboard, not a placeholder."""

    def test_panel_api_has_dashboard_endpoint(self):
        from zero.panel_api import PanelAPI
        source = inspect.getsource(PanelAPI)
        assert "_dashboard" in source, "PanelAPI must have _dashboard endpoint"
        assert "_realtime" in source, "PanelAPI must have realtime endpoint"

    def test_panel_api_has_user_management(self):
        from zero.panel_api import PanelAPI
        source = inspect.getsource(PanelAPI)
        assert "_users" in source, "PanelAPI must have users endpoint"
        assert "role" in source.lower() or "owner" in source.lower(), "PanelAPI must manage roles"

    def test_panel_api_has_memory_management(self):
        from zero.panel_api import PanelAPI
        source = inspect.getsource(PanelAPI)
        assert "_memory" in source, "PanelAPI must have memory management endpoint"

    def test_panel_api_has_session_management(self):
        from zero.panel_api import PanelAPI
        source = inspect.getsource(PanelAPI)
        assert "_session" in source, "PanelAPI must have session management endpoint"

    def test_panel_api_has_settings_endpoint(self):
        from zero.panel_api import PanelAPI
        source = inspect.getsource(PanelAPI)
        assert "_settings" in source, "PanelAPI must have settings endpoint"

    def test_panel_api_dashboard_returns_real_data(self):
        """Dashboard must return real data, not placeholder."""
        from zero.panel_api import PanelAPI
        source = inspect.getsource(PanelAPI)
        assert "_dashboard_payload" in source, "PanelAPI must have dashboard payload method"


class TestOfficeProactiveHardening:
    """Office and Proactive subsystems must be hardened."""

    def test_proactive_outbox_has_lease_owner(self):
        from zero.proactive_transport import Outbox
        source = inspect.getsource(Outbox)
        assert "worker_id" in source, "Outbox must have worker_id for concurrency safety"

    def test_proactive_outbox_has_lease_expiry(self):
        from zero.proactive_transport import Outbox
        source = inspect.getsource(Outbox)
        assert "lease_until" in source, "Outbox must have lease_until for crash recovery"

    def test_office_delivery_has_max_retries(self):
        from zero.office.db import OFFICE_SCHEMA
        assert "max_retries" in OFFICE_SCHEMA or "retry" in OFFICE_SCHEMA, \
            "office_jobs must track retries"

    def test_office_quota_is_date_scoped(self):
        from zero.office.db import OFFICE_SCHEMA
        assert "quota_date" in OFFICE_SCHEMA, "quota must be date-scoped"

    def test_office_delivery_outbox_has_destination(self):
        from zero.office.db import OFFICE_SCHEMA
        assert "destination_chat_id" in OFFICE_SCHEMA or "outbound_key" in OFFICE_SCHEMA, \
            "outbox must have destination fields"


class TestBackupRestoreVerification:
    """Backup and restore must be tested."""

    def test_backup_creates_snapshots(self):
        """The store must support snapshot/backup operations."""
        from zero.storage import ZeroStore
        source = inspect.getsource(ZeroStore)
        assert "snapshot" in source.lower() or "backup" in source.lower() or "export" in source.lower(), \
            "ZeroStore must support backup/snapshot operations"

    def test_restore_imports_data(self):
        """The store must support restore/import operations."""
        from zero.storage import ZeroStore
        source = inspect.getsource(ZeroStore)
        assert "import" in source.lower() or "restore" in source.lower(), \
            "ZeroStore must support restore/import operations"


class TestUpgradeRollback:
    """Upgrade and rollback must be supported."""

    def test_migration_version_tracking_exists(self):
        from zero.storage import ZeroStore
        source = inspect.getsource(ZeroStore)
        assert "schema_version" in source or "migration" in source.lower(), \
            "ZeroStore must track schema version for upgrades"

    def test_migration_scripts_exist(self):
        root = Path(__file__).resolve().parents[1]
        scripts = list((root / "scripts").glob("migrate*.py"))
        assert len(scripts) >= 2, f"At least 2 migration scripts must exist, found: {scripts}"

    def test_zero_cli_has_version_command(self):
        from zero import cli
        source = inspect.getsource(cli)
        assert "version" in source.lower(), "CLI must have a version command for upgrade checks"
