from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from zero.config import ZeroConfig


def main() -> int:
    config = ZeroConfig.load("/root/zero/config/zero.yaml")
    office = config.office
    checks = {"feature_enabled": office.enabled}
    if not office.enabled:
        print(json.dumps({"status": "disabled", "checks": checks}))
        return 0
    checks["rollout_configuration"] = (
        "limited" if office.rollout_required and office.rollout_user_ids and office.rollout_chat_ids
        else ("blocked" if office.rollout_required else "public")
    )
    cli = Path(office.cli_path)
    checks["officecli_executable"] = cli.is_file() and os.access(cli, os.X_OK)
    try:
        result = subprocess.run([str(cli), "--version"], capture_output=True, text=True, timeout=10, env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "OFFICECLI_SKIP_UPDATE": "1", "OFFICECLI_NO_AUTO_RESIDENT": "1"})
        checks["officecli_version"] = result.stdout.strip() if result.returncode == 0 else "unavailable"
    except (OSError, subprocess.SubprocessError):
        checks["officecli_version"] = "unavailable"
    root = Path(office.workspace_root)
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        probe = root / ".health-probe"
        probe.write_text("ok", encoding="ascii"); probe.unlink()
        checks["workspace_writable"] = True
    except OSError:
        checks["workspace_writable"] = False
    try:
        with sqlite3.connect(config.memory.db_path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            checks["migration"] = {"office_jobs", "office_quota_usage", "office_delivery_outbox"} <= tables
            row = conn.execute("SELECT value FROM office_metrics WHERE name='office_worker_heartbeat_epoch'").fetchone()
            checks["last_worker_heartbeat"] = int(row[0]) if row else None
            checks["worker_heartbeat_active"] = bool(row and int(time.time()) - int(row[0]) <= max(30, office.lease_seconds))
    except sqlite3.Error:
        checks["migration"] = False
    critical = (
        checks.get("officecli_executable"), checks.get("officecli_version") != "unavailable",
        checks.get("workspace_writable"), checks.get("migration"), checks.get("worker_heartbeat_active"),
        checks.get("rollout_configuration") != "blocked",
    )
    status = "ok" if all(critical) else "failed"
    print(json.dumps({"status": status, "checks": checks, "checked_at": int(time.time())}, separators=(",", ":")))
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
