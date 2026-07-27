"""Zero TUI verification tests.

Verifies the TUI:
- Is a real module connected to the runtime (not a stub)
- Reads live data from ZeroStore
- Displays real diagnostics
- Is wired into the CLI as `zero tui`
- --print mode works for non-TTY environments
"""
from __future__ import annotations

import inspect
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from zero.tui import render, _gather_status, _format_status
from zero.cli import build_parser, main as cli_main


class TestTUIIsReal:
    """The TUI must be a real module, not a stub."""

    def test_tui_module_exists(self):
        from zero import tui
        assert hasattr(tui, "main")
        assert hasattr(tui, "render")

    def test_tui_render_returns_content(self):
        lines = render()
        assert isinstance(lines, list)
        assert len(lines) > 5
        # Must contain the header
        assert any("Zero" in line for line in lines)
        # Must contain diagnostics section
        assert any("Diagnostics" in line for line in lines)

    def test_tui_render_with_empty_db(self, tmp_path):
        db = tmp_path / "zero.db"
        db.touch()
        lines = render(store_path=db)
        text = "\n".join(lines)
        assert "Database:" in text or "NOT FOUND" in text

    def test_tui_render_with_missing_db(self, tmp_path):
        db = tmp_path / "nonexistent.db"
        lines = render(store_path=db)
        text = "\n".join(lines)
        assert "NOT FOUND" in text

    def test_tui_read_real_data_from_store(self, tmp_path):
        """TUI reads real data from an actual SQLite database."""
        db = tmp_path / "zero.db"
        conn = sqlite3.connect(str(db))
        c = conn.cursor()
        c.execute("CREATE TABLE office_jobs (id TEXT, installation_id TEXT, group_id TEXT)")
        c.execute("INSERT INTO office_jobs VALUES ('job-1', 'inst-a', 'group-a')")
        c.execute("CREATE TABLE settings (key TEXT, value TEXT)")
        c.execute("INSERT INTO settings VALUES ('telegram_mode', 'hybrid')")
        conn.commit()
        conn.close()

        status = _gather_status(db)
        assert status["db_exists"] is True
        assert status["office_jobs_count"] == 1
        assert status["settings"]["telegram_mode"] == "hybrid"


class TestTUIWiredIntoCLI:
    """The TUI must be reachable as `zero tui`."""

    def test_cli_has_tui_subcommand(self):
        parser = build_parser()
        # Parse 'tui' subcommand
        args = parser.parse_args(["tui"])
        assert args.command == "tui"

    def test_cli_tui_runs_in_print_mode(self, tmp_path, monkeypatch):
        """`zero tui --print` outputs status and exits 0."""
        # Use a non-existent DB so it's fast
        db = tmp_path / "nonexistent.db"
        result = cli_main(["tui", "--print", "--store", str(db)])
        assert result == 0


class TestTUIDiagnostics:
    """The TUI must show real diagnostics."""

    def test_render_includes_diagnostics(self):
        lines = render()
        # Must reference at least one diagnostic check
        diag_lines = [l for l in lines if any(
            check in l for check in ("python", "sqlite", "dependency", "config", "runtime")
        )]
        assert len(diag_lines) >= 3

    def test_render_shows_config_path(self):
        lines = render()
        assert any("Config:" in line for line in lines)


class TestTUINoStubBehavior:
    """The TUI must call real functions, not print placeholders."""

    def test_tui_does_not_contain_todo_or_placeholder(self):
        from zero import tui
        source = inspect.getsource(tui)
        assert "TODO" not in source
        assert "placeholder" not in source.lower()
        assert "NotImplementedError" not in source

    def test_tui_render_calls_real_diagnostics(self):
        """Verify _format_status returns real data from the store."""
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            conn = sqlite3.connect(str(db_path))
            c = conn.cursor()
            c.execute("CREATE TABLE office_jobs (id TEXT)")
            c.execute("INSERT INTO office_jobs VALUES ('x')")
            c.execute("CREATE TABLE settings (key TEXT, value TEXT)")
            c.execute("INSERT INTO settings VALUES ('k', 'v')")
            conn.commit()
            conn.close()

            status = _gather_status(db_path)
            lines = _format_status(status)
            text = "\n".join(lines)
            assert "office_jobs" in text or "Database" in text
            assert "settings" in text
        finally:
            os.unlink(db_path)


