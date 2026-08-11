from pathlib import Path

import pytest


class FakeLoginAdapter:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def login(self, **kwargs):
        self.calls.append({key: value for key, value in kwargs.items() if key not in {"api_hash", "phone"}})
        Path(str(kwargs["session_path"]) + ".session").write_bytes(b"session")
        return self.outcome


def test_session_registry_lifecycle_and_active_delete_guard(tmp_path):
    from zero.sessions import ActiveSessionError, SessionRegistry, SessionRegistryError

    registry = SessionRegistry(tmp_path / "sessions")
    record = registry.add("primary", label="Primary account")
    assert record.state == "new"
    assert record.active is False
    assert record.managed is True

    with pytest.raises(SessionRegistryError):
        registry.add("primary")
    with pytest.raises(SessionRegistryError):
        registry.activate("primary")

    Path(str(record.session_path) + ".session").write_bytes(b"credential")
    registry.mark_authorized("primary", user_id=42, username="safe_name")
    active = registry.activate("primary")
    assert active.active is True
    assert registry.active().session_id == "primary"

    with pytest.raises(ActiveSessionError):
        registry.remove("primary", confirmed=True)
    with pytest.raises(SessionRegistryError):
        registry.remove("primary", confirmed=False)


def test_session_registry_removes_only_managed_files(tmp_path):
    from zero.sessions import SessionRegistry

    registry = SessionRegistry(tmp_path / "sessions")
    managed = registry.add("managed")
    managed_file = Path(str(managed.session_path) + ".session")
    managed_file.write_bytes(b"credential")
    registry.remove("managed", confirmed=True)
    assert not managed_file.exists()

    external = tmp_path / "external.session"
    external.write_bytes(b"external-credential")
    registry.add("external", session_path=external)
    registry.remove("external", confirmed=True)
    assert external.read_bytes() == b"external-credential"


def test_session_registry_permissions_and_active_resolution(tmp_path):
    from zero.sessions import SessionRegistry

    registry = SessionRegistry(tmp_path / "sessions")
    assert (tmp_path / "sessions").stat().st_mode & 0o777 == 0o700
    assert registry.db_path.stat().st_mode & 0o777 == 0o600
    assert registry.resolve_active_path("/legacy/session") == Path("/legacy/session")


@pytest.mark.asyncio
async def test_login_session_uses_adapter_and_persists_only_safe_status(tmp_path):
    from zero.sessions import LoginOutcome, SessionRegistry, login_session

    registry = SessionRegistry(tmp_path / "sessions")
    registry.add("login-test")
    adapter = FakeLoginAdapter(LoginOutcome("authorized", user_id=99, username="verified_user"))
    outcome = await login_session(
        registry,
        "login-test",
        adapter=adapter,
        api_id=12345,
        api_hash="secret-api-hash",
        phone="+980000000000",
        code_provider=lambda: "12345",
        password_provider=lambda: "secret-password",
    )
    assert outcome.status == "authorized"
    record = registry.get("login-test")
    assert record.state == "authorized"
    assert record.user_id == 99
    assert record.username == "verified_user"
    raw = registry.db_path.read_bytes()
    assert b"secret-api-hash" not in raw
    assert b"secret-password" not in raw
    assert b"+980000000000" not in raw
