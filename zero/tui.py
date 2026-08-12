"""Zero TUI — interactive terminal administration interface.

A lightweight curses-based TUI with multiple panels, each reading real state
from the running installation's ZeroStore, ConfigStore, TenancyRegistry and
log files. Not a stub: every panel displays live data.

Panels:
    status   — live DB counts (office_jobs, outbox, memory tables), config path, runtime home
    doctor   — run diagnostics() from cli.py and show results
    groups   — list all groups from TenancyRegistry, show their settings
    backup   — snapshot/export the ZeroStore DB, show backup file path
    logs     — show last N lines from ~/.zero/logs/*.log
    setup    — show setup state and launch the safe setup wizard
    chat     — local conversational session backed by ZeroBrain

Usage:
    python -m zero tui                  # interactive
    python -m zero tui --print          # default panel, non-interactive
    python -m zero tui --print --panel doctor
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import warnings
from pathlib import Path
from dataclasses import dataclass
from typing import Callable, Literal, cast

from .paths import zero_home, panel_state_path
from .configuration import canonical_config_path, ConfigStore, ensure_private_directory, restrict_private_file
from .runtime_config import runtime_config_path
from .chat import ChatRuntime, build_chat_runtime
from .tui_contract import PANEL_NAMES


# Navigation footer shown at the bottom of every panel.
_NAV_FOOTER = "[1] status  [2] doctor  [3] groups  [4] backup  [5] logs  [6] setup  [7] chat  [8] sessions  [←/→] switch  [q] quit"


def _safe_int(v, default=0):
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _header(title: str) -> list[str]:
    return [
        "╔══════════════════════════════════════╗",
        f"║  {title:<34}║",
        "╚══════════════════════════════════════╝",
    ]


def _gather_status(store_path: Path) -> dict:
    """Read real state from the ZeroStore database."""
    status: dict = {}
    if not store_path.exists():
        status["db_exists"] = False
        return status
    status["db_exists"] = True
    status["db_size"] = store_path.stat().st_size
    try:
        conn = sqlite3.connect(str(store_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        for table in ("office_jobs", "office_delivery_outbox", "proactive_followup_outbox", "office_quota_usage"):
            try:
                c.execute(f"SELECT COUNT(*) FROM {table}")
                status[f"{table}_count"] = _safe_int(c.fetchone()[0])
            except sqlite3.Error:
                status[f"{table}_count"] = "table missing"

        for table in ("direct_memory", "semantic_memory", "medium_term_memory"):
            try:
                c.execute(f"SELECT COUNT(*) FROM {table}")
                status[f"{table}_count"] = _safe_int(c.fetchone()[0])
            except sqlite3.Error:
                status[f"{table}_count"] = "table missing"

        try:
            c.execute("SELECT key, value FROM settings")
            status["settings"] = {row[0]: row[1] for row in c.fetchall()}
        except sqlite3.Error:
            status["settings"] = {}

        conn.close()
    except sqlite3.Error as exc:
        status["db_error"] = str(exc)
    return status


def _format_status(status: dict) -> list[str]:
    """Format status dict into display lines."""
    lines: list[str] = []
    if not status.get("db_exists"):
        lines.append("  Database: NOT FOUND")
        return lines
    lines.append(f"  Database: {status.get('db_size', 0)} bytes")

    for table in ("office_jobs", "office_delivery_outbox", "proactive_followup_outbox", "office_quota_usage"):
        key = f"{table}_count"
        val = status.get(key, "?")
        lines.append(f"  {table}: {val}")

    for table in ("direct_memory", "semantic_memory", "medium_term_memory"):
        key = f"{table}_count"
        val = status.get(key, "?")
        lines.append(f"  {table}: {val}")

    settings = status.get("settings", {})
    if settings:
        lines.append(f"  settings: {len(settings)} keys")
    if "db_error" in status:
        lines.append(f"  ERROR: {status['db_error']}")

    return lines


# ---------------------------------------------------------------------------
# Panel render functions
#
# Each returns list[str] — the display lines for that panel.
# ---------------------------------------------------------------------------


def render_status(
    store_path: Path | None = None,
    config_path: Path | None = None,
    runtime_path: Path | None = None,
) -> list[str]:
    """Status panel — live DB counts, config path, runtime home, diagnostics."""
    if store_path is None:
        store_path = zero_home() / "zero.db"
    if config_path is None:
        config_path = canonical_config_path()

    lines = _header("Zero Status")
    lines.append("")
    home = zero_home()
    lines.append(f"  Runtime home: {home}")
    lines.append(f"  Config: {config_path}")
    lines.append(f"  Config exists: {'yes' if config_path.exists() else 'no'}")
    lines.append(f"  Store path:    {store_path}")
    lines.append("")
    lines.append("── Database ──")
    status = _gather_status(store_path)
    lines.extend(_format_status(status))
    lines.append("")
    lines.append("── Diagnostics ──")
    from .cli import diagnostics

    for check in diagnostics(config_path=config_path, runtime_path=runtime_path):
        mark = "✓" if check["ok"] else "✗"
        lines.append(f"  {mark} {check['check']}: {check['detail']}")
    lines.append("")
    lines.append(f"  Refresh: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return lines


def render_doctor(
    store_path: Path | None = None,
    config_path: Path | None = None,
    runtime_path: Path | None = None,
) -> list[str]:
    """Doctor panel — run diagnostics() and show results."""
    from .cli import diagnostics

    lines = _header("Zero Doctor")
    lines.append("")
    checks = diagnostics(config_path=config_path, runtime_path=runtime_path)
    passed = sum(1 for c in checks if c["ok"])
    failed = len(checks) - passed
    lines.append(f"  Checks: {len(checks)} total — {passed} passed, {failed} failed")
    lines.append("")
    lines.append("── Diagnostics ──")
    for check in checks:
        mark = "✓" if check["ok"] else "✗"
        lines.append(f"  {mark} {check['check']}: {check['detail']}")
    lines.append("")
    lines.append(f"  Run `zero doctor` for JSON output.  {time.strftime('%H:%M:%S')}")
    return lines


def render_sessions(store_path: Path | None = None, config_path: Path | None = None) -> list[str]:
    """Sessions panel backed by SessionRegistry without exposing session paths."""
    from .sessions import SessionRegistry

    lines = _header("Zero Sessions")
    lines.append("")
    root = zero_home() / "sessions"
    if not (root / "registry.db").is_file():
        lines.extend(("  No managed sessions registered.", ""))
        return lines
    try:
        records = SessionRegistry(root).list()
    except (OSError, sqlite3.Error, ValueError) as exc:
        lines.extend((f"  Session registry unavailable ({type(exc).__name__}).", ""))
        return lines
    if not records:
        lines.extend(("  No managed sessions registered.", ""))
        return lines
    for record in records:
        active = "active" if record.active else "inactive"
        ownership = "managed" if record.managed else "external"
        account = f"@{record.username}" if record.username else (str(record.user_id) if record.user_id is not None else "unbound")
        lines.append(f"  {record.session_id} — {record.label or '(unlabelled)'}")
        lines.append(f"    state: {record.state} ({active}, {ownership})")
        lines.append(f"    account: {account}")
        lines.append("")
    return lines


def render_groups(store_path: Path | None = None, config_path: Path | None = None) -> list[str]:
    """Groups panel — list all groups from TenancyRegistry, show settings."""
    from .tenancy.registry import TenancyRegistry

    lines = _header("Zero Groups")
    lines.append("")

    # The tenancy registry lives next to the ZeroStore as tenancy.db.
    home = zero_home()
    registry_path = home / "tenancy.db"
    lines.append(f"  Registry: {registry_path}")
    lines.append(f"  Registry exists: {'yes' if registry_path.exists() else 'no'}")
    lines.append("")

    if not registry_path.exists():
        lines.append("  No groups registered (tenancy.db not found).")
        lines.append("")
        return lines

    try:
        registry = TenancyRegistry(registry_path)
        installation_ids = registry.installations()
        lines.append(f"  Installations: {len(installation_ids)}")
        lines.append("")

        any_groups = False
        for inst_id in installation_ids:
            groups = registry.groups(inst_id)
            if not groups:
                continue
            any_groups = True
            lines.append(f"── Installation: {inst_id} ({len(groups)} groups) ──")
            for g in groups:
                serving = "serving" if g.serving else "idle"
                lines.append(f"  {g.group_id}")
                lines.append(f"    platform: {g.platform}")
                lines.append(f"    state:    {g.state.value} ({serving})")
                if g.platform_chat_id is not None:
                    lines.append(f"    chat_id:  {g.platform_chat_id}")
                if g.title:
                    lines.append(f"    title:    {g.title}")
                # Read per-group settings.
                try:
                    from .tenancy.models import Scope

                    scope = Scope(installation_id=inst_id, group_id=g.group_id)
                    settings = registry.settings(scope)
                    if settings:
                        lines.append("    settings:")
                        for key, value in sorted(settings.items()):
                            lines.append(f"      {key}: {json.dumps(value, ensure_ascii=False)}")
                    else:
                        lines.append("    settings: (none)")
                    limits = registry.quotas(scope, "human_replies")
                    if limits:
                        lines.append("    human replies:")
                        for period in ("hour", "day", "week", "month"):
                            if period in limits:
                                lines.append(f"      {period}: {limits[period]}")
                    else:
                        lines.append("    human replies: unlimited")
                except Exception as exc:
                    lines.append(f"    settings: error reading — {type(exc).__name__}")
                lines.append("")
        if not any_groups:
            lines.append("  No groups discovered in any installation.")
            lines.append("")
    except sqlite3.Error as exc:
        lines.append(f"  Registry error: {exc}")
        lines.append("")
    return lines


def render_backup(store_path: Path | None = None, config_path: Path | None = None, *, create: bool = False) -> list[str]:
    """Backup panel — report the latest snapshot and create only on an explicit action.

    Rendering is side-effect free. Interactive `r`/`refresh` invokes this with
    ``create=True`` after the user explicitly requests a snapshot.
    """
    if store_path is None:
        store_path = zero_home() / "zero.db"

    lines = _header("Zero Backup")
    lines.append("")
    lines.append(f"  Source: {store_path}")
    lines.append("")

    if not store_path.exists():
        lines.append("  Source database not found — nothing to back up.")
        lines.append("")
        return lines

    home = zero_home()
    backup_dir = home / "backups"
    if not create:
        latest = sorted(backup_dir.glob("zero-*.db"), reverse=True) if backup_dir.is_dir() else []
        if latest:
            latest_path = latest[0]
            lines.append(f"  Latest backup: {latest_path}")
            lines.append(f"  Backup size:   {latest_path.stat().st_size} bytes")
        else:
            lines.append("  No backup has been created yet.")
        lines.append("")
        return lines

    # Determine backup destination: <home>/backups/zero-<timestamp>.db
    ensure_private_directory(backup_dir, repair_existing=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"zero-{stamp}.db"
    # Avoid replacing a snapshot when two explicit requests land in one second.
    suffix = 1
    while backup_path.exists():
        backup_path = backup_dir / f"zero-{stamp}-{suffix}.db"
        suffix += 1

    # Write to a private temporary file first. A failed online SQLite backup must
    # not leave a partial database that can be mistaken for a recoverable snapshot.
    temporary: str | None = None
    src: sqlite3.Connection | None = None
    dst: sqlite3.Connection | None = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".zero-backup-", suffix=".db", dir=backup_dir)
        os.close(fd)
        restrict_private_file(temporary)
        try:
            src = sqlite3.connect(str(store_path))
            dst = sqlite3.connect(temporary)
            src.backup(dst)
        finally:
            if dst is not None:
                dst.close()
            if src is not None:
                src.close()
        os.replace(temporary, backup_path)
        temporary = None
        restrict_private_file(backup_path)
        size = backup_path.stat().st_size
        lines.append("  Backup: SUCCESS")
        lines.append(f"  Backup path: {backup_path}")
        lines.append(f"  Backup size: {size} bytes")
    except (OSError, sqlite3.Error):
        lines.append("  Backup: FAILED")
        lines.append("  Error: local snapshot creation failed.")
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass
    lines.append("")
    lines.append(f"  Snapshot taken: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return lines


def render_logs(store_path: Path | None = None, config_path: Path | None = None, tail: int = 50) -> list[str]:
    """Logs panel — show last N lines from ~/.zero/logs/*.log."""
    home = zero_home()
    logs_dir = home / "logs"

    lines = _header("Zero Logs")
    lines.append("")
    lines.append(f"  Logs dir: {logs_dir}")
    lines.append(f"  Logs dir exists: {'yes' if logs_dir.is_dir() else 'no'}")
    lines.append("")

    if not logs_dir.is_dir():
        lines.append("  No logs directory found.")
        lines.append("")
        return lines

    log_files = sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not log_files:
        lines.append("  No .log files found in logs directory.")
        lines.append("")
        return lines

    lines.append(f"  Log files: {len(log_files)}")
    lines.append("")

    for log_file in log_files[:5]:
        lines.append(f"── {log_file.name} ({log_file.stat().st_size} bytes) ──")
        try:
            content = log_file.read_text(encoding="utf-8", errors="replace")
            log_lines = content.splitlines()
            tail_count = max(1, min(int(tail), 500))
            shown = log_lines[-tail_count:]
            if len(log_lines) > tail_count:
                lines.append(f"  ... ({len(log_lines) - tail_count} earlier lines omitted)")
            for log_line in shown:
                lines.append(f"  {log_line}")
        except OSError as exc:
            lines.append(f"  (read error: {exc})")
        lines.append("")
    return lines


def render_setup(store_path: Path | None = None, config_path: Path | None = None) -> list[str]:
    """Setup panel — show setup state (config exists, secrets configured)."""
    if config_path is None:
        config_path = canonical_config_path()

    lines = _header("Zero Setup")
    lines.append("")
    home = zero_home()
    lines.append(f"  Runtime home:   {home}")
    lines.append(f"  Config path:    {config_path}")
    lines.append(f"  Config exists:  {'yes' if config_path.exists() else 'no'}")
    lines.append("")

    # Config content / validity
    config_loaded = None
    if config_path.exists():
        lines.append("── Canonical Config ──")
        try:
            cfg = ConfigStore(config_path).load()
            config_loaded = cfg
            lines.append(f"  schema_version:  {cfg.schema_version}")
            lines.append(f"  installation_id: {cfg.installation_id}")
            tg = cfg.telegram
            lines.append(f"  telegram.mode:    {tg.mode}")
            # Whether secrets are configured depends on the telegram transport mode.
            secrets_configured = False
            if tg.mode == "disabled":
                lines.append("  telegram secrets: not required (mode=disabled)")
            else:
                has_bot = bool(tg.bot_token_ref)
                has_user = bool(tg.api_id and tg.api_hash_ref and tg.session_ref)
                if tg.mode == "bot":
                    secrets_configured = has_bot
                    lines.append(f"  telegram bot_token_ref: {'configured' if has_bot else 'MISSING'}")
                elif tg.mode == "user_session":
                    secrets_configured = has_user
                    lines.append(f"  telegram session: {'configured' if has_user else 'MISSING (need api_id+api_hash_ref+session_ref)'}")
                elif tg.mode == "hybrid":
                    secrets_configured = has_bot and has_user
                    lines.append(f"  telegram bot_token_ref: {'configured' if has_bot else 'MISSING'}")
                    lines.append(f"  telegram session: {'configured' if has_user else 'MISSING'}")
                lines.append(f"  telegram secrets: {'configured' if secrets_configured else 'INCOMPLETE'}")
        except (OSError, ValueError):
            lines.append("  Config load error: configuration validation failed; check local files.")
        lines.append("")

    # Panel-store setup state, if present.
    panel_db = panel_state_path()
    lines.append("── Panel Setup State ──")
    lines.append(f"  Panel DB: {panel_db}")
    lines.append(f"  Panel DB exists: {'yes' if panel_db.exists() else 'no'}")
    if panel_db.exists():
        try:
            from .panel_store import PanelStore

            store = PanelStore(panel_db)
            state = store.get_setup_state()
            lines.append(f"  current_step: {state['current_step']}")
            lines.append(f"  completed:    {state['completed']}")
            lines.append(f"  updated_at:   {state['updated_at']}")
            data = state.get("data", {})
            if data:
                lines.append(f"  data keys:    {', '.join(sorted(data.keys()))}")
            else:
                lines.append("  data:         (empty)")
        except (OSError, sqlite3.Error, ValueError):
            lines.append("  Panel state error: local state validation failed.")
    else:
        lines.append("  (panel.db not created yet — run setup wizard)")
    lines.append("")
    return lines


def render_chat(
    runtime: ChatRuntime | None = None,
    store_path: Path | None = None,
    config_path: Path | None = None,
) -> list[str]:
    """Conversation panel; runtime is injected by the interactive loop."""
    lines = _header("Zero Chat")
    lines.append("")
    if runtime is None:
        lines.extend(
            [
                "  Chat runtime is initialized when you submit the first prompt.",
                "  Enter: compose prompt    /help: local commands",
                "  Provider credentials remain in the protected runtime config.",
                "",
            ]
        )
        return lines
    session = runtime.state.active
    lines.append(f"  Session: {session.session_id}  {session.title}")
    lines.append(f"  Sessions: {len(runtime.state.sessions)}")
    lines.append("")
    if not session.messages:
        lines.append("  No messages yet. Press Enter to send a prompt.")
    for item in session.messages:
        prefix = {"user": "You", "assistant": "Zero", "system": "System"}.get(item.role, item.role)
        lines.append(f"  {prefix}: {item.text}")
    lines.append("")
    lines.append("  Enter prompt  /help  /new  /clear  /sessions  /use <id>  /quit")
    return lines


@dataclass
class TUIController:
    """Pure navigation state for the curses frontend.

    Keeping navigation separate from curses makes resize, panel switching and
    scrolling deterministic and directly testable without a real terminal.
    """

    panel_index: int = 0
    scroll_offset: int = 0
    status_message: str = ""

    def select(self, panel: str) -> None:
        if panel in PANEL_NAMES:
            self.panel_index = PANEL_NAMES.index(panel)
            self.scroll_offset = 0

    @property
    def panel(self) -> str:
        return PANEL_NAMES[self.panel_index % len(PANEL_NAMES)]

    def next_panel(self) -> None:
        self.panel_index = (self.panel_index + 1) % len(PANEL_NAMES)
        self.scroll_offset = 0

    def previous_panel(self) -> None:
        self.panel_index = (self.panel_index - 1) % len(PANEL_NAMES)
        self.scroll_offset = 0

    def scroll(self, delta: int, content_lines: int, viewport_lines: int) -> None:
        maximum = max(0, content_lines - max(1, viewport_lines))
        self.scroll_offset = max(0, min(maximum, self.scroll_offset + delta))

    def reset_scroll(self) -> None:
        self.scroll_offset = 0


def _setup_state(config_path: Path, panel_path: Path) -> dict[str, object]:
    """Return redacted, side-effect-free setup state for rendering."""
    result: dict[str, object] = {
        "config_exists": config_path.exists(),
        "config_valid": False,
        "installation_id": None,
        "telegram_mode": None,
        "telegram_configured": False,
        "panel_exists": panel_path.exists(),
        "current_step": None,
        "completed": False,
        "error": None,
    }
    if config_path.exists():
        try:
            cfg = ConfigStore(config_path).load()
            result.update(
                config_valid=True,
                installation_id=cfg.installation_id,
                telegram_mode=cfg.telegram.mode,
                telegram_configured=(
                    cfg.telegram.mode == "disabled"
                    or (cfg.telegram.mode == "bot" and bool(cfg.telegram.bot_token_ref))
                    or (cfg.telegram.mode == "user_session" and bool(cfg.telegram.api_id and cfg.telegram.api_hash_ref and cfg.telegram.session_ref))
                    or (cfg.telegram.mode == "hybrid" and bool(cfg.telegram.bot_token_ref and cfg.telegram.api_id and cfg.telegram.api_hash_ref and cfg.telegram.session_ref))
                ),
            )
        except (OSError, ValueError):
            result["error"] = "canonical configuration validation failed"
    if panel_path.exists():
        try:
            from .panel_store import PanelStore

            state = PanelStore(panel_path).get_setup_state()
            result.update(
                current_step=state.get("current_step"),
                completed=state.get("completed", False),
            )
        except (OSError, sqlite3.Error, ValueError):
            result["error"] = "panel state validation failed"
    return result


# ---------------------------------------------------------------------------
# Panel registry + dispatch
# ---------------------------------------------------------------------------

# Maps panel name -> render function. All panels share the same signature
# so render() can call any of them uniformly.
panels: dict[str, Callable[..., list[str]]] = {
    "status": render_status,
    "doctor": render_doctor,
    "groups": render_groups,
    "backup": render_backup,
    "logs": render_logs,
    "setup": render_setup,
    "chat": render_chat,
    "sessions": render_sessions,
}


def render(
    panel_name: str = "status",
    store_path: Path | None = None,
    config_path: Path | None = None,
    runtime_path: Path | None = None,
) -> list[str]:
    """Non-interactive render — dispatch to the named panel.

    Returns the display lines for that panel (with navigation footer).
    Used by tests and by `zero tui --print` for non-TTY output.
    """
    func = panels.get(panel_name, render_status)
    if func in {render_status, render_doctor}:
        lines = func(store_path=store_path, config_path=config_path, runtime_path=runtime_path)
    else:
        lines = func(store_path=store_path, config_path=config_path)
    lines.append("")
    lines.append(_NAV_FOOTER)
    return lines


def _curses_prompt(stdscr, prompt: str, *, default: str = "", secret: bool = False) -> str:
    """Read one bounded line while preserving curses terminal ownership."""
    import curses

    height, width = stdscr.getmaxyx()
    row = max(0, height - 2)
    stdscr.move(row, 0)
    stdscr.clrtoeol()
    label = f"{prompt} [{default}]: " if default else f"{prompt}: "
    stdscr.addnstr(row, 0, label, max(1, width - 1))
    if secret:
        curses.noecho()
    else:
        curses.echo()
    try:
        raw = stdscr.getstr(row, min(len(label), max(0, width - 1)), 256)
    finally:
        curses.noecho()
    value = raw.decode("utf-8", errors="replace").strip()
    return value or default


def _curses_message(stdscr, lines: list[str], *, wait: bool = True) -> None:
    import curses

    stdscr.erase()
    height, width = stdscr.getmaxyx()
    for index, line in enumerate(lines[: max(1, height - 2)]):
        try:
            stdscr.addnstr(index, 0, line, max(1, width - 1))
        except curses.error:
            pass
    if wait:
        footer = "Press any key to continue"
        try:
            stdscr.addnstr(max(0, height - 1), 0, footer, max(1, width - 1))
        except curses.error:
            pass
        stdscr.refresh()
        stdscr.getch()


def _commit_setup_with_panel_state(
    service,
    *,
    panel_path: Path,
    installation_id: str,
    mode: Literal["disabled", "bot", "user_session", "hybrid"],
    bot_token_ref: str | None,
    api_id: int | None,
    api_hash_ref: str | None,
    session_ref: str | None,
):
    """Commit canonical config and panel wizard progress with compensation on failure."""
    from .panel_store import PanelStore

    candidate = service.prepare_setup(
        installation_id=installation_id,
        mode=mode,
        bot_token_ref=bot_token_ref,
        api_id=api_id,
        api_hash_ref=api_hash_ref,
        session_ref=session_ref,
    )
    if candidate is None:
        return None

    original_config = service._config
    config_snapshot = service.store.snapshot()
    panel_existed = panel_path.exists()
    panel: PanelStore | None = None
    panel_snapshot: tuple[str, int, str, int] | None = None
    try:
        panel = PanelStore(panel_path)
        panel_snapshot = panel.snapshot_setup_state()
        state = service.commit_setup(candidate)
        panel.save_setup_step("profile", {"installation_id": installation_id})
        panel.save_setup_step(
            "telegram",
            {
                "mode": mode,
                "bot_token_ref": bot_token_ref,
                "api_id": api_id,
                "api_hash_ref": api_hash_ref,
                "session_ref": session_ref,
            },
        )
        panel.save_setup_step("validation", {"config_valid": True})
        panel.save_setup_step("start", {"ready": True})
        return state
    except BaseException:
        try:
            service.store.restore(config_snapshot)
            service._config = original_config
            if panel_existed and panel is not None and panel_snapshot is not None:
                panel.restore_setup_state(panel_snapshot)
            elif not panel_existed:
                PanelStore.remove_database_files(panel_path)
        except BaseException as rollback_error:
            raise OSError("setup transaction rollback failed") from rollback_error
        raise


def run_setup_wizard(stdscr, *, config_path: Path, panel_path: Path) -> bool:
    """Run the local setup flow using the canonical SetupService.

    The wizard intentionally accepts only symbolic secret references. Actual
    tokens, API hashes, and session material stay in the protected secret
    stores described by INSTALLATION.md and are never echoed or persisted by
    this TUI.
    """
    from .configuration import ConfigStore, SetupService, is_safe_installation_id

    try:
        store = ConfigStore(config_path)
        service = SetupService(store, installation_id="local")
        current = service._config
        installation_id = _curses_prompt(
            stdscr,
            "Installation id",
            default=current.installation_id,
            secret=True,
        )
        installation_id = installation_id.strip()
        if not is_safe_installation_id(installation_id):
            _curses_message(stdscr, ["Setup failed", "Installation id is invalid."])
            return False

        mode = _curses_prompt(
            stdscr,
            "Telegram mode (disabled/bot/user_session/hybrid)",
            default=current.telegram.mode,
        ).lower()
        if mode not in {"disabled", "bot", "user_session", "hybrid"}:
            _curses_message(stdscr, ["Setup failed", "Unknown Telegram mode."])
            return False
        mode = cast(Literal["disabled", "bot", "user_session", "hybrid"], mode)

        bot_ref = None
        api_id = None
        api_hash_ref = None
        session_ref = None
        if mode in {"bot", "hybrid"}:
            bot_ref = _curses_prompt(
                stdscr,
                "Bot token reference",
                default=current.telegram.bot_token_ref or "telegram.bot_token",
                secret=True,
            )
        if mode in {"user_session", "hybrid"}:
            api_id_text = _curses_prompt(stdscr, "Telegram API id", default=str(current.telegram.api_id or ""))
            try:
                api_id = int(api_id_text)
            except ValueError:
                _curses_message(stdscr, ["Setup failed", "API id must be a positive integer."])
                return False
            if api_id <= 0:
                _curses_message(stdscr, ["Setup failed", "API id must be a positive integer."])
                return False
            api_hash_ref = _curses_prompt(
                stdscr,
                "API hash reference",
                default=current.telegram.api_hash_ref or "telegram.api_hash",
                secret=True,
            )
            session_ref = _curses_prompt(
                stdscr,
                "Session reference",
                default=current.telegram.session_ref or "telegram.session",
                secret=True,
            )

        state = _commit_setup_with_panel_state(
            service,
            panel_path=panel_path,
            installation_id=installation_id,
            mode=mode,
            bot_token_ref=bot_ref,
            api_id=api_id,
            api_hash_ref=api_hash_ref,
            session_ref=session_ref,
        )
        if state is None:
            _curses_message(stdscr, ["Setup failed", "Telegram settings did not pass validation."])
            return False
        _curses_message(
            stdscr,
            [
                "Setup complete",
                f"Installation: {state.config.installation_id}",
                f"Telegram mode: {state.config.telegram.mode}",
                "Only symbolic references were stored; no credentials were displayed.",
                "Configure the legacy runtime YAML and protected secret files, then run `zero doctor` before starting listener/panel processes.",
            ],
        )
        return True
    except (EOFError, KeyboardInterrupt):
        _curses_message(stdscr, ["Setup cancelled."])
        return False
    except (OSError, ValueError, sqlite3.Error):
        _curses_message(
            stdscr,
            ["Setup failed", "Configuration or storage validation failed; check local files and retry."],
        )
        return False


def _hidden_console_input(prompt: str) -> str:
    """Read a reference without permitting ``getpass`` to fall back to echo."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass(prompt)
    except getpass.GetPassWarning as exc:
        raise OSError("secure terminal input is unavailable") from exc


def run_console_setup_wizard(
    *,
    config_path: Path,
    panel_path: Path,
    input_fn: Callable[[str], str] | None = None,
    output_fn: Callable[[str], None] | None = None,
) -> bool:
    """Run canonical setup without curses while keeping secret values out of it."""
    from .configuration import ConfigStore, SetupService, is_safe_installation_id

    read = input if input_fn is None else input_fn
    write = print if output_fn is None else output_fn

    def prompt(label: str, *, default: str = "", secret: bool = False) -> str:
        suffix = f" [{default}]" if default else ""
        reader = _hidden_console_input if secret and input_fn is None else read
        return reader(f"{label}{suffix}: ").strip() or default

    try:
        store = ConfigStore(config_path)
        service = SetupService(store, installation_id="local")
        current = service._config

        installation_id = prompt("Installation id", default=current.installation_id, secret=True)
        installation_id = installation_id.strip()
        if not is_safe_installation_id(installation_id):
            write("Setup failed: installation id is invalid.")
            return False

        mode = prompt(
            "Telegram mode (disabled/bot/user_session/hybrid)",
            default=current.telegram.mode,
        ).casefold()
        if mode not in {"disabled", "bot", "user_session", "hybrid"}:
            write("Setup failed: unknown Telegram mode.")
            return False
        mode = cast(Literal["disabled", "bot", "user_session", "hybrid"], mode)

        bot_ref = None
        api_id = None
        api_hash_ref = None
        session_ref = None
        if mode in {"bot", "hybrid"}:
            bot_ref = prompt(
                "Bot token reference",
                default=current.telegram.bot_token_ref or "telegram.bot_token",
                secret=True,
            )
        if mode in {"user_session", "hybrid"}:
            api_id_text = prompt(
                "Telegram API id",
                default=str(current.telegram.api_id or ""),
            )
            try:
                api_id = int(api_id_text)
            except ValueError:
                write("Setup failed: API id must be a positive integer.")
                return False
            if api_id <= 0:
                write("Setup failed: API id must be a positive integer.")
                return False
            api_hash_ref = prompt(
                "API hash reference",
                default=current.telegram.api_hash_ref or "telegram.api_hash",
                secret=True,
            )
            session_ref = prompt(
                "Session reference",
                default=current.telegram.session_ref or "telegram.session",
                secret=True,
            )

        state = _commit_setup_with_panel_state(
            service,
            panel_path=panel_path,
            installation_id=installation_id,
            mode=mode,
            bot_token_ref=bot_ref,
            api_id=api_id,
            api_hash_ref=api_hash_ref,
            session_ref=session_ref,
        )
        if state is None:
            write("Setup failed: Telegram settings did not pass validation.")
            return False
        write("Setup complete.")
        write(f"Installation: {state.config.installation_id}")
        write(f"Telegram mode: {state.config.telegram.mode}")
        write("Only symbolic references were stored; no credentials were displayed.")
        write("Configure the legacy runtime YAML and protected secret files, then run `zero doctor` before starting listener/panel processes.")
        return True
    except (EOFError, KeyboardInterrupt):
        write("Setup cancelled.")
        return False
    except (OSError, ValueError, sqlite3.Error):
        write("Setup failed: configuration or storage validation failed; check local files and retry.")
        return False


def _panel_lines(
    panel: str,
    *,
    store_path: Path | None,
    config_path: Path | None,
    runtime_path: Path | None,
    tail: int,
    create_backup: bool = False,
    chat_runtime: ChatRuntime | None = None,
) -> list[str]:
    if panel == "chat":
        return render_chat(chat_runtime)
    if panel == "logs":
        return render_logs(store_path=store_path, config_path=config_path, tail=tail)
    if panel == "backup":
        return render_backup(store_path=store_path, config_path=config_path, create=create_backup)
    return render(
        panel_name=panel,
        store_path=store_path,
        config_path=config_path,
        runtime_path=runtime_path,
    )


def _startup_animation_enabled(stdin=None, stdout=None, env=None) -> bool:
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    env = os.environ if env is None else env
    if not stdin.isatty() or not stdout.isatty():
        return False
    if str(env.get("TERM", "")).casefold() in {"dumb", "unknown"}:
        return False
    return not any(env.get(name) for name in ("NO_COLOR", "CI", "ZERO_TUI_NO_ANIMATION"))


def _startup_animation(stdscr, *, sleep=time.sleep, frame_delay: float = 0.055) -> None:
    frames = (
        ("Z", "initializing local console"),
        ("ZE", "loading private runtime"),
        ("ZER", "checking control surfaces"),
        ("ZERO", "sessions • groups • limits"),
        ("ZERO", "local • private • ready"),
    )
    stdscr.nodelay(True)
    try:
        height, width = stdscr.getmaxyx()
        for title, subtitle in frames:
            stdscr.erase()
            box_width = min(42, max(20, width - 4))
            top = max(0, height // 2 - 2)
            left = max(0, (width - box_width) // 2)
            lines = (
                "╭" + "─" * max(0, box_width - 2) + "╮",
                "│" + title.center(max(0, box_width - 2)) + "│",
                "│" + subtitle.center(max(0, box_width - 2)) + "│",
                "╰" + "─" * max(0, box_width - 2) + "╯",
            )
            for offset, line in enumerate(lines):
                try:
                    stdscr.addnstr(top + offset, left, line, max(1, width - left - 1))
                except Exception:
                    pass
            stdscr.refresh()
            if stdscr.getch() != -1:
                break
            sleep(frame_delay)
    finally:
        stdscr.nodelay(False)


def _interactive(stdscr, *, initial_panel: str, store_path: Path | None, config_path: Path | None, runtime_path: Path, tail: int, animate: bool = True) -> None:
    import curses

    if animate:
        _startup_animation(stdscr)

    controller = TUIController()
    controller.select(initial_panel)
    chat_runtime: ChatRuntime | None = None
    stdscr.keypad(True)
    stdscr.timeout(1000)
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    while True:
        if controller.panel == "chat" and chat_runtime is None:
            try:
                chat_runtime = build_chat_runtime(runtime_config_path=runtime_path, store_path=store_path)
            except Exception as exc:
                controller.status_message = f"Chat unavailable ({type(exc).__name__}); check runtime config."
        lines = _panel_lines(
            controller.panel,
            store_path=store_path,
            config_path=config_path,
            runtime_path=runtime_path,
            tail=tail,
            create_backup=False,
            chat_runtime=chat_runtime,
        )
        if controller.status_message:
            lines.extend(["", f"  {controller.status_message}"])
        height, width = stdscr.getmaxyx()
        viewport = max(1, height - 2)
        controller.scroll_offset = min(controller.scroll_offset, max(0, len(lines) - viewport))
        stdscr.erase()
        visible = lines[controller.scroll_offset : controller.scroll_offset + viewport]
        for row, line in enumerate(visible):
            try:
                stdscr.addnstr(row, 0, line, max(1, width - 1))
            except curses.error:
                pass
        footer = f"[{controller.panel}] ↑/↓ scroll  ←/→/Tab panel  Enter setup  r refresh  q quit"
        try:
            stdscr.addnstr(height - 1, 0, footer, max(1, width - 1))
        except curses.error:
            pass
        stdscr.refresh()
        key = stdscr.getch()
        controller.status_message = ""
        if key in (ord("q"), ord("Q"), 27):
            return
        if key in (curses.KEY_RIGHT, ord("\t"), ord("l")):
            controller.next_panel()
        elif key in (curses.KEY_LEFT, ord("h")):
            controller.previous_panel()
        elif key in (curses.KEY_UP, ord("k")):
            controller.scroll(-1, len(lines), viewport)
        elif key in (curses.KEY_DOWN, ord("j")):
            controller.scroll(1, len(lines), viewport)
        elif key == curses.KEY_PPAGE:
            controller.scroll(-viewport, len(lines), viewport)
        elif key == curses.KEY_NPAGE:
            controller.scroll(viewport, len(lines), viewport)
        elif key == curses.KEY_HOME:
            controller.reset_scroll()
        elif key == curses.KEY_END:
            controller.scroll(len(lines), len(lines), viewport)
        elif key == ord("r"):
            controller.reset_scroll()
            if controller.panel == "backup":
                lines = render_backup(store_path=store_path, config_path=config_path, create=True)
                controller.status_message = "Backup requested; press r again to create another snapshot."
        elif key in (curses.KEY_ENTER, 10, 13) and controller.panel == "chat":
            if chat_runtime is None:
                controller.status_message = "Chat runtime is unavailable."
            else:
                prompt = _curses_prompt(stdscr, "Prompt")
                if prompt.strip().casefold() == "/quit":
                    return
                if prompt.strip():
                    controller.status_message = "Thinking…"
                    stdscr.erase()
                    try:
                        stdscr.addnstr(0, 0, "Zero is thinking…", max(1, stdscr.getmaxyx()[1] - 1))
                    except curses.error:
                        pass
                    stdscr.refresh()
                    answer = asyncio.run(chat_runtime.ask(prompt))
                    controller.status_message = "Response complete."
                    controller.reset_scroll()
                    if answer:
                        controller.scroll(len(render_chat(chat_runtime)), len(render_chat(chat_runtime)), max(1, stdscr.getmaxyx()[0] - 2))
        elif key in (curses.KEY_ENTER, 10, 13) and controller.panel == "setup":
            controller.reset_scroll()
            run_setup_wizard(
                stdscr,
                config_path=config_path or canonical_config_path(),
                panel_path=panel_state_path(),
            )
        elif ord("1") <= key <= ord("0") + min(9, len(PANEL_NAMES)):
            controller.select(PANEL_NAMES[key - ord("1")])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zero tui", description="Zero TUI")
    parser.add_argument("--print", action="store_true", help="Print the selected panel once and exit (non-interactive)")
    parser.add_argument("--panel", default="status", choices=PANEL_NAMES, help="Which panel to display (default: status)")
    parser.add_argument("--store", type=Path, default=None, help="Path to ZeroStore database")
    parser.add_argument("--config", type=Path, default=None, help="Path to canonical config")
    parser.add_argument("--runtime-config", type=Path, default=Path(runtime_config_path()), help="Path to legacy runtime YAML used by Chat")
    parser.add_argument("--tail", type=int, default=50, help="Number of log lines to show (logs panel)")
    parser.add_argument("--no-animation", action="store_true", help="Skip the bounded interactive startup animation")
    return parser


def run_console_tui(
    *,
    initial_panel: str,
    store_path: Path | None,
    config_path: Path | None,
    runtime_path: Path,
    tail: int,
    input_fn: Callable[[str], str] | None = None,
    output_fn: Callable[[str], None] | None = None,
) -> int:
    """Run a line-oriented TUI that works without the optional curses backend.

    Windows CPython does not ship ``_curses``.  Returning after one status
    render makes ``zero tui`` look like a crashing UI, so this deliberately
    keeps a real interactive loop open until the user quits or closes input.
    ``input_fn`` and ``output_fn`` make the terminal lifecycle testable without
    a real TTY.
    """
    read = input if input_fn is None else input_fn
    write = print if output_fn is None else output_fn
    current_panel = initial_panel if initial_panel in PANEL_NAMES else "status"
    panel_commands = {str(index): panel for index, panel in enumerate(PANEL_NAMES, start=1)}
    chat_runtime: ChatRuntime | None = None

    while True:
        for line in _panel_lines(
            current_panel,
            store_path=store_path,
            config_path=config_path,
            runtime_path=runtime_path,
            tail=tail,
            create_backup=False,
            chat_runtime=chat_runtime,
        ):
            write(line)
        try:
            raw_command = read("zero> ").strip()
            command = raw_command.casefold()
        except EOFError:
            write("Zero console input closed; exiting.")
            return 0
        except KeyboardInterrupt:
            write("Zero console interrupted.")
            return 130

        if command in {"q", "quit", "exit"}:
            write("Zero console closed.")
            return 0
        if command in {"", "r", "refresh"}:
            if command in {"r", "refresh"} and current_panel == "backup":
                for line in _panel_lines(
                    current_panel,
                    store_path=store_path,
                    config_path=config_path,
                    runtime_path=runtime_path,
                    tail=tail,
                    create_backup=True,
                    chat_runtime=chat_runtime,
                ):
                    write(line)
            continue
        if command.startswith("chat "):
            message = raw_command[5:].strip()
            current_panel = "chat"
            if chat_runtime is None:
                try:
                    chat_runtime = build_chat_runtime(
                        runtime_config_path=runtime_path,
                        store_path=store_path,
                    )
                except Exception as exc:
                    write(f"Chat unavailable ({type(exc).__name__}); check runtime config.")
                    continue
            write("Zero is thinking…")
            try:
                asyncio.run(chat_runtime.ask(message))
            except Exception as exc:
                write(f"Chat failed ({type(exc).__name__}); no response was saved.")
            else:
                write("Response complete.")
            continue
        if command == "setup":
            current_panel = "setup"
            completed = run_console_setup_wizard(
                config_path=config_path or canonical_config_path(),
                panel_path=panel_state_path(),
                input_fn=input_fn,
                output_fn=output_fn,
            )
            write("Setup complete; returning to the Setup panel." if completed else "Setup did not complete; returning to the Setup panel.")
            continue
        if command in panel_commands:
            current_panel = panel_commands[command]
            continue
        if command in PANEL_NAMES:
            current_panel = command
            continue
        if command in {"?", "help"}:
            write("Commands: 1-8 or a panel name to navigate; setup runs setup; chat <prompt> sends a message; r refreshes; q quits.")
            continue
        write(f"Unknown command: {command or '(empty)'}. Type help for commands.")


def main(argv: list[str] | None = None) -> int:
    """TUI entry point. Uses `zero tui` subcommand."""
    args = build_parser().parse_args(argv)

    if args.print:
        if args.panel == "logs":
            lines = render_logs(
                store_path=args.store,
                config_path=args.config,
                tail=args.tail,
            )
        elif args.panel == "backup":
            lines = render_backup(
                store_path=args.store,
                config_path=args.config,
                create=False,
            )
        else:
            lines = render(
                panel_name=args.panel,
                store_path=args.store,
                config_path=args.config,
                runtime_path=args.runtime_config,
            )
        for line in lines:
            print(line)
        return 0

    # Prefer curses for a real TTY; otherwise retain a portable console session.
    try:
        import curses
    except ImportError:
        if sys.stdin.isatty() and sys.stdout.isatty():
            return run_console_tui(
                initial_panel=args.panel,
                store_path=args.store,
                config_path=args.config,
                runtime_path=args.runtime_config,
                tail=args.tail,
            )
        for line in _panel_lines(
            args.panel,
            store_path=args.store,
            config_path=args.config,
            runtime_path=args.runtime_config,
            tail=args.tail,
            create_backup=False,
        ):
            print(line)
        return 0

    try:
        return (
            curses.wrapper(
                lambda stdscr: _interactive(
                    stdscr,
                    initial_panel=args.panel,
                    store_path=args.store,
                    config_path=args.config,
                    runtime_path=args.runtime_config,
                    tail=args.tail,
                    animate=(not args.no_animation and _startup_animation_enabled()),
                )
            )
            or 0
        )
    except curses.error as exc:
        # A broken TERM can import curses but fail wrapper setup. Keep interactive
        # users in the portable console instead of presenting a transient error.
        if sys.stdin.isatty() and sys.stdout.isatty():
            print(f"zero tui: terminal initialization failed: {exc}; using the portable console.", file=sys.stderr)
            return run_console_tui(
                initial_panel=args.panel,
                store_path=args.store,
                config_path=args.config,
                runtime_path=args.runtime_config,
                tail=args.tail,
            )
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            for line in _panel_lines(
                args.panel,
                store_path=args.store,
                config_path=args.config,
                runtime_path=args.runtime_config,
                tail=args.tail,
                create_backup=False,
            ):
                print(line)
            return 0
        print(f"zero tui: terminal initialization failed: {exc}", file=sys.stderr)
        return 1
