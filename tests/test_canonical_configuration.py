from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from zero.configuration import (
    CanonicalConfig,
    ConfigStore,
    SetupService,
    TelegramConfig,
)


def bot_config() -> CanonicalConfig:
    return CanonicalConfig(
        installation_id="install-1",
        telegram=TelegramConfig(mode="bot", bot_token_ref="telegram.bot_token"),
    )


def test_canonical_config_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        CanonicalConfig.model_validate({"installation_id": "i", "unexpected": True})


def test_config_store_persists_references_not_secret_values(tmp_path) -> None:
    path = tmp_path / "config.json"
    ConfigStore(path).save(bot_config())

    raw = path.read_text(encoding="utf-8")
    assert "telegram.bot_token" in raw
    assert "123456:real-token-value" not in raw
    assert path.stat().st_mode & 0o077 == 0


def test_setup_service_marks_telegram_complete_only_after_runtime_validation(tmp_path) -> None:
    path = tmp_path / "config.json"
    service = SetupService(ConfigStore(path))

    incomplete = service.apply_telegram(mode="bot")
    assert incomplete.completed_steps == ()
    assert not path.exists()

    complete = service.apply_telegram(mode="bot", bot_token_ref="telegram.bot_token")
    assert complete.completed_steps == ("telegram",)
    assert complete.config.telegram.mode == "bot"
    assert json.loads(path.read_text(encoding="utf-8"))["telegram"]["mode"] == "bot"


def test_hybrid_requires_both_transport_references() -> None:
    with pytest.raises(ValidationError):
        TelegramConfig(mode="hybrid", bot_token_ref="telegram.bot_token")
