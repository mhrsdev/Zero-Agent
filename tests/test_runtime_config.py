from __future__ import annotations

import json

import pytest

from zero.runtime_config import load_effective_config, runtime_config_path


class FakeConfig:
    @classmethod
    def load(cls, path):
        return {"legacy_path": str(path)}


def test_composition_loader_validates_canonical_then_loads_legacy(monkeypatch, tmp_path):
    canonical = tmp_path / "canonical.json"
    canonical.write_text(json.dumps({"installation_id": "test", "telegram": {"mode": "disabled"}}), encoding="utf-8")
    legacy = tmp_path / "zero.yaml"
    legacy.write_text("legacy: true\n", encoding="utf-8")
    monkeypatch.setenv("ZERO_CANONICAL_CONFIG", str(canonical))
    result = load_effective_config(legacy, FakeConfig)
    assert result == {"legacy_path": str(legacy)}


def test_invalid_canonical_blocks_legacy_startup(monkeypatch, tmp_path):
    canonical = tmp_path / "canonical.json"
    canonical.write_text(json.dumps({"installation_id": "test", "unknown": True}), encoding="utf-8")
    monkeypatch.setenv("ZERO_CANONICAL_CONFIG", str(canonical))
    with pytest.raises(ValueError):
        load_effective_config(tmp_path / "zero.yaml", FakeConfig)


def test_runtime_path_is_shared(monkeypatch):
    monkeypatch.setenv("ZERO_CONFIG_PATH", "/isolated/zero.yaml")
    assert runtime_config_path() == "/isolated/zero.yaml"
