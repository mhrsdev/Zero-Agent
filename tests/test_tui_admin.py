from pathlib import Path

import pytest

from zero.admin import GroupAdminService
from zero.sessions import LoginOutcome, SessionRegistry
from zero.tenancy.registry import TenancyRegistry


class FakeAuthorizedLogin:
    async def login(self, **kwargs):
        Path(str(kwargs["session_path"]) + ".session").write_bytes(b"credential")
        return LoginOutcome("authorized", user_id=88, username="safe_account")


@pytest.mark.asyncio
async def test_tui_admin_session_actions_require_confirmation_and_safe_state(tmp_path):
    from zero.tui_admin import TUIAdmin, TUIAdminError

    sessions = SessionRegistry(tmp_path / "sessions")
    tenancy = TenancyRegistry(tmp_path / "tenancy.db")
    admin = TUIAdmin(sessions, GroupAdminService(tenancy, installation_id="install-a", owner_user_id=42))
    admin.add_session("primary", "Primary")
    outcome = await admin.login_session(
        "primary",
        adapter=FakeAuthorizedLogin(),
        api_id=123,
        api_hash="example-not-persisted",
        phone="+980000000000",
        code_provider=lambda: "12345",
        password_provider=lambda: "example-not-persisted",
    )
    assert outcome.status == "authorized"
    admin.activate_session("primary")
    with pytest.raises(TUIAdminError):
        admin.delete_session("primary", confirmation="DELETE primary")

    admin.add_session("replacement")
    replacement = sessions.get("replacement")
    Path(str(replacement.session_path) + ".session").write_bytes(b"credential")
    sessions.mark_authorized("replacement", user_id=89, username="second")
    admin.activate_session("replacement")
    with pytest.raises(TUIAdminError):
        admin.delete_session("primary", confirmation="yes")
    admin.delete_session("primary", confirmation="DELETE primary")
    assert [item.session_id for item in sessions.list()] == ["replacement"]


def test_tui_admin_group_actions_and_four_limits(tmp_path):
    from zero.tui_admin import TUIAdmin, TUIAdminError

    sessions = SessionRegistry(tmp_path / "sessions")
    tenancy = TenancyRegistry(tmp_path / "tenancy.db")
    admin = TUIAdmin(sessions, GroupAdminService(tenancy, installation_id="install-a", owner_user_id=42))
    group = admin.add_group(-1001, "Group")
    limits = admin.set_group_limits(-1001, hour=5, day=25, week=100, month=400)
    assert limits == {"hour": 5, "day": 25, "week": 100, "month": 400}
    with pytest.raises(TUIAdminError):
        admin.remove_group(-1001, confirmation="REMOVE")
    removed = admin.remove_group(-1001, confirmation="REMOVE -1001")
    assert removed.state.value == "archived"


def test_tui_renders_sessions_and_group_limits_without_session_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("ZERO_HOME", str(tmp_path / "home"))
    from zero.tui import PANEL_NAMES, render_groups, render_sessions

    sessions = SessionRegistry(tmp_path / "home" / "sessions")
    record = sessions.add("safe-id", label="Safe label")
    Path(str(record.session_path) + ".session").write_bytes(b"credential")
    sessions.mark_authorized("safe-id", user_id=77, username="safe_user")
    sessions.activate("safe-id")

    tenancy = TenancyRegistry(tmp_path / "home" / "tenancy.db")
    group_admin = GroupAdminService(tenancy, installation_id="local", owner_user_id=42)
    group_admin.add_group(-2002, title="Test group")
    group_admin.set_reply_limits(-2002, {"hour": 2, "day": 8, "week": 40, "month": 120})

    session_text = "\n".join(render_sessions())
    assert "sessions" in PANEL_NAMES
    assert "safe-id" in session_text
    assert "authorized" in session_text
    assert "safe_user" in session_text
    assert str(record.session_path) not in session_text

    group_text = "\n".join(render_groups())
    assert "human replies" in group_text.lower()
    for value in ("hour: 2", "day: 8", "week: 40", "month: 120"):
        assert value in group_text
