from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_zero_version_is_available() -> None:
    result = subprocess.run([sys.executable, "-m", "zero", "version"], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "0.1.0-alpha"


def test_zero_config_show_is_redacted_and_machine_readable(tmp_path) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"schema_version": 1, "installation_id": "test", "telegram": {"mode": "disabled"}}), encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "zero", "config", "show", "--path", str(config)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["telegram"]["mode"] == "disabled"
    assert "token" not in result.stdout.lower()


def test_config_show_defaults_to_the_shared_canonical_path(tmp_path, monkeypatch) -> None:
    from zero import cli

    monkeypatch.delenv("ZERO_CANONICAL_CONFIG", raising=False)
    monkeypatch.setenv("ZERO_HOME", str(tmp_path / "runtime-home"))

    args = cli.build_parser().parse_args(["config", "show"])
    assert Path(args.path) == tmp_path / "runtime-home" / "config" / "zero.json"


def test_config_show_does_not_echo_malformed_config_values(tmp_path) -> None:
    marker = "DO_NOT_ECHO_THIS_INVALID_VALUE"
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "installation_id": "test",
                "telegram": {
                    "mode": "user_session",
                    "api_id": marker,
                    "api_hash_ref": "telegram.api_hash",
                    "session_ref": "telegram.session",
                },
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", "zero", "config", "show", "--path", str(config)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert marker not in result.stdout + result.stderr
    assert json.loads(result.stdout)["error"] == "configuration validation failed"


def test_doctor_reports_real_checks_and_fails_when_a_check_fails(tmp_path, monkeypatch, capsys):
    """doctor must exit non-zero on a real problem, not always succeed."""
    import json as _json

    from zero import cli

    monkeypatch.setenv("ZERO_HOME", str(tmp_path / "absent-home"))
    monkeypatch.setenv("ZERO_CANONICAL_CONFIG", str(tmp_path / "absent.json"))

    code = cli.main(["doctor"])
    payload = _json.loads(capsys.readouterr().out)

    assert code == 1 and payload["ok"] is False
    names = {check["check"] for check in payload["checks"]}
    assert {"python_version", "runtime_home_exists", "canonical_config", "sqlite_fts5"} <= names
    # A missing runtime home is a real failure, not a warning.
    by_name = {check["check"]: check for check in payload["checks"]}
    assert by_name["runtime_home_exists"]["ok"] is False


def test_doctor_reports_missing_legacy_runtime_config(tmp_path, monkeypatch, capsys):
    """A canonical-only setup must not be reported as ready to start."""
    import json as _json

    from zero import cli
    from zero.configuration import ConfigStore

    home = tmp_path / "home"
    home.mkdir()
    canonical = tmp_path / "zero.json"
    ConfigStore(canonical).save(ConfigStore.new_config("install-doctor"))
    monkeypatch.setenv("ZERO_HOME", str(home))
    monkeypatch.setenv("ZERO_CANONICAL_CONFIG", str(canonical))
    monkeypatch.delenv("ZERO_CONFIG_PATH", raising=False)

    assert cli.main(["doctor"]) == 1
    checks = {check["check"]: check for check in _json.loads(capsys.readouterr().out)["checks"]}
    assert checks["legacy_runtime_config"]["ok"] is False
    assert "missing:" in checks["legacy_runtime_config"]["detail"]


def test_doctor_passes_on_a_healthy_installation(tmp_path, monkeypatch, capsys):
    import json as _json

    from zero import cli
    from zero.configuration import ConfigStore

    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "zero.json"
    ConfigStore(config).save(ConfigStore.new_config("install-doctor"))
    runtime_config = home / "config" / "zero.yaml"
    runtime_config.parent.mkdir()
    runtime_config.write_text(
        (Path(__file__).resolve().parents[1] / "config" / "zero.example.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZERO_HOME", str(home))
    monkeypatch.setenv("ZERO_CANONICAL_CONFIG", str(config))
    monkeypatch.setenv("ZERO_CONFIG_PATH", str(runtime_config))

    code = cli.main(["doctor"])
    payload = _json.loads(capsys.readouterr().out)
    assert code == 0 and payload["ok"] is True and payload["failed"] == 0


def test_doctor_never_contacts_an_external_service(monkeypatch):
    """Diagnostics must be safe to run against a live host."""
    import socket

    from zero import cli

    def refuse(*args, **kwargs):
        raise AssertionError("doctor must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    cli.diagnostics()


def test_status_reports_the_runtime_home(tmp_path, monkeypatch, capsys):
    import json as _json

    from zero import cli

    monkeypatch.setenv("ZERO_HOME", str(tmp_path / "home"))
    assert cli.main(["status"]) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload["version"] == cli.VERSION
    assert "runtime_home" in payload and "~" not in payload["runtime_home"]


def test_panel_and_listener_subcommands_exist_for_the_container_entrypoint():
    """docker-compose invokes these; a drifted name would break the image."""
    from zero import cli

    parser = cli.build_parser()
    actions = [a for a in parser._actions if a.dest == "command"]
    assert actions, "expected a subcommand group"
    assert {"panel", "listener", "doctor", "status", "version", "config"} <= set(actions[0].choices)
