"""Adversarial multi-group isolation.

These tests are written from the attacker's side: they assert that one group
cannot read, spend, configure or deliver into another, that the same human in
two groups carries two independent sets of rights, and that a forum topic is a
distinct delivery destination rather than a detail of the group.
"""
from __future__ import annotations

import pytest

from zero.tenancy import (
    GroupState,
    GroupStateError,
    Permission,
    PermissionDenied,
    Role,
    Scope,
    ScopeViolation,
    TenancyRegistry,
)


@pytest.fixture
def registry(tmp_path):
    return TenancyRegistry(tmp_path / "tenancy.db")


def approved(registry, installation, group, chat_id, owner=1):
    """Discover, seat an owner, and approve a group into serving state."""
    registry.discover_group(installation, group, platform_chat_id=chat_id, title=group)
    scope = Scope(installation, group, owner)
    registry.add_member(scope, owner, Role.OWNER, actor_id=owner, bootstrap=True)
    registry.set_group_state(scope, GroupState.ACTIVE)
    return scope


# ---- scope identity ----------------------------------------------------


def test_scope_rejects_empty_or_malformed_tenant_identifiers():
    for bad in ("", " ", "../etc", "a/b"):
        with pytest.raises(ValueError):
            Scope(bad, "g1")
        with pytest.raises(ValueError):
            Scope("i1", bad)


def test_scope_of_two_groups_never_owns_the_other():
    a, b = Scope("inst", "group-a", 7), Scope("inst", "group-b", 7)
    assert not a.owns(b) and not b.owns(a)
    with pytest.raises(ScopeViolation):
        a.assert_owns(b)


def test_same_group_id_in_two_installations_is_a_different_tenant():
    a, b = Scope("inst-1", "shared", 7), Scope("inst-2", "shared", 7)
    assert not a.owns(b)
    with pytest.raises(ScopeViolation):
        a.assert_owns(b)


def test_thread_is_part_of_the_delivery_destination_not_the_tenant():
    base = Scope("inst", "g1", 7)
    topic = base.with_thread(42)
    assert topic.thread_id == 42 and base.thread_id is None
    # Same tenant, so ownership holds; the thread only narrows delivery.
    assert base.owns(topic)


# ---- group lifecycle ---------------------------------------------------


def test_discovered_group_is_pending_and_serves_no_traffic(registry):
    registry.discover_group("inst", "g1", platform_chat_id=-100)
    scope = Scope("inst", "g1", 1)
    assert registry.get_group("inst", "g1").state is GroupState.PENDING
    with pytest.raises(GroupStateError):
        registry.require_serving(scope)


def test_approval_requires_manage_group_state_permission(registry):
    registry.discover_group("inst", "g1", platform_chat_id=-100)
    stranger = Scope("inst", "g1", 999)
    with pytest.raises(PermissionDenied):
        registry.set_group_state(stranger, GroupState.ACTIVE)
    # A plain member still cannot approve.
    registry.add_member(stranger, 999, Role.MEMBER, actor_id=999, bootstrap=True)
    with pytest.raises(PermissionDenied):
        registry.set_group_state(stranger, GroupState.ACTIVE)
    assert registry.get_group("inst", "g1").state is GroupState.PENDING


def test_illegal_lifecycle_transitions_are_rejected(registry):
    scope = approved(registry, "inst", "g1", -100)
    registry.set_group_state(scope, GroupState.ARCHIVED)
    # Nothing leaves archived.
    for target in (GroupState.ACTIVE, GroupState.DISABLED, GroupState.PENDING):
        with pytest.raises(GroupStateError):
            registry.set_group_state(scope, target)


def test_disabled_group_stops_serving_but_can_be_reactivated(registry):
    scope = approved(registry, "inst", "g1", -100)
    registry.require_serving(scope)
    registry.set_group_state(scope, GroupState.DISABLED)
    with pytest.raises(GroupStateError):
        registry.require_serving(scope)
    registry.set_group_state(scope, GroupState.ACTIVE)
    assert registry.require_serving(scope).serving


