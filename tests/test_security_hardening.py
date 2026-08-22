from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from zero.config import ZeroConfig
from zero.fsprivacy import path_is_private, restrict_private_path
from zero.logging_utils import setup_logger
from zero.management import load_bot_token
from zero.runtime_control import stop_listener
from zero.storage import ZeroStore
from scripts.run_listener import _request_log_fields
from scripts.run_panel import owner_only


ROOT = Path(__file__).resolve().parents[1]


def _public_config(tmp_path: Path) -> Path:
    data = yaml.safe_load((ROOT / "config/zero.example.yaml").read_text(encoding="utf-8"))
    data["listener"]["telegram_api_hash"] = "__FROM_SECRET__"
    data["router"]["providers"]["gemini"]["keys"] = []
    data["router"]["providers"]["openrouter"]["keys"] = []
    data["telegram_search"]["enabled"] = True
    data["telegram_search"]["api_hash"] = "__FROM_SECRET__"
    public = tmp_path / "zero.yaml"
    public.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return public


def _secret_file(tmp_path: Path) -> Path:
    secret = tmp_path / "zero.secrets.yaml"
    secret.write_text(
        yaml.safe_dump(
            {
                "listener": {"telegram_api_hash": "listener-secret"},
                "router": {
                    "providers": {
                        "gemini": {"keys": ["gemini-secret"]},
                        "openrouter": {"keys": ["openrouter-secret"]},
                    }
                },
                "telegram_search": {"api_hash": "search-secret"},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    # Cross-platform: strip inherited/extra ACL entries; POSIX chmod cannot
    # remove NTFS inheritance on Windows.
    restrict_private_path(secret)
    return secret


def test_config_loads_credentials_from_protected_secret_file(tmp_path, monkeypatch):
    public = _public_config(tmp_path)
    secret = _secret_file(tmp_path)
    monkeypatch.setenv("ZERO_SECRET_FILE", str(secret))

    config = ZeroConfig.load(public)

    assert config.listener.telegram_api_hash == "listener-secret"
    assert config.router.providers.gemini.keys == ["gemini-secret"]
    assert config.router.providers.openrouter.keys == ["openrouter-secret"]
    assert config.telegram_search.api_hash == "search-secret"


def test_config_rejects_group_or_world_readable_secret_file(tmp_path, monkeypatch):
    public = _public_config(tmp_path)
    secret = tmp_path / "zero.secrets.yaml"
    secret.write_text("listener: {telegram_api_hash: x}", encoding="utf-8")
    if path_is_private(secret):  # pragma: no cover - restrictive umask systems
        os.chmod(secret, 0o640)
        assert not path_is_private(secret)
    monkeypatch.setenv("ZERO_SECRET_FILE", str(secret))

    with pytest.raises(PermissionError, match="secret file"):
        ZeroConfig.load(public)


def test_owner_commands_require_private_chat():
    config = SimpleNamespace(owner_user_id=42)
    owner = SimpleNamespace(id=42)

    assert owner_only(config, SimpleNamespace(from_user=owner, chat=SimpleNamespace(type="private"))) is True
    assert owner_only(config, SimpleNamespace(from_user=owner, chat=SimpleNamespace(type="group"))) is False


def test_bot_token_file_requires_private_permissions(tmp_path):
    token_file = tmp_path / "bot.env"
    token_file.write_text("BOT_TOKEN=token-value\n", encoding="utf-8")
    if path_is_private(token_file):  # pragma: no cover - restrictive umask systems
        os.chmod(token_file, 0o640)
        assert not path_is_private(token_file)

    with pytest.raises(PermissionError, match="bot token file"):
        load_bot_token(str(token_file))


def test_listener_stop_does_not_signal_pid_reuse(tmp_path, monkeypatch):
    import zero.runtime_control as runtime_control

    pid_file = tmp_path / "listener.pid"
    pid_file.write_text("1234", encoding="utf-8")
    monkeypatch.setattr(runtime_control, "LISTENER_PID", pid_file)
    monkeypatch.setattr(runtime_control, "_process_identity_matches", lambda pid: False)

    result = stop_listener()

    assert result == {"running": False, "pid": 1234}
    assert not pid_file.exists()


def test_logger_creates_private_directory_and_file(tmp_path):
    log_path = tmp_path / "logs" / "listener.log"
    logger = setup_logger(f"test.private.{tmp_path.name}", str(log_path))
    logger.info("safe")

    assert path_is_private(log_path.parent)
    assert path_is_private(log_path)


def test_systemd_units_drop_privileges_and_isolate_runtime():
    required = {
        "User=zero",
        "Group=zero",
        "UMask=0077",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "ReadWritePaths=/opt/zero/runtime",
    }
    for name in ("zero-listener.service", "zero-panel.service"):
        lines = {line.strip() for line in (ROOT / "deploy" / name).read_text(encoding="utf-8").splitlines()}
        assert required - {"ReadWritePaths=/opt/zero/runtime"} <= lines
        assert any(line.startswith("ReadWritePaths=/opt/zero/runtime") for line in lines)


def test_request_logging_keeps_content_out_of_log_fields():
    length, digest = _request_log_fields("secret message")

    assert length == len("secret message")
    assert digest != "secret message"
    assert len(digest) == 16


def test_store_restricts_database_and_parent_permissions(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o755)
    db = state / "zero.db"

    ZeroStore(str(db))

    assert path_is_private(state)
    assert path_is_private(db)
