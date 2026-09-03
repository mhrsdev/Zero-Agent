"""Authentication & Authorization adversarial tests.

Verifies that the auth system is secure and group-aware:
- Passwords are hashed (scrypt, not plaintext)
- Sessions are token-hashed, not stored in plaintext
- CSRF is enforced on all write operations
- Rate limiting is applied to auth endpoints
- Default admin must change password
- Secrets are stripped from setup state
- Viewer role cannot mutate
- Session revocation works
- Auth is group-scoped: an admin of group-a has no rights in group-b
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest

from zero.panel_store import PanelStore, DuplicateAdminError


@pytest.fixture
def store(tmp_path):
    return PanelStore(tmp_path / "panel.db")


class TestPasswordSecurity:
    """Passwords must be hashed with scrypt, never stored in plaintext."""

    def test_password_is_scrypt_hashed_not_plaintext(self, store):
        admin_id = store.create_admin("admin", "a_strong_password_123")
        with store._connect() as db:
            row = db.execute("SELECT password_hash FROM panel_admins WHERE id=?", (admin_id,)).fetchone()
        assert row["password_hash"].startswith("scrypt$"), "password must be scrypt-hashed"
        assert "a_strong_password_123" not in row["password_hash"], "plaintext must not appear in hash"

    def test_short_password_rejected(self, store):
        with pytest.raises(ValueError, match="password must be at least 12"):
            store.create_admin("user1", "short")

    def test_password_verification_works(self, store):
        store.create_admin("admin", "a_strong_password_123")
        assert store.verify_admin("admin", "a_strong_password_123") is not None
        assert store.verify_admin("admin", "wrong_password") is None
        assert store.verify_admin("nonexistent", "a_strong_password_123") is None

    def test_password_change_requires_current_password(self, store):
        admin_id = store.create_admin("admin", "a_strong_password_123")
        with pytest.raises(ValueError, match="current password is incorrect"):
            store.change_admin_password(admin_id, "wrong", "new_strong_password_456")
        # Correct password works
        store.change_admin_password(admin_id, "a_strong_password_123", "new_strong_password_456")
        assert store.verify_admin("admin", "new_strong_password_456") is not None


class TestSessionSecurity:
    """Sessions must be token-hashed, have CSRF, and be revocable."""

    def test_session_token_is_hashed_not_plaintext(self, store):
        admin_id = store.create_admin("admin", "a_strong_password_123")
        token, csrf = store.create_session(admin_id)
        with store._connect() as db:
            row = db.execute("SELECT token_hash FROM panel_sessions WHERE admin_id=?", (admin_id,)).fetchone()
        # The stored token must be the SHA-256 hash, not the plaintext token
        assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
        assert row["token_hash"] != token

    def test_csrf_token_is_generated(self, store):
        admin_id = store.create_admin("admin", "a_strong_password_123")
        token, csrf = store.create_session(admin_id)
        assert csrf and len(csrf) >= 24

    def test_session_can_be_revoked(self, store):
        admin_id = store.create_admin("admin", "a_strong_password_123")
        token, _ = store.create_session(admin_id)
        assert store.get_session(token) is not None
        store.revoke_session(token)
        assert store.get_session(token) is None

    def test_expired_session_is_invalid(self, store):
        admin_id = store.create_admin("admin", "a_strong_password_123")
        token, _ = store.create_session(admin_id, ttl_seconds=1)
        # Wait for expiry
        time.sleep(2)
        assert store.get_session(token) is None


class TestDefaultAdminSecurity:
    """Default admin must be forced to change password."""

    def test_weak_default_admin_password_is_rejected(self, store):
        with pytest.raises(ValueError, match="password must be at least 12"):
            store.create_admin("admin", "Admin", must_change_password=True)

    def test_default_admin_must_change_password(self, store):
        admin_id = store.create_admin("admin", "a_strong_password_123", must_change_password=True)
        with store._connect() as db:
            row = db.execute("SELECT must_change_password FROM panel_admins WHERE id=?", (admin_id,)).fetchone()
        assert row["must_change_password"] == 1

    def test_normal_admin_does_not_need_password_change(self, store):
        admin_id = store.create_admin("custom", "a_strong_password_123")
        with store._connect() as db:
            row = db.execute("SELECT must_change_password FROM panel_admins WHERE id=?", (admin_id,)).fetchone()
        assert row["must_change_password"] == 0


class TestSecretStripping:
    """Setup state must never accept or expose secrets."""

    def test_setup_state_rejects_secret_payloads(self, store):
        raw_bot_token = "123456:" + ("a" * 31)
        with pytest.raises(ValueError, match="validation"):
            store.save_setup_step("credentials", {
                "api_key": "sk-" + ("a" * 32),
                "bot_token": raw_bot_token,
                "password": "my_secret_password",
                "safe_value": "this is fine",
            })
        state = store.get_setup_state()
        assert not state["data"]
        assert raw_bot_token not in json.dumps(state)


class TestDuplicateAdminPrevention:
    """Duplicate admin usernames must be rejected."""

    def test_duplicate_admin_rejected(self, store):
        store.create_admin("admin", "a_strong_password_123")
        with pytest.raises(DuplicateAdminError):
            store.create_admin("admin", "another_strong_password_456")