# ---- membership isolation ----------------------------------------------


def test_same_user_holds_independent_roles_in_two_groups(registry):
    a = approved(registry, "inst", "group-a", -100, owner=1)
    b = approved(registry, "inst", "group-b", -200, owner=2)
    registry.add_member(a, 7, Role.ADMIN, actor_id=a.user_id)
    registry.add_member(b, 7, Role.VIEWER, actor_id=b.user_id)

    assert registry.role_of(a, 7) is Role.ADMIN
    assert registry.role_of(b, 7) is Role.VIEWER
    assert registry.has(a, Permission.MANAGE_SETTINGS, 7)
    assert not registry.has(b, Permission.MANAGE_SETTINGS, 7)
    # A viewer cannot even write memory in the second group.
    assert not registry.has(b, Permission.WRITE_MEMORY, 7)


def test_membership_in_one_group_grants_nothing_in_another(registry):
    a = approved(registry, "inst", "group-a", -100)
    b = approved(registry, "inst", "group-b", -200, owner=2)
    registry.add_member(a, 7, Role.OWNER, actor_id=a.user_id)

    assert registry.permissions(b, 7) == frozenset()
    with pytest.raises(PermissionDenied):
        registry.require(b, Permission.READ_GROUP, 7)
    assert 7 not in registry.members(b)


def test_two_groups_have_different_administrators(registry):
    a = approved(registry, "inst", "group-a", -100, owner=1)
    b = approved(registry, "inst", "group-b", -200, owner=2)
    assert registry.members(a) == {1: Role.OWNER}
    assert registry.members(b) == {2: Role.OWNER}
    with pytest.raises(PermissionDenied):
        registry.set_group_state(b.for_user(1), GroupState.DISABLED)


def test_removing_a_member_revokes_only_that_groups_rights(registry):
    a = approved(registry, "inst", "group-a", -100)
    b = approved(registry, "inst", "group-b", -200, owner=2)
    registry.add_member(a, 7, Role.MEMBER, actor_id=a.user_id)
    registry.add_member(b, 7, Role.MEMBER, actor_id=b.user_id)
    registry.remove_member(a, 7, actor_id=a.user_id)
    assert registry.role_of(a, 7) is None
    assert registry.role_of(b, 7) is Role.MEMBER


# ---- per-group policy --------------------------------------------------


def test_persona_provider_and_tool_policy_are_per_group(registry):
    a = approved(registry, "inst", "group-a", -100)
    b = approved(registry, "inst", "group-b", -200, owner=2)
    registry.set_setting(a, "persona", "concise")
    registry.set_setting(a, "provider_profile", "fast-profile")
    registry.set_setting(a, "tool_policy", {"web_search": True})
    registry.set_setting(b, "persona", "verbose")
    registry.set_setting(b, "provider_profile", "quality-profile")
    registry.set_setting(b, "tool_policy", {"web_search": False})

    assert registry.get_setting(a, "persona") == "concise"
    assert registry.get_setting(b, "persona") == "verbose"
    assert registry.get_setting(a, "provider_profile") != registry.get_setting(b, "provider_profile")
    assert registry.get_setting(a, "tool_policy")["web_search"] is True
    assert registry.get_setting(b, "tool_policy")["web_search"] is False


def test_settings_are_not_readable_across_groups(registry):
    a = approved(registry, "inst", "group-a", -100)
    b = approved(registry, "inst", "group-b", -200, owner=2)
    registry.set_setting(a, "persona", "secret-persona")
    assert registry.get_setting(b, "persona") is None
    assert registry.settings(b) == {}


def test_unknown_setting_keys_are_rejected(registry):
    a = approved(registry, "inst", "group-a", -100)
    with pytest.raises(ValueError):
        registry.set_setting(a, "arbitrary_key", "x")


