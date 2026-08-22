"""Installer + doctor simulated cycle tests.

Covers the acceptance criteria for the one-line installer without touching the
real ~/.zero home: doctor must be healthy on a fresh example config, fail
cleanly (exit 1) without one, never leak secret-looking material, and both
platform installer scripts must exist and parse on their native interpreter
(when available on this machine).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "config" / "zero.example.yaml"
HEX32 = re.compile(r"\b[0-9a-f]{32}\b")


def _run_doctor(zero_home: Path, *extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["ZERO_HOME"] = str(zero_home)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "doctor.py"), *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), env=env, timeout=120,
    )


def _runtime_config_path(zero_home: Path) -> Path:
    """Mirror zero.runtime_config.runtime_config_path under a custom ZERO_HOME."""
    from zero.runtime_config import runtime_config_path
    saved = os.environ.get("ZERO_HOME")
    os.environ["ZERO_HOME"] = str(zero_home)
    try:
        return Path(runtime_config_path())
    finally:
        if saved is None:
            os.environ.pop("ZERO_HOME", None)
        else:
            os.environ["ZERO_HOME"] = saved


def _copy_example_config(zero_home: Path) -> None:
    target = _runtime_config_path(zero_home)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(EXAMPLE, target)


def test_doctor_healthy_on_fresh_example_config(tmp_path):
    assert EXAMPLE.exists(), "config/zero.example.yaml must ship with the repo"
    _copy_example_config(tmp_path)
    proc = _run_doctor(tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, "doctor failed on fresh example config: " + out
    assert "healthy" in out
    # No 32-hex-char secret lookalikes may appear in output.
    assert not HEX32.search(out), "possible secret leak in doctor output: " + out


def test_doctor_fails_cleanly_without_config(tmp_path):
    proc = _run_doctor(tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"expected exit 1 without config, got {proc.returncode}: " + out
    assert "missing" in out or "config_exists" in out


def test_doctor_json_mode_machine_readable(tmp_path):
    _copy_example_config(tmp_path)
    proc = _run_doctor(tmp_path, "--json")
    payload = json.loads(proc.stdout)
    assert payload["exit_code"] == 0
    assert all(c["ok"] or c.get("level") == "warning" for c in payload["checks"])


def test_installer_scripts_exist():
    assert (ROOT / "install.sh").exists()
    assert (ROOT / "install.ps1").exists()


def test_install_sh_parses_when_bash_available():
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available on this machine")
    # WSL bash cannot open Windows-style absolute paths; run from the repo
    # directory with a relative filename instead.
    proc = subprocess.run([bash, "-n", "install.sh"], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, "install.sh syntax error: " + proc.stderr


def test_install_ps1_parses_when_powershell_available():
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        pytest.skip("powershell not available on this machine")
    script = (
        "$t=$null;$e=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{ROOT / 'install.ps1'}',[ref]$t,[ref]$e)|Out-Null;"
        "if($e -and $e.Count -gt 0){$e|ForEach-Object{$_.Message};exit 1}else{exit 0}"
    )
    proc = subprocess.run([ps, "-NoProfile", "-Command", script],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, "install.ps1 parse errors: " + proc.stdout + proc.stderr