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
    setup    — show setup state (config exists, secrets configured)

Usage:
    python -m zero tui                  # interactive
    python -m zero tui --print          # default panel, non-interactive
    python -m zero tui --print --panel doctor
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Callable

from .paths import zero_home
from .configuration import canonical_config_path, ConfigStore


# Ordered panel names — the order here is the tab order in interactive mode.
PANEL_NAMES = ("status", "doctor", "groups", "backup", "logs", "setup")

# Number keys map to panels by index.
# Navigation footer shown at the bottom of every panel.
_NAV_FOOTER = (
    "[1] status  [2] doctor  [3] groups  [4] backup  [5] logs  [6] setup  "
    "[←/→] switch  [q] quit"
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
                        lines.append(f"    settings:")
                        for key, value in sorted(settings.items()):
                            lines.append(f"      {key}: {json.dumps(value, ensure_ascii=False)}")
                    else:
                        lines.append(f"    settings: (none)")
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
                  config_path: Path | None = None) -> list[str]:
    """Backup panel — snapshot/export the ZeroStore DB, show the path.

    Uses SQLite's online backup API (`Connection.backup`) which safely copies
    a live (possibly WAL-mode) database without taking an exclusive lock.
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

    # Determine backup destination: <home>/backups/zero-<timestamp>.db
    home = zero_home()
    backup_dir = home / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"zero-{stamp}.db"

    # Online backup via the SQLite backup API — safe on a live WAL database.
    # The backup() method is called on the SOURCE connection and copies the
    # source 'main' database into the TARGET connection's 'main' database.
    try:
        src = sqlite3.connect(str(store_path))
        dst = sqlite3.connect(str(backup_path))
        src.backup(dst)
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


def main(argv: list[str] | None = None) -> int:
    """TUI entry point. Uses `zero tui` subcommand."""
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
    parser.add_argument("--tail", type=int, default=50,
                        help="Number of log lines to show (logs panel)")
    args = parser.parse_args(argv)

    if args.print:
        if args.panel == "logs":
            for line in render_logs(store_path=args.store,
                                    config_path=args.config,
                                    tail=args.tail):
                print(line)
        else:
            for line in render(panel_name=args.panel,
                               store_path=args.store,
                               config_path=args.config):
                print(line)
        return 0

    # Interactive mode — use curses if available, else fall back to --print.
    try:
        import curses
    except ImportError:
        for line in render(panel_name=args.panel, store_path=args.store,
                           config_path=args.config):
            print(line)
        return 0

    current = PANEL_NAMES.index(args.panel) if args.panel in PANEL_NAMES else 0

    def _curses_main(stdscr):
        nonlocal current
        curses.curs_set(0)
        stdscr.clear()
        while True:
            panel = PANEL_NAMES[current]
            if panel == "logs":
                lines = render_logs(store_path=args.store,
                                    config_path=args.config,
                                    tail=args.tail)
            else:
                lines = render(panel_name=panel, store_path=args.store,
                               config_path=args.config)
            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()
            for i, line in enumerate(lines):
                if i < max_y - 1:
                    stdscr.addstr(i, 0, line[:max_x - 1])
            stdscr.refresh()
            try:
                key = stdscr.getch()
            except curses.error:
                time.sleep(1)
                continue
            # Quit
            if key in (ord('q'), ord('Q'), 27):  # q, Q, ESC
                break
            # Number keys 1-6 switch panels
            if key in (ord('1'), ord('2'), ord('3'), ord('4'), ord('5'), ord('6')):
                idx = key - ord('1')
                if 0 <= idx < len(PANEL_NAMES):
                    current = idx
            # Arrow left/right switch panels
            elif key == curses.KEY_LEFT:
                current = (current - 1) % len(PANEL_NAMES)
            elif key == curses.KEY_RIGHT:
                current = (current + 1) % len(PANEL_NAMES)

    return curses.wrapper(_curses_main) or 0
