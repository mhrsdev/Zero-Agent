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


def test_setup_state_resumes_and_never_returns_secret_values(tmp_path: Path):
    store = PanelStore(tmp_path / "panel.db")
    store.save_setup_step("telegram", {"mode": "bot", "bot_token": "sensitive"})

    state = store.get_setup_state()
    assert state["current_step"] == "credentials"
    assert state["data"]["telegram"]["mode"] == "bot"
    assert state["data"]["telegram"]["bot_token"] == "[stored securely]"
    assert "sensitive" not in (tmp_path / "panel.db").read_bytes().decode("utf-8", errors="ignore")


def test_default_admin_is_forced_to_change_password(tmp_path: Path):
    store = PanelStore(tmp_path / "panel.db")
    store.create_admin("Admin", "Admin", must_change_password=True)
    admin = store.verify_admin("admin", "Admin")
    assert admin and admin["must_change_password"] == 1
    store.change_admin_password(admin["id"], "Admin", "a secure replacement password")
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
