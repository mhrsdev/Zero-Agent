import os
from pathlib import Path

import pytest

from zero.panel_store import PanelStore


def test_admin_passwords_are_hashed_and_sessions_persist(tmp_path: Path):
    db = tmp_path / "panel.db"
    store = PanelStore(db)
    store.create_admin("admin", "correct horse battery staple")

    assert store.verify_admin("admin", "correct horse battery staple") is not None
    assert store.verify_admin("admin", "wrong") is None
    assert "correct horse" not in db.read_bytes().decode("utf-8", errors="ignore")

    token, csrf = store.create_session(1, ttl_seconds=3600)
    reopened = PanelStore(db)
    session = reopened.get_session(token)
    assert session and session["admin_id"] == 1 and session["csrf_token"] == csrf


def test_setup_state_rejects_raw_telegram_credential_keys(tmp_path: Path):
    store = PanelStore(tmp_path / "panel.db")

    with pytest.raises(ValueError, match="validation"):
        store.save_setup_step("telegram", {"mode": "bot", "bot_token": "sensitive"})

    state = store.get_setup_state()
    assert state["current_step"] == "welcome"
    assert "telegram" not in state["data"]
    assert "sensitive" not in (tmp_path / "panel.db").read_bytes().decode("utf-8", errors="ignore")


def test_setup_state_rejects_raw_telegram_reference_values_without_a_setup_service(tmp_path: Path):
    """A standalone panel store must not become a raw-secret persistence bypass."""
    db = tmp_path / "panel.db"
    raw_api_hash = "a" * 32
    store = PanelStore(db)

    with pytest.raises(ValueError, match="validation"):
        store.save_setup_step(
            "telegram",
            {
                "mode": "user_session",
                "api_id": 1,
                "api_hash_ref": raw_api_hash,
                "session_ref": "telegram.session",
            },
        )

    state = store.get_setup_state()
    assert state["current_step"] == "welcome"
    assert "telegram" not in state["data"]
    assert raw_api_hash not in db.read_bytes().decode("utf-8", errors="ignore")


def test_setup_state_rejects_raw_bot_token_reference_without_a_setup_service(tmp_path: Path):
    """Reference validation must run even when no numeric API id is required."""
    db = tmp_path / "panel.db"
    raw_bot_token = "123456789:" + ("a" * 31)
    store = PanelStore(db)

    with pytest.raises(ValueError, match="validation"):
        store.save_setup_step("telegram", {"mode": "bot", "bot_token_ref": raw_bot_token})

    assert "telegram" not in store.get_setup_state()["data"]
    assert raw_bot_token not in db.read_bytes().decode("utf-8", errors="ignore")


def test_panel_store_uses_private_database_and_directory_permissions(tmp_path: Path):
    db = tmp_path / "panel.db"
    PanelStore(db)

    if os.name != "nt":
        assert db.stat().st_mode & 0o077 == 0
        assert db.parent.stat().st_mode & 0o077 == 0


def test_setup_state_rejects_raw_or_unknown_non_telegram_setup_payloads(tmp_path: Path):
    """Every setup endpoint must reject raw credentials and unmodeled fields."""
    db = tmp_path / "panel.db"
    raw_bot_token = "123456789:" + ("a" * 31)
    store = PanelStore(db)

    for step, payload in (
        ("credentials", {"session_ref": raw_bot_token}),
        ("web_search", {"api_key": raw_bot_token}),
        ("provider", {"model": raw_bot_token}),
        ("validation", {"unexpected": True}),
    ):
        with pytest.raises(ValueError, match="validation"):
            store.save_setup_step(step, payload)

    state = store.get_setup_state()
    assert state["current_step"] == "welcome"
    assert not state["data"]
    assert raw_bot_token not in db.read_bytes().decode("utf-8", errors="ignore")


def test_setup_state_accepts_reference_only_web_search_configuration(tmp_path: Path):
    store = PanelStore(tmp_path / "panel.db")

    store.save_setup_step(
        "web_search",
        {"enabled": "on", "provider": "tavily", "api_key_ref": "web.tavily"},
    )

    state = store.get_setup_state()
    assert state["data"]["web_search"]["enabled"] is True
    assert state["data"]["web_search"]["provider"] == "tavily"
    assert state["data"]["web_search"]["api_key_ref"] == "[stored securely]"


def test_setup_state_redacts_legacy_session_reference_values(tmp_path: Path):
    """Even pre-existing unsafe panel rows must not leak through the local API."""
    import json
    import sqlite3

    db = tmp_path / "panel.db"
    marker = "SENSITIVE_SESSION_VALUE"
    store = PanelStore(db)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE panel_setup SET data_json=? WHERE id=1",
            (json.dumps({"telegram": {"session_ref": marker}}),),
        )

    state = store.get_setup_state()
    assert marker not in str(state)
    assert state["data"]["telegram"]["session_ref"] == "[stored securely]"


def test_default_admin_is_forced_to_change_password(tmp_path: Path):
    store = PanelStore(tmp_path / "panel.db")
    with pytest.raises(ValueError, match="password must be at least 12"):
        store.create_admin("Admin", "Admin", must_change_password=True)
    store.create_admin("admin", "a_strong_password_123", must_change_password=True)
    admin = store.verify_admin("admin", "a_strong_password_123")
    assert admin and admin["must_change_password"] == 1
    store.change_admin_password(admin["id"], "a_strong_password_123", "a secure replacement password")
    updated = store.verify_admin("admin", "a secure replacement password")
    assert updated and updated["must_change_password"] == 0

def test_existing_installation_can_skip_setup(tmp_path: Path):
    store = PanelStore(tmp_path / "panel.db")
    store.skip_setup()
    state = store.get_setup_state()
    assert state["completed"] is True
    assert state["current_step"] == "start"
    assert state["data"]["skipped"]["reason"] == "existing installation"


def test_setup_transition_rejects_unknown_step(tmp_path: Path):
    store = PanelStore(tmp_path / "panel.db")
    with pytest.raises(ValueError, match="unknown setup step"):
        store.save_setup_step("not-a-step", {})
