from datetime import datetime, timezone

import pytest

from zero.tenancy import GroupState, Role, Scope, TenancyRegistry


def _active_group(registry, installation_id, group_id, chat_id, owner_id=101):
    group = registry.discover_group(installation_id, group_id, platform_chat_id=chat_id, title=group_id)
    scope = Scope(installation_id, group_id, owner_id)
    registry.add_member(scope, owner_id, Role.OWNER)
    registry.set_group_state(scope, GroupState.ACTIVE)
    return scope


def test_quota_periods_have_exact_utc_calendar_buckets():
    bucket = TenancyRegistry._bucket
    monday = datetime(2024, 12, 30, 12, tzinfo=timezone.utc).timestamp()
    sunday = datetime(2025, 1, 5, 23, tzinfo=timezone.utc).timestamp()
    next_monday = datetime(2025, 1, 6, 0, tzinfo=timezone.utc).timestamp()
    assert bucket("hour", monday) == "2024-12-30T12"
    assert bucket("day", monday) == "2024-12-30"
    assert bucket("week", monday) == "2025-W01"
    assert bucket("week", sunday) == "2025-W01"
    assert bucket("week", next_monday) == "2025-W02"
    assert bucket("month", monday) == "2024-12"
    with pytest.raises(ValueError):
        bucket("year", monday)


def test_multi_period_quota_is_atomic_and_validated(tmp_path):
    registry = TenancyRegistry(tmp_path / "tenancy.db")
    scope = _active_group(registry, "install-a", "telegram-100", -100)
    registry.set_quotas(scope, "human_replies", {"hour": 5, "day": 5, "week": 5, "month": 0})
    decision = registry.consume_quotas(scope, "human_replies")
    assert decision.allowed is False
    assert decision.blocked_period == "month"
    assert registry.usage(scope, "human_replies", period="hour") == 0

    registry.set_quota(scope, "human_replies", 1, period="month")
    assert registry.consume_quotas(scope, "human_replies").allowed is True
    blocked = registry.consume_quotas(scope, "human_replies")
    assert blocked.allowed is False
    assert blocked.blocked_period == "month"
    assert registry.usage(scope, "human_replies", period="hour") == 1

    with pytest.raises(ValueError):
        registry.set_quota(scope, "human_replies", -1, period="day")
    with pytest.raises(ValueError):
        registry.set_quota(scope, "human_replies", 1, period="year")


def test_group_limits_are_independent_and_readable(tmp_path):
    registry = TenancyRegistry(tmp_path / "tenancy.db")
    first = _active_group(registry, "install-a", "telegram-101", -101)
    second = _active_group(registry, "install-a", "telegram-202", -202)
    limits = {"hour": 1, "day": 2, "week": 3, "month": 4}
    registry.set_quotas(first, "human_replies", limits)
    registry.set_quotas(second, "human_replies", {key: 9 for key in limits})
    assert registry.quotas(first, "human_replies") == limits
    assert registry.quotas(second, "human_replies") == {key: 9 for key in limits}
    assert registry.consume_quotas(first, "human_replies").allowed is True
    assert registry.consume_quotas(first, "human_replies").allowed is False
    assert registry.consume_quotas(second, "human_replies").allowed is True
