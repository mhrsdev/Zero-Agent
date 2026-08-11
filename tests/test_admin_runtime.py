import pytest

from zero.tenancy import GroupState
from zero.tenancy.registry import TenancyRegistry


def test_group_admin_lifecycle_and_legacy_precedence(tmp_path):
    from zero.admin import GroupAdminError, GroupAdminService, active_group_chat_ids, group_is_allowed

    registry = TenancyRegistry(tmp_path / "tenancy.db")
    admin = GroupAdminService(registry, installation_id="install-a", owner_user_id=42)
    group = admin.add_group(-100123, title="Managed group")
    assert group.group_id == "telegram:-100123"
    assert group.state is GroupState.ACTIVE
    assert group_is_allowed(registry, "install-a", -100123, legacy_allowed=False) is True
    assert active_group_chat_ids(registry, "install-a", legacy_ids=[]) == [-100123]

    assert admin.add_group(-100123, title="Updated title").state is GroupState.ACTIVE
    admin.disable_group(-100123)
    assert group_is_allowed(registry, "install-a", -100123, legacy_allowed=True) is False
    assert active_group_chat_ids(registry, "install-a", legacy_ids=[-100123]) == []

    admin.add_group(-100123)
    with pytest.raises(GroupAdminError):
        admin.remove_group(-100123, confirmed=False)
    removed = admin.remove_group(-100123, confirmed=True)
    assert removed.state is GroupState.ARCHIVED
    assert group_is_allowed(registry, "install-a", -100123, legacy_allowed=True) is False
    with pytest.raises(GroupAdminError):
        admin.add_group(-100123)


def test_group_admin_sets_all_limits_in_shared_registry(tmp_path):
    from zero.admin import GroupAdminService
    from zero.tenancy import Scope

    registry = TenancyRegistry(tmp_path / "tenancy.db")
    admin = GroupAdminService(registry, installation_id="install-a", owner_user_id=42)
    group = admin.add_group(-777)
    limits = {"hour": 4, "day": 20, "week": 80, "month": 300}
    admin.set_reply_limits(-777, limits)
    scope = Scope("install-a", group.group_id)
    assert registry.quotas(scope, "human_replies") == limits


def test_runtime_session_resolution_uses_active_registry_only(tmp_path):
    from pathlib import Path
    from zero.sessions import SessionRegistry
    from zero.admin import resolve_listener_session_path

    root = tmp_path / "sessions"
    assert resolve_listener_session_path("/legacy/account", session_root=root) == Path("/legacy/account")
    registry = SessionRegistry(root)
    record = registry.add("second")
    Path(str(record.session_path) + ".session").write_bytes(b"credential")
    registry.mark_authorized("second", user_id=7, username="account")
    registry.activate("second")
    assert resolve_listener_session_path("/legacy/account", session_root=root) == record.session_path
