from __future__ import annotations

import json

import pytest

from zero.configuration import ConfigStore, SetupService
from zero.panel_store import PanelStore


def test_panel_telegram_setup_writes_canonical_config(tmp_path):
    config_store = ConfigStore(tmp_path / "canonical.json")
    setup = SetupService(config_store, installation_id="community")
    panel = PanelStore(tmp_path / "panel.db", setup_service=setup)

    panel.save_setup_step(
        "telegram",
        {
            "mode": "bot",
            "bot_token_ref": "telegram.bot_token",
        },
    )

    config = config_store.load()
    assert config.installation_id == "community"
    assert config.telegram.mode == "bot"
    assert config.telegram.bot_token_ref == "telegram.bot_token"


def test_panel_rejects_invalid_telegram_setup_without_advancing(tmp_path):
    config_store = ConfigStore(tmp_path / "canonical.json")
    panel = PanelStore(tmp_path / "panel.db", setup_service=SetupService(config_store))

    with pytest.raises(ValueError, match="validation"):
        panel.save_setup_step("telegram", {"mode": "bot", "bot_token": "raw-secret"})

    assert not config_store.path.exists()
    assert panel.get_setup_state()["current_step"] == "welcome"