class TestTUIPanels:
    """Each panel must render real runtime data via the `panels` registry."""

    def test_panels_registry_exists(self):
        from zero.tui import panels, PANEL_NAMES
        assert set(PANEL_NAMES) == set(panels.keys())
        assert set(panels.keys()) == {"status", "doctor", "groups", "backup", "logs", "setup"}

    def test_render_dispatches_by_name(self):
        from zero.tui import render, panels
        for name in panels:
            lines = render(panel_name=name)
            assert isinstance(lines, list)
            assert len(lines) > 0

    def test_render_unknown_name_falls_back_to_status(self):
        from zero.tui import render
        lines = render(panel_name="nonexistent")
        assert any("Zero Status" in l for l in lines)

    def test_render_appends_navigation_footer(self):
        from zero.tui import render
        lines = render(panel_name="status")
        footer = lines[-1]
        assert "[1]" in footer and "[q]" in footer and "quit" in footer

    # ------------------------------------------------------------------
    # Status panel
    # ------------------------------------------------------------------

    def test_render_status_panel(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZERO_HOME", str(tmp_path / "home"))
        db = tmp_path / "zero.db"
        conn = sqlite3.connect(str(db))
        c = conn.cursor()
        c.execute("CREATE TABLE office_jobs (id TEXT)")
        c.execute("INSERT INTO office_jobs VALUES ('job-1')")
        c.execute("CREATE TABLE settings (key TEXT, value TEXT)")
        c.execute("INSERT INTO settings VALUES ('k', 'v')")
        conn.commit()
        conn.close()

        from zero.tui import render_status
        lines = render_status(store_path=db)
        text = "\n".join(lines)
        assert "Zero Status" in text
        assert "Runtime home" in text
        assert "Config" in text
        assert "office_jobs" in text
        # status panel includes diagnostics for backward compatibility
        assert "Diagnostics" in text

    # ------------------------------------------------------------------
    # Doctor panel
    # ------------------------------------------------------------------

    def test_render_doctor_panel(self, monkeypatch):
        from zero.tui import render_doctor
        # diagnostics reads the real environment; restrict to a home
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            monkeypatch.setenv("ZERO_HOME", str(td))
            lines = render_doctor()
            text = "\n".join(lines)
            assert "Zero Doctor" in text
            assert "Checks:" in text
            # Real diagnostics checks appear
            assert any(
                chk in text for chk in
                ("python_version", "runtime_home_exists", "canonical_config", "sqlite_fts5")
            )
            # Each check line carries ✓ or ✗
            assert any(l.strip().startswith("✓") or l.strip().startswith("✗")
                       for l in lines)

    # ------------------------------------------------------------------
    # Groups panel
    # ------------------------------------------------------------------

    def test_render_groups_panel(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZERO_HOME", str(tmp_path / "home"))
        # Build a real tenancy registry with one installation + one group.
        registry_path = tmp_path / "home" / "tenancy.db"
        registry_path.parent.mkdir(parents=True, exist_ok=True)

        from zero.tenancy.registry import TenancyRegistry
        from zero.tenancy.models import GroupState, Scope, Role
        reg = TenancyRegistry(registry_path)
        reg.discover_group("inst-a", "group-1", platform="telegram",
                           platform_chat_id=12345, title="Test Group")
        scope = Scope(installation_id="inst-a", group_id="group-1")
        # Add user 1 as OWNER first, so it has MANAGE_GROUP_STATE permission.
        reg.add_member(scope, 1, Role.OWNER)
        reg.set_group_state(scope, GroupState.ACTIVE, actor_id=1)
        reg.set_setting(scope, "persona", "helpful", actor_id=1)

        from zero.tui import render_groups
        lines = render_groups()
        text = "\n".join(lines)
        assert "Zero Groups" in text
        assert "inst-a" in text
        assert "group-1" in text
        assert "active" in text
        assert "persona" in text
        assert "helpful" in text

    def test_render_groups_panel_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZERO_HOME", str(tmp_path / "home"))
        from zero.tui import render_groups
        lines = render_groups()
        text = "\n".join(lines)
        assert "Zero Groups" in text
        # No tenancy.db means graceful empty message
        assert "not found" in text or "No groups" in text

    # ------------------------------------------------------------------
    # Backup panel
    # ------------------------------------------------------------------

    def test_render_backup_panel(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZERO_HOME", str(tmp_path / "home"))
        # Create a real store DB with some content.
        db = tmp_path / "zero.db"
        conn = sqlite3.connect(str(db))
        c = conn.cursor()
        c.execute("CREATE TABLE settings (key TEXT, value TEXT)")
        c.execute("INSERT INTO settings VALUES ('k', 'v')")
        c.execute("CREATE TABLE office_jobs (id TEXT)")
        c.execute("INSERT INTO office_jobs VALUES ('job-1')")
        conn.commit()
        conn.close()

        from zero.tui import render_backup
        lines = render_backup(store_path=db)
        text = "\n".join(lines)
        assert "Zero Backup" in text
        assert "SUCCESS" in text
        assert "Backup path:" in text
        # The backup file must actually exist on disk.
        backup_line = next(l for l in lines if "Backup path:" in l)
        backup_path = Path(backup_line.split("Backup path:", 1)[1].strip())
        assert backup_path.exists()
        # The backup must contain the real data.
        chk = sqlite3.connect(str(backup_path))
        assert chk.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 1
        assert chk.execute("SELECT COUNT(*) FROM office_jobs").fetchone()[0] == 1
        chk.close()

    def test_render_backup_panel_missing_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZERO_HOME", str(tmp_path / "home"))
        from zero.tui import render_backup
        lines = render_backup(store_path=tmp_path / "missing.db")
        text = "\n".join(lines)
        assert "Zero Backup" in text
        assert "not found" in text

    # ------------------------------------------------------------------
    # Logs panel
    # ------------------------------------------------------------------

    def test_render_logs_panel(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZERO_HOME", str(tmp_path / "home"))
        logs_dir = tmp_path / "home" / "logs"
        logs_dir.mkdir(parents=True)
        log_file = logs_dir / "listener.log"
        log_lines = [f"line-{i} some log content" for i in range(60)]
        log_file.write_text("\n".join(log_lines), encoding="utf-8")

        from zero.tui import render_logs
        lines = render_logs(tail=20)
        text = "\n".join(lines)
        assert "Zero Logs" in text
        assert "listener.log" in text
        assert "line-59" in text  # last line present
        assert "line-0 " not in text  # earliest line omitted by tail
        assert "omitted" in text

    def test_render_logs_panel_no_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZERO_HOME", str(tmp_path / "home"))
        from zero.tui import render_logs
        lines = render_logs()
        text = "\n".join(lines)
        assert "Zero Logs" in text
        assert "No logs directory" in text

    # ------------------------------------------------------------------
    # Setup panel
    # ------------------------------------------------------------------

    def test_render_setup_panel(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir(parents=True)
        monkeypatch.setenv("ZERO_HOME", str(home))
        config = tmp_path / "zero.json"
        from zero.configuration import ConfigStore, CanonicalConfig, TelegramConfig
        cfg = CanonicalConfig(
            installation_id="inst-setup",
            telegram=TelegramConfig(mode="bot", bot_token_ref="bot-token-ref"),
        )
        ConfigStore(config).save(cfg)
        monkeypatch.setenv("ZERO_CANONICAL_CONFIG", str(config))

        from zero.tui import render_setup
        lines = render_setup(config_path=config)
        text = "\n".join(lines)
        assert "Zero Setup" in text
        assert "Config exists:  yes" in text
        assert "inst-setup" in text
        assert "telegram.mode:    bot" in text
        assert "configured" in text
        # generic status/existence sections present
        assert "Runtime home:" in text
        assert "canonical_config" not in text  # this is the doctor output, not setup

    def test_render_setup_panel_no_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZERO_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("ZERO_CANONICAL_CONFIG", str(tmp_path / "absent.json"))
        from zero.tui import render_setup
        lines = render_setup(config_path=tmp_path / "absent.json")
        text = "\n".join(lines)
        assert "Zero Setup" in text
        assert "Config exists:  no" in text

    # ------------------------------------------------------------------
    # CLI --panel arg
    # ------------------------------------------------------------------

    def test_cli_tui_print_panel_doctor(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZERO_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("ZERO_CANONICAL_CONFIG", str(tmp_path / "absent.json"))
        from zero.cli import main as cli_main
        rc = cli_main(["tui", "--print", "--panel", "doctor"])
        assert rc == 0

