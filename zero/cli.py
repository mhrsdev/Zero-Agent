from __future__ import annotations

import argparse
import json
import runpy
import sqlite3
import sys
from pathlib import Path

from .configuration import ConfigStore, canonical_config_path
from .paths import panel_state_path, repo_path, zero_home
from .runtime_config import runtime_config_path
from .tui_contract import PANEL_NAMES

VERSION = "0.1.0-alpha"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zero", description="Zero administration console")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="print the Zero version")
    sub.add_parser("status", help="print installation status as JSON")
    sub.add_parser("doctor", help="run local diagnostics and report problems")
    sub.add_parser("panel", help="run the admin panel process")
    sub.add_parser("listener", help="run the Telegram listener process")
    setup = sub.add_parser("setup", help="run the interactive canonical setup wizard")
    setup.add_argument("--config", type=Path, default=None, help="Path to canonical config")
    setup.add_argument("--panel-db", type=Path, default=None, help="Path to setup state database")
    tui_parser = sub.add_parser("tui", help="run the terminal admin interface")
    tui_parser.add_argument("--print", action="store_true", help="Print the selected panel once and exit (non-interactive)")
    tui_parser.add_argument("--panel", default="status", choices=PANEL_NAMES, help="Which panel to display (default: status)")
    tui_parser.add_argument("--store", type=Path, default=None, help="Path to ZeroStore database")
    tui_parser.add_argument("--config", type=Path, default=None, help="Path to canonical config")
    tui_parser.add_argument("--runtime-config", type=Path, default=None, help="Path to legacy runtime YAML used by Chat")
    tui_parser.add_argument("--tail", type=int, default=50, help="Number of log lines to show (logs panel)")
    tui_parser.add_argument("--no-animation", action="store_true", help="Skip the bounded interactive startup animation")
    config = sub.add_parser("config", help="inspect configuration")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    show = config_sub.add_parser("show")
    show.add_argument("--path", default=str(canonical_config_path()))
    return parser


def _check(name: str, ok: bool, detail: str) -> dict[str, object]:
    return {"check": name, "ok": bool(ok), "detail": detail}


def diagnostics(
    config_path: str | Path | None = None,
    runtime_path: str | Path | None = None,
) -> list[dict[str, object]]:
    """Local, side-effect-free health checks.

    Every check reports a real observation. None of them contacts Telegram or a
    provider, so ``zero doctor`` is safe to run against a live host.
    """
    results: list[dict[str, object]] = []

    version = sys.version_info
    results.append(
        _check(
            "python_version",
            version >= (3, 11),
            f"{version.major}.{version.minor}.{version.micro}",
        )
    )

    home = zero_home()
    results.append(_check("runtime_home_exists", home.is_dir(), str(home)))
    if home.is_dir():
        probe = home / ".zero-doctor-write-probe"
        try:
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            writable, detail = True, "writable"
        except OSError as exc:
            writable, detail = False, type(exc).__name__
        results.append(_check("runtime_home_writable", writable, detail))

    config_path = Path(config_path) if config_path is not None else canonical_config_path()
    if config_path.exists():
        try:
            ConfigStore(config_path).load()
            results.append(_check("canonical_config", True, str(config_path)))
        except (OSError, ValueError) as exc:
            results.append(_check("canonical_config", False, f"invalid: {type(exc).__name__}"))
    else:
        results.append(_check("canonical_config", False, f"missing: {config_path} (run setup)"))

    runtime_path = Path(runtime_path) if runtime_path is not None else Path(runtime_config_path())
    if runtime_path.is_file():
        try:
            from .config import ZeroConfig

            ZeroConfig.load(runtime_path)
        except Exception as exc:
            results.append(_check("legacy_runtime_config", False, f"invalid: {type(exc).__name__}"))
        else:
            results.append(_check("legacy_runtime_config", True, str(runtime_path)))
    else:
        results.append(
            _check(
                "legacy_runtime_config",
                False,
                f"missing: {runtime_path} (configure ZERO_CONFIG_PATH before starting listener or panel)",
            )
        )

    try:
        sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        results.append(_check("sqlite_fts5", True, "available"))
    except sqlite3.Error:
        results.append(_check("sqlite_fts5", False, "missing FTS5; memory search is degraded"))

    for name in ("telethon", "aiogram", "pydantic", "yaml", "aiosqlite"):
        try:
            __import__(name)
            results.append(_check(f"dependency_{name}", True, "importable"))
        except ImportError as exc:
            results.append(_check(f"dependency_{name}", False, str(exc)))

    return results


