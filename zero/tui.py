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
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Callable

from .paths import zero_home
from .configuration import canonical_config_path, ConfigStore
from .runtime_config import runtime_config_path
from .chat import ChatRuntime, build_chat_runtime


# Ordered panel names — the order here is the tab order in interactive mode.
PANEL_NAMES = ("status", "doctor", "groups", "backup", "logs", "setup", "chat", "sessions")

# Number keys map to panels by index.
# Navigation footer shown at the bottom of every panel.
_NAV_FOOTER = (
    "[1] status  [2] doctor  [3] groups  [4] backup  [5] logs  [6] setup  [7] chat  "
    "[8] sessions  [←/→] switch  [q] quit"
)


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

        for table in ("office_jobs", "office_delivery_outbox",
                      "proactive_followup_outbox", "office_quota_usage"):
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

    for table in ("office_jobs", "office_delivery_outbox",
                   "proactive_followup_outbox", "office_quota_usage"):
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


def render_status(store_path: Path | None = None,
                  config_path: Path | None = None) -> list[str]:
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
    for check in diagnostics():
        mark = "✓" if check["ok"] else "✗"
        lines.append(f"  {mark} {check['check']}: {check['detail']}")
    lines.append("")
    lines.append(f"  Refresh: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return lines


def render_doctor(store_path: Path | None = None,
                  config_path: Path | None = None) -> list[str]:
    """Doctor panel — run diagnostics() and show results."""
    from .cli import diagnostics

    lines = _header("Zero Doctor")
    lines.append("")
    checks = diagnostics()
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


def render_sessions(store_path: Path | None = None,
                    config_path: Path | None = None) -> list[str]:
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


def render_groups(store_path: Path | None = None,
                  config_path: Path | None = None) -> list[str]:
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


def render_backup(store_path: Path | None = None,
                  config_path: Path | None = None,
                  *,
                  create: bool = True) -> list[str]:
    """Backup panel — report the latest snapshot, optionally create one.

    ``create=True`` preserves the explicit ``--print --panel backup`` command
    contract. Interactive redraws pass ``create=False`` so merely navigating
    or resizing the TUI never creates a new backup as a side effect.
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
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"zero-{stamp}.db"
    # Avoid replacing a snapshot when two explicit requests land in one second.
    suffix = 1
    while backup_path.exists():
        backup_path = backup_dir / f"zero-{stamp}-{suffix}.db"
        suffix += 1

    # Online backup via the SQLite backup API — safe on a live WAL database.
    # The backup() method is called on the SOURCE connection and copies
    # the source 'main' database into the TARGET connection's 'main' database.
    try:
        src = sqlite3.connect(str(store_path))
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        size = backup_path.stat().st_size
        lines.append("  Backup: SUCCESS")
        lines.append(f"  Backup path: {backup_path}")
        lines.append(f"  Backup size: {size} bytes")
    except sqlite3.Error as exc:
        lines.append("  Backup: FAILED")
        lines.append(f"  Error: {exc}")
        if backup_path.exists():
            lines.append(f"  (partial file at {backup_path})")
    lines.append("")
    lines.append(f"  Snapshot taken: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return lines


def render_logs(store_path: Path | None = None,
                config_path: Path | None = None,
                tail: int = 50) -> list[str]:
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


def render_setup(store_path: Path | None = None,
                 config_path: Path | None = None) -> list[str]:
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
        except (OSError, ValueError) as exc:
            lines.append(f"  Config load error: {type(exc).__name__}: {exc}")
        lines.append("")

    # Panel-store setup state, if present.
    panel_db = home / "panel.db"
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
        except (OSError, sqlite3.Error, ValueError) as exc:
            lines.append(f"  Panel state error: {type(exc).__name__}: {exc}")
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
        lines.extend([
            "  Chat runtime is initialized when you submit the first prompt.",
            "  Enter: compose prompt    /help: local commands",
            "  Provider credentials remain in the protected runtime config.",
            "",
        ])
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
                    or (
                        cfg.telegram.mode == "user_session"
                        and bool(cfg.telegram.api_id and cfg.telegram.api_hash_ref and cfg.telegram.session_ref)
                    )
                    or (
                        cfg.telegram.mode == "hybrid"
                        and bool(
                            cfg.telegram.bot_token_ref
                            and cfg.telegram.api_id
                            and cfg.telegram.api_hash_ref
                            and cfg.telegram.session_ref
                        )
                    )
                ),
            )
        except (OSError, ValueError) as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
    if panel_path.exists():
        try:
            from .panel_store import PanelStore
            state = PanelStore(panel_path).get_setup_state()
            result.update(
                current_step=state.get("current_step"),
                completed=state.get("completed", False),
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
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


def render(panel_name: str = "status",
           store_path: Path | None = None,
           config_path: Path | None = None) -> list[str]:
    """Non-interactive render — dispatch to the named panel.

    Returns the display lines for that panel (with navigation footer).
    Used by tests and by `zero tui --print` for non-TTY output.
    """
    func = panels.get(panel_name, render_status)
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


def run_setup_wizard(stdscr, *, config_path: Path, panel_path: Path) -> bool:
    """Run the local setup flow using the canonical SetupService.

    The wizard intentionally accepts only symbolic secret references. Actual
    tokens, API hashes, and session material stay in the protected secret
    stores described by INSTALLATION.md and are never echoed or persisted by
    this TUI.
    """
    from .configuration import ConfigStore, SetupService
    from .panel_store import PanelStore

    store = ConfigStore(config_path)
    service = SetupService(store, installation_id="local")
    panel = PanelStore(panel_path, setup_service=service)
    try:
        current = service._config
        installation_id = _curses_prompt(
            stdscr,
            "Installation id",
            default=current.installation_id,
        )
        profile_state = service.apply_profile(installation_id=installation_id)
        if "profile" not in profile_state.completed_steps:
            _curses_message(stdscr, ["Setup failed", "Installation id is invalid."])
            return False
        panel.save_setup_step("profile", {"installation_id": installation_id})

        mode = _curses_prompt(
            stdscr,
            "Telegram mode (disabled/bot/user_session/hybrid)",
            default=current.telegram.mode,
        ).lower()
        if mode not in {"disabled", "bot", "user_session", "hybrid"}:
            _curses_message(stdscr, ["Setup failed", "Unknown Telegram mode."])
            return False

        bot_ref = None
        api_id = None
        api_hash_ref = None
        session_ref = None
        if mode in {"bot", "hybrid"}:
            bot_ref = _curses_prompt(stdscr, "Bot token reference", default=current.telegram.bot_token_ref or "telegram.bot_token")
        if mode in {"user_session", "hybrid"}:
            api_id_text = _curses_prompt(stdscr, "Telegram API id", default=str(current.telegram.api_id or ""))
            try:
                api_id = int(api_id_text)
            except ValueError:
                _curses_message(stdscr, ["Setup failed", "API id must be a positive integer."])
                return False
            api_hash_ref = _curses_prompt(stdscr, "API hash reference", default=current.telegram.api_hash_ref or "telegram.api_hash")
            session_ref = _curses_prompt(stdscr, "Session reference", default=current.telegram.session_ref or "telegram.session")

        state = service.apply_telegram(
            mode=mode,
            bot_token_ref=bot_ref,
            api_id=api_id,
            api_hash_ref=api_hash_ref,
            session_ref=session_ref,
        )
        if "telegram" not in state.completed_steps:
            _curses_message(stdscr, ["Setup failed", "Telegram settings did not pass validation."])
            return False
        panel.save_setup_step(
            "telegram",
            {
                "mode": mode,
                "bot_token_ref": bot_ref,
                "api_id": api_id,
                "api_hash_ref": api_hash_ref,
                "session_ref": session_ref,
            },
        )
        panel.save_setup_step("validation", {"config_valid": True})
        panel.save_setup_step("start", {"ready": True})
        _curses_message(
            stdscr,
            [
                "Setup complete",
                f"Installation: {state.config.installation_id}",
                f"Telegram mode: {state.config.telegram.mode}",
                "Only symbolic references were stored; no credentials were displayed.",
                "Run `zero doctor` before starting listener/panel processes.",
            ],
        )
        return True
    except (OSError, ValueError, sqlite3.Error) as exc:
        _curses_message(stdscr, ["Setup failed", f"{type(exc).__name__}: {exc}"])
        return False


def _panel_lines(panel: str, *, store_path: Path | None, config_path: Path | None, tail: int, create_backup: bool = False, chat_runtime: ChatRuntime | None = None) -> list[str]:
    if panel == "chat":
        return render_chat(chat_runtime)
    if panel == "logs":
        return render_logs(store_path=store_path, config_path=config_path, tail=tail)
    if panel == "backup":
        return render_backup(store_path=store_path, config_path=config_path, create=create_backup)
    return render(panel_name=panel, store_path=store_path, config_path=config_path)



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
                panel_path=zero_home() / "panel.db",
            )
        elif ord("1") <= key <= ord("0") + min(9, len(PANEL_NAMES)):
            controller.select(PANEL_NAMES[key - ord("1")])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zero tui", description="Zero TUI")
    parser.add_argument("--print", action="store_true",
                        help="Print the selected panel once and exit (non-interactive)")
    parser.add_argument("--panel", default="status",
                        choices=PANEL_NAMES,
                        help="Which panel to display (default: status)")
    parser.add_argument("--store", type=Path, default=None,
                        help="Path to ZeroStore database")
    parser.add_argument("--config", type=Path, default=None,
                        help="Path to canonical config")
    parser.add_argument("--runtime-config", type=Path, default=Path(runtime_config_path()),
                        help="Path to legacy runtime YAML used by Chat")
    parser.add_argument("--tail", type=int, default=50,
                        help="Number of log lines to show (logs panel)")
    parser.add_argument("--no-animation", action="store_true",
                        help="Skip the bounded interactive startup animation")
    return parser


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
                create=True,
            )
        else:
            lines = render(
                panel_name=args.panel,
                store_path=args.store,
                config_path=args.config,
            )
        for line in lines:
            print(line)
        return 0

    # Interactive mode — use curses if available, else fall back to --print.
    try:
        import curses
    except ImportError:
        for line in _panel_lines(
            args.panel,
            store_path=args.store,
            config_path=args.config,
            tail=args.tail,
            create_backup=args.panel == "backup",
        ):
            print(line)
        return 0

    try:
        return curses.wrapper(
            lambda stdscr: _interactive(
                stdscr,
                initial_panel=args.panel,
                store_path=args.store,
                config_path=args.config,
                runtime_path=args.runtime_config,
                tail=args.tail,
                animate=(not args.no_animation and _startup_animation_enabled()),
            )
        ) or 0
    except curses.error as exc:
        # A non-interactive terminal can still import curses successfully but
        # fail during wrapper setup. Preserve the documented print fallback.
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            for line in _panel_lines(
                args.panel,
                store_path=args.store,
                config_path=args.config,
                tail=args.tail,
                create_backup=args.panel == "backup",
            ):
                print(line)
            return 0
        print(f"zero tui: terminal initialization failed: {exc}", file=sys.stderr)
        return 1
