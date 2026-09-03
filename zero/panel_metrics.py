"""Host measurement for the panel dashboard, with an explicit state per metric.

The panel must not present an unmeasured figure as a value, and it must not
present a failed read as an absent capability. Every sampler here returns one of
three states, which the API payload and the browser keep distinct:

``measured``
    ``value`` is authoritative for the moment it was sampled.
``unsupported``
    the host has no such interface (no ``/proc/meminfo``, no ``os.getloadavg``).
    No later poll will change that, so the UI says so instead of retrying hope.
``failed``
    the interface exists but the read raised. ``reason`` carries the exception
    type -- enough to diagnose, and free of paths, identifiers and secrets.

``value`` is ``None`` in every state except ``measured``: a zero is a
measurement, so an unknown may never borrow its rendering.

Every function here is synchronous blocking I/O and is meant to be called
through ``asyncio.to_thread`` -- see ``zero.panel_cache``.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

MEASURED = "measured"
UNSUPPORTED = "unsupported"
FAILED = "failed"

MEMINFO_PATH = Path("/proc/meminfo")


def _metric(state: str, value: str | None = None, reason: str | None = None) -> dict[str, Any]:
    return {"state": state, "value": value, "reason": reason}


def memory_percent_used(source: str | Path = MEMINFO_PATH) -> dict[str, Any]:
    """Return used physical memory as a percentage, read from procfs."""
    try:
        text = Path(source).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return _metric(UNSUPPORTED)
    except OSError as exc:
        return _metric(FAILED, reason=type(exc).__name__)
    fields: dict[str, str] = {}
    for line in text.splitlines():
        name, separator, rest = line.partition(":")
        if separator:
            fields[name] = rest
    try:
        total = int(fields["MemTotal"].split()[0])
        available = int(fields["MemAvailable"].split()[0])
    except (KeyError, IndexError, ValueError) as exc:
        return _metric(FAILED, reason=type(exc).__name__)
    if total <= 0:
        return _metric(FAILED, reason="EmptyMemTotal")
    return _metric(MEASURED, f"{(1 - available / total) * 100:.0f}%")


def cpu_load_average() -> dict[str, Any]:
    """Return the 1-minute load average; the interface is POSIX-only."""
    getloadavg = getattr(os, "getloadavg", None)
    if getloadavg is None:
        return _metric(UNSUPPORTED)
    try:
        return _metric(MEASURED, f"{getloadavg()[0]:.2f}")
    except OSError as exc:
        return _metric(FAILED, reason=type(exc).__name__)


def disk_free_percent(path: str | Path) -> dict[str, Any]:
    """Return free space on the volume holding *path*.

    The database file does not exist before the first write, and the operator
    needs the volume either way, so an absent target falls back to its directory.
    """
    target = Path(path)
    for candidate in (target, target.parent):
        try:
            usage = shutil.disk_usage(candidate)
        except FileNotFoundError:
            continue
        except OSError as exc:
            return _metric(FAILED, reason=type(exc).__name__)
        if usage.total <= 0:
            return _metric(FAILED, reason="EmptyVolume")
        return _metric(MEASURED, f"{usage.free / usage.total * 100:.0f}% free")
    return _metric(FAILED, reason="FileNotFoundError")


def sample_host(database_path: str | Path) -> dict[str, dict[str, Any]]:
    """One blocking pass over every host measurement the dashboard shows."""
    return {
        "cpu": cpu_load_average(),
        "ram": memory_percent_used(),
        "disk": disk_free_percent(database_path),
    }
