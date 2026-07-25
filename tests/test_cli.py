from __future__ import annotations

import json
import subprocess
import sys


def test_zero_version_is_available() -> None:
    result = subprocess.run([sys.executable, "-m", "zero", "version"], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "0.1.0-alpha"


def test_zero_config_show_is_redacted_and_machine_readable(tmp_path) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"schema_version": 1, "installation_id": "test", "telegram": {"mode": "disabled"}}), encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "zero", "config", "show", "--path", str(config)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["telegram"]["mode"] == "disabled"
    assert "token" not in result.stdout.lower()