def _run_script(name: str) -> int:
    """Execute a bundled entrypoint script in-process.

    The container entrypoint is ``python -m zero``, so the long-running
    processes are reachable as subcommands rather than as separate image
    commands that could drift from the packaged scripts.
    """
    script = repo_path("scripts", name)
    if not script.is_file():
        print(json.dumps({"error": f"entrypoint not found: {script}"}), file=sys.stderr)
        return 2
    root = str(repo_path())
    if root not in sys.path:
        sys.path.insert(0, root)
    runpy.run_path(str(script), run_name="__main__")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(VERSION)
        return 0
    if args.command == "status":
        print(
            json.dumps(
                {
                    "version": VERSION,
                    "config": str(canonical_config_path()),
                    "runtime_home": str(zero_home()),
                },
                indent=2,
            )
        )
        return 0
    if args.command == "doctor":
        checks = diagnostics()
        failed = [check for check in checks if not check["ok"]]
        print(json.dumps({"ok": not failed, "failed": len(failed), "checks": checks}, indent=2))
        return 1 if failed else 0
    if args.command == "panel":
        return _run_script("run_panel.py")
    if args.command == "listener":
        return _run_script("run_listener.py")
    if args.command == "setup":
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print("zero setup requires an interactive TTY; use `zero tui --print --panel setup` to inspect state.", file=sys.stderr)
            return 2
        config_path = args.config or canonical_config_path()
        panel_path = args.panel_db or panel_state_path()
        try:
            import curses
        except ImportError:
            from .tui import run_console_setup_wizard

            print("zero setup: curses is unavailable; using the portable console wizard.")
            return (
                0
                if run_console_setup_wizard(
                    config_path=config_path,
                    panel_path=panel_path,
                )
                else 1
            )
        from .tui import run_setup_wizard

        setup_started = False

        def _run_curses_setup(stdscr) -> int:
            nonlocal setup_started
            setup_started = True
            return (
                0
                if run_setup_wizard(
                    stdscr,
                    config_path=config_path,
                    panel_path=panel_path,
                )
                else 1
            )

        try:
            return curses.wrapper(_run_curses_setup)
        except curses.error as exc:
            if not setup_started:
                from .tui import run_console_setup_wizard

                print(f"zero setup: terminal initialization failed: {exc}; using the portable console wizard.", file=sys.stderr)
                return (
                    0
                    if run_console_setup_wizard(
                        config_path=config_path,
                        panel_path=panel_path,
                    )
                    else 1
                )
            print(f"zero setup: terminal initialization failed: {exc}", file=sys.stderr)
            return 1
    if args.command == "tui":
        from .tui import main as tui_main

        tui_args = []
        if getattr(args, "print", False):
            tui_args.append("--print")
        if getattr(args, "panel", None):
            tui_args.extend(["--panel", str(args.panel)])
        if getattr(args, "store", None):
            tui_args.extend(["--store", str(args.store)])
        if getattr(args, "config", None):
            tui_args.extend(["--config", str(args.config)])
        if getattr(args, "runtime_config", None):
            tui_args.extend(["--runtime-config", str(args.runtime_config)])
        if getattr(args, "tail", None):
            tui_args.extend(["--tail", str(args.tail)])
        if getattr(args, "no_animation", False):
            tui_args.append("--no-animation")
        return tui_main(tui_args)
    if args.command == "config" and args.config_command == "show":
        try:
            config = ConfigStore(args.path).load()
        except (OSError, ValueError):
            print(json.dumps({"error": "configuration validation failed"}))
            return 1
        print(json.dumps(config.model_dump(mode="json", exclude_none=True), indent=2, sort_keys=True))
        return 0
    return 2
