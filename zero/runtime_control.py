from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from .fsprivacy import restrict_private_path
from .paths import repo_path, zero_home_path

PID_DIR = zero_home_path("pids")
PID_DIR.mkdir(parents=True, exist_ok=True)
try:
    restrict_private_path(PID_DIR, directory=True)
except PermissionError:
    pass
LISTENER_PID = PID_DIR / "listener.pid"
_LISTENER_SCRIPT = str(repo_path("scripts", "run_listener.py"))
_LISTENER_PYTHON = os.environ.get("ZERO_PYTHON") or sys.executable


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except Exception:
        return None


def _process_identity_matches(pid: int) -> bool:
    try:
        args = [arg for arg in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\\0') if arg]
    except (FileNotFoundError, PermissionError, OSError):
        return False
    decoded = [arg.decode(errors='replace') for arg in args]
    return _LISTENER_SCRIPT in decoded and decoded and decoded[0] in {_LISTENER_PYTHON, 'python', 'python3'}


def listener_status() -> dict[str, str | int | bool]:
    pid = _read_pid(LISTENER_PID)
    running = bool(pid and _process_identity_matches(pid))
    return {'running': running, 'pid': pid or 0}


def start_listener() -> dict[str, str | int | bool]:
    status = listener_status()
    if status['running']:
        return status
    proc = subprocess.Popen([_LISTENER_PYTHON, _LISTENER_SCRIPT], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    LISTENER_PID.write_text(str(proc.pid))
    try:
        restrict_private_path(LISTENER_PID)
    except PermissionError:
        pass
    return {'running': True, 'pid': proc.pid}


def stop_listener() -> dict[str, str | int | bool]:
    pid = _read_pid(LISTENER_PID)
    if not pid:
        return {'running': False, 'pid': 0}
    if _process_identity_matches(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    try:
        LISTENER_PID.unlink()
    except FileNotFoundError:
        pass
    return {'running': False, 'pid': pid}


def restart_listener() -> dict[str, str | int | bool]:
    stop_listener()
    return start_listener()