def test_member_cannot_change_group_settings(registry):
    a = approved(registry, "inst", "group-a", -100)
    registry.add_member(a, 7, Role.MEMBER, actor_id=a.user_id)
    with pytest.raises(PermissionDenied):
        registry.set_setting(a, "persona", "hijacked", actor_id=7)


# ---- quotas and usage --------------------------------------------------


def test_quotas_are_independent_and_one_group_cannot_exhaust_another(registry):
    a = approved(registry, "inst", "group-a", -100)
    b = approved(registry, "inst", "group-b", -200, owner=2)
    registry.set_quota(a, "llm_calls", 2, actor_id=a.user_id)
    registry.set_quota(b, "llm_calls", 5, actor_id=b.user_id)

    assert registry.consume(a, "llm_calls")[0] is True
    assert registry.consume(a, "llm_calls")[0] is True
    allowed, used, limit = registry.consume(a, "llm_calls")
    assert allowed is False and used == 2 and limit == 2

    # group-b is untouched by group-a exhausting its quota.
    assert registry.usage(b, "llm_calls") == 0
    assert registry.consume(b, "llm_calls")[0] is True


def test_usage_is_recorded_per_group(registry):
    a = approved(registry, "inst", "group-a", -100)
    b = approved(registry, "inst", "group-b", -200, owner=2)
    registry.consume(a, "llm_calls", amount=3)
    assert registry.usage(a, "llm_calls") == 3
    assert registry.usage(b, "llm_calls") == 0


def test_unlimited_resource_still_records_usage(registry):
    a = approved(registry, "inst", "group-a", -100)
    allowed, used, limit = registry.consume(a, "messages")
    assert allowed is True and used == 1 and limit is None


# ---- identity ----------------------------------------------------------


def test_identity_history_does_not_cross_groups(registry):
    a = approved(registry, "inst", "group-a", -100)
    b = approved(registry, "inst", "group-b", -200, owner=2)
    registry.record_identity(a, 7, "@work-handle")
    registry.record_identity(b, 7, "@personal-handle")
    assert registry.identity_history(a, 7) == ["@work-handle"]
    assert registry.identity_history(b, 7) == ["@personal-handle"]


# ---- chat resolution ---------------------------------------------------


def test_chat_id_resolves_to_its_owning_group_only(registry):
    approved(registry, "inst", "group-a", -100)
    approved(registry, "inst", "group-b", -200, owner=2)
    resolved = registry.resolve_scope("inst", platform_chat_id=-200, user_id=7, thread_id=5)
    assert resolved.group_id == "group-b"
    assert resolved.thread_id == 5
    with pytest.raises(GroupStateError):
        registry.resolve_scope("inst", platform_chat_id=-999)


def test_the_same_chat_id_in_another_installation_is_not_visible(registry):
    approved(registry, "inst-1", "group-a", -100)
    registry.create_installation("inst-2")
    with pytest.raises(GroupStateError):
        registry.resolve_scope("inst-2", platform_chat_id=-100)


def test_concurrent_scoped_writes_stay_in_their_own_group(registry):
    """Interleaved work for two groups must not blend."""
    a = approved(registry, "inst", "group-a", -100)
    b = approved(registry, "inst", "group-b", -200, owner=2)
    for index in range(20):
        registry.consume(a if index % 2 == 0 else b, "llm_calls")
        registry.record_identity(a if index % 2 == 0 else b, 7, f"label-{index}")

    assert registry.usage(a, "llm_calls") == 10
    assert registry.usage(b, "llm_calls") == 10
    assert len(registry.identity_history(a, 7)) == 10
    assert len(registry.identity_history(b, 7)) == 10
    assert set(registry.identity_history(a, 7)).isdisjoint(registry.identity_history(b, 7))


