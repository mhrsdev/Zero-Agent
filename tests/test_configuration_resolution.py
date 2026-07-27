from __future__ import annotations

import json

import pytest

from zero.configuration import ConfigStore, canonical_config_path


def test_all_composition_roots_use_one_explicit_config_path(monkeypatch, tmp_path):
    path = tmp_path / "canonical.json"
    monkeypatch.setenv("ZERO_CANONICAL_CONFIG", str(path))
    assert canonical_config_path() == path
    assert canonical_config_path() == ConfigStore.default_path()


def test_invalid_config_is_rejected_on_reload(tmp_path):
    path = tmp_path / "canonical.json"
    path.write_text(json.dumps({"installation_id": "x", "unknown": True}), encoding="utf-8")
    with pytest.raises(Exception):
        ConfigStore(path).load()


def test_save_keeps_previous_config_available_for_rollback(tmp_path):
    path = tmp_path / "canonical.json"
    store = ConfigStore(path)
    first = store.new_config("one")
    store.save(first)
    store.save(store.new_config("two"))
    assert store.load().installation_id == "two"
    assert store.rollback() is True
    assert store.load().installation_id == "one"
