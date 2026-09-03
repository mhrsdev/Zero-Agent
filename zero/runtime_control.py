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
    return _pid_record(path)[0]


def _pid_record(path: Path) -> tuple[int | None, str]:
    """Return ``(pid, record state)``.

    The three failure modes are distinct and the panel renders them distinctly:
    ``absent`` means no listener was ever started through this installation,
    ``unreadable`` means the record exists but cannot be read (permissions, a
    directory in its place), and ``malformed`` means it holds no pid. Only
    ``absent`` justifies reporting the listener as stopped.
    """
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None, "absent"
    except OSError:
        return None, "unreadable"
    try:
        return int(raw.strip()), "recorded"
    except ValueError:
        return None, "malformed"


def _identity_is_verifiable() -> bool:
    """Whether this host can confirm that a pid is really the listener.

    Identity is checked by reading ``/proc/<pid>/cmdline``; without procfs a pid
    match would prove nothing and pid reuse would be indistinguishable from a
    live listener. Such a host reports ``unverified`` rather than guessing.
    """
    return Path("/proc").is_dir()


def _process_identity_matches(pid: int) -> bool:
    try:
        args = [arg for arg in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\\0') if arg]
    except (FileNotFoundError, PermissionError, OSError):
        return False
    decoded = [arg.decode(errors='replace') for arg in args]
    return _LISTENER_SCRIPT in decoded and decoded and decoded[0] in {_LISTENER_PYTHON, 'python', 'python3'}


def listener_status() -> dict[str, str | int | bool]:
    """Report the listener as ``running``, ``stopped`` or ``unverified``.

    ``running`` stays reserved for a pid whose process identity was confirmed, so
    a caller that only reads that key can never be told a listener is up when it
    is merely recorded.
    """
    pid, record = _pid_record(LISTENER_PID)
    if pid is None:
        return {'running': False, 'pid': 0, 'state': 'stopped' if record == 'absent' else 'unverified'}
    if not _identity_is_verifiable():
        return {'running': False, 'pid': pid, 'state': 'unverified'}
    running = _process_identity_matches(pid)
    return {'running': running, 'pid': pid, 'state': 'running' if running else 'stopped'}


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
