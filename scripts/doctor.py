"""Installation health check for Zero.

Exit codes:
    0  healthy
    1  configuration problem
    2  dependency/import problem
    3  storage problem

Never prints secret values (api hashes, tokens, keys); only presence/absence.
Run:  python scripts/doctor.py [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHECKS: list[dict] = []


def check(name: str, ok: bool, detail: str, category: str, *, level: str = "critical") -> bool:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": detail,
                   "category": category, "level": level})
    return bool(ok)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    # 1) Core imports (dependency layer).
    try:
        from zero.config import ZeroConfig  # noqa: F401
        from zero.runtime_config import load_effective_config, runtime_config_path  # noqa: F401
        from zero.storage import ZeroStore  # noqa: F401
        from zero.reactions import should_react  # noqa: F401
        from zero.automation import automation_disabled  # noqa: F401
        check("imports", True, "core modules import cleanly", "deps")
    except Exception as exc:
        check("imports", False, f"{type(exc).__name__}: {exc}", "deps")
        return _finish(args, 2)

    # 2) Configuration resolves.
    try:
        from zero.runtime_config import load_effective_config, runtime_config_path
        from zero.config import ZeroConfig
        config_path = Path(runtime_config_path())
        if not config_path.exists():
            check("config_exists", False,
                  f"missing {config_path}; copy config/zero.example.yaml there", "config")
            return _finish(args, 1)
        config = load_effective_config(config_path, ZeroConfig)
        check("config_valid", True, str(config_path), "config")
    except Exception as exc:
        check("config_valid", False, f"{type(exc).__name__}: {exc}", "config")
        return _finish(args, 1)

    # 3) Secrets present but never printed.
    # A fresh install legitimately ships without credentials yet: warn, do not
    # fail -- completing setup is a next step, not a broken installation.
    has_api = bool(getattr(config.listener, "telegram_api_id", 0)) and bool(
        getattr(config.listener, "telegram_api_hash", ""))
    check("telegram_credentials", has_api,
          "api_id/api_hash set" if has_api else "api_id/api_hash missing (listener cannot start)",
          "config", level="warning")

    # 4) Storage writable + openable.
    try:
        db_path = Path(config.memory.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        probe = db_path.with_name(db_path.name + ".doctor-probe")
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
        check("storage_writable", True, str(db_path.parent), "storage")
    except Exception as exc:
        check("storage_writable", False, f"{type(exc).__name__}: {exc}", "storage")
        return _finish(args, 3)

    # 5) Kill-switch sanity (informational; both states are valid).
    from zero.automation import kill_switch_active, observe_only
    check("automation_kill_switch", True,
          f"kill={'on' if kill_switch_active() else 'off'} observe={'on' if observe_only() else 'off'}",
          "config")

    return _finish(args, 0)


def _finish(args: argparse.Namespace, code: int) -> int:
    failed = [c for c in CHECKS if not c["ok"] and c.get("level") != "warning"]
    if args.json:
        print(json.dumps({"exit_code": code, "checks": CHECKS}, ensure_ascii=False, indent=2))
    else:
        for c in CHECKS:
            if c["ok"]:
                mark = "OK  "
            elif c.get("level") == "warning":
                mark = "WARN"
            else:
                mark = "FAIL"
            print(f"[{mark}] {c['category']}/{c['name']}: {c['detail']}")
        print(f"doctor: {'healthy' if code == 0 else f'unhealthy (exit {code})'}; "
              f"{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    return code


if __name__ == "__main__":
    sys.exit(main())