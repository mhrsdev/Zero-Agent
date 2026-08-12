from __future__ import annotations

import json
import os
from pathlib import Path

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


@pytest.mark.parametrize(
    "unsafe_installation_id",
    [
        "a" * 32,
        "123456789:" + ("a" * 31),
        "s" * 48,
    ],
)
def test_canonical_config_rejects_credential_shaped_installation_ids(unsafe_installation_id: str) -> None:
    with pytest.raises(ValidationError, match="installation id"):
        CanonicalConfig(installation_id=unsafe_installation_id)


def test_config_store_persists_references_not_secret_values(tmp_path) -> None:
    path = tmp_path / "config.json"
    ConfigStore(path).save(bot_config())

    raw = path.read_text(encoding="utf-8")
    assert "telegram.bot_token" in raw
    assert "123456:real-token-value" not in raw
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0
        assert path.parent.stat().st_mode & 0o077 == 0


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


def test_setup_service_applies_profile_and_telegram_as_one_configuration_write(tmp_path: Path, monkeypatch) -> None:
    store = ConfigStore(tmp_path / "zero.json")
    service = SetupService(store, installation_id="initial")
    original_save = store.save
    writes = 0

    def count_save(config: CanonicalConfig) -> None:
        nonlocal writes
        writes += 1
        original_save(config)

    monkeypatch.setattr(store, "save", count_save)
    state = service.apply_setup(
        installation_id="final-installation",
        mode="user_session",
        api_id=12345,
        api_hash_ref="telegram.api_hash",
        session_ref="telegram.session",
    )

    assert state.config.installation_id == "final-installation"
    assert state.config.telegram.mode == "user_session"
    persisted = store.load()
    assert persisted.installation_id == "final-installation"
    assert persisted.telegram.api_id == 12345
    assert set(state.completed_steps) == {"profile", "telegram"}
    assert writes == 1


def test_setup_service_rejects_invalid_atomic_setup_without_changing_existing_config(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "zero.json")
    store.save(CanonicalConfig(installation_id="original"))
    service = SetupService(store, installation_id="original")

    state = service.apply_setup(
        installation_id="replacement",
        mode="user_session",
        api_id=1,
        api_hash_ref="a" * 32,
        session_ref="telegram.session",
    )

    assert state.completed_steps == ()
    assert store.load().installation_id == "original"
    assert not store.backup_path.exists()


def test_hybrid_requires_both_transport_references() -> None:
    with pytest.raises(ValidationError):
        TelegramConfig(mode="hybrid", bot_token_ref="telegram.bot_token")


def test_telegram_config_rejects_boolean_api_id() -> None:
    with pytest.raises(ValidationError):
        TelegramConfig(
            mode="user_session",
            api_id=True,
            api_hash_ref="telegram.api_hash",
            session_ref="telegram.session",
        )


def test_telegram_config_rejects_raw_api_hash_as_a_secret_reference() -> None:
    with pytest.raises(ValidationError, match="symbolic"):
        TelegramConfig(
            mode="user_session",
            api_id=1,
            api_hash_ref="a" * 32,
            session_ref="telegram.session",
        )


def test_telegram_config_rejects_opaque_session_material_as_a_reference() -> None:
    with pytest.raises(ValidationError, match="symbolic"):
        TelegramConfig(
            mode="user_session",
            api_id=1,
            api_hash_ref="telegram.api_hash",
            session_ref="s" * 48,
        )