def test_registry_exposes_no_unscoped_row_accessor():
    """Every accessor must take a Scope or an explicit installation id.

    An unscoped reader would be the easiest way to reintroduce cross-tenant
    leakage, so its absence is asserted rather than assumed.
    """
    import inspect

    exempt = {"create_installation", "installations", "discover_group", "get_group", "groups", "resolve_scope"}
    for name, member in inspect.getmembers(TenancyRegistry, inspect.isfunction):
        if name.startswith("_") or name in exempt:
            continue
        parameters = list(inspect.signature(member).parameters)
        assert parameters[:2] == ["self", "scope"], f"{name} must take a Scope as its first argument"


# ---- memory boundary enforcement ---------------------------------------


def test_bound_memory_service_rejects_another_groups_chat(registry, tmp_path):
    """A scoped MemoryService must refuse a message from a foreign chat."""
    import asyncio
    from types import SimpleNamespace

    from zero.core.memory_service import MemoryService
    from zero.memory_v3 import MemoryV3Service

    a = approved(registry, "inst", "group-a", -100)
    approved(registry, "inst", "group-b", -200, owner=2)
    registry.add_member(a, 7, Role.MEMBER, actor_id=a.user_id)

    service = MemoryService(MemoryV3Service(str(tmp_path / "v3.db"))).bind(a.for_user(7), registry)
    own = SimpleNamespace(chat_id=-100, sender_id=7, trace_id="t", message_id=1)
    foreign = SimpleNamespace(chat_id=-200, sender_id=7, trace_id="t", message_id=2)

    asyncio.run(service.context(own))  # same tenant: allowed
    with pytest.raises(ScopeViolation):
        asyncio.run(service.context(foreign))
    with pytest.raises(ScopeViolation):
        asyncio.run(service.observe(foreign))
    with pytest.raises(ScopeViolation):
        asyncio.run(service.record_message(foreign))


def test_bound_memory_service_enforces_memory_permissions(registry, tmp_path):
    import asyncio
    from types import SimpleNamespace

    from zero.core.memory_service import MemoryService
    from zero.memory_v3 import MemoryV3Service

    a = approved(registry, "inst", "group-a", -100)
    registry.add_member(a, 7, Role.VIEWER, actor_id=a.user_id)  # viewers may not touch memory
    service = MemoryService(MemoryV3Service(str(tmp_path / "v3.db"))).bind(a.for_user(7), registry)
    message = SimpleNamespace(chat_id=-100, sender_id=7, trace_id="t", message_id=1)

    with pytest.raises(PermissionDenied):
        asyncio.run(service.context(message))
    with pytest.raises(PermissionDenied):
        asyncio.run(service.observe(message))

    registry.add_member(a, 7, Role.MEMBER, actor_id=a.user_id)
    asyncio.run(service.context(message))


def test_bound_memory_service_rejects_a_foreign_scoped_item(registry, tmp_path):
    import asyncio

    from zero.core.memory_service import MemoryService
    from zero.memory_v3 import MemoryV3Item, MemoryV3Service

    a = approved(registry, "inst", "group-a", -100)
    approved(registry, "inst", "group-b", -200, owner=2)
    registry.add_member(a, 7, Role.MEMBER, actor_id=a.user_id)
    service = MemoryService(MemoryV3Service(str(tmp_path / "v3.db"))).bind(a.for_user(7), registry)

    asyncio.run(service.put(MemoryV3Item.group(chat_id=-100, content="ours", kind="fact")))
    with pytest.raises(ScopeViolation):
        asyncio.run(service.put(MemoryV3Item.group(chat_id=-200, content="theirs", kind="fact")))


def test_unbound_memory_service_keeps_single_tenant_behaviour(tmp_path):
    """Existing composition roots must keep working while they migrate."""
    import asyncio
    from types import SimpleNamespace

    from zero.core.memory_service import MemoryService
    from zero.memory_v3 import MemoryV3Service

    service = MemoryService(MemoryV3Service(str(tmp_path / "v3.db")))
    assert service.scope is None
    asyncio.run(service.context(SimpleNamespace(chat_id=-999, sender_id=1, trace_id="t", message_id=1)))
