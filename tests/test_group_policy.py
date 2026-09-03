from datetime import datetime
from zoneinfo import ZoneInfo

from zero.group_policy import QuietHours, load_group_policy
from zero.tenancy import GroupState, Role, Scope, TenancyRegistry


def test_quiet_hours_wrap_midnight():
    hours = QuietHours("22:00", "07:00", timezone="UTC")
    night = datetime(2026, 1, 1, 23, 0, tzinfo=ZoneInfo("UTC"))
    morning = datetime(2026, 1, 2, 8, 0, tzinfo=ZoneInfo("UTC"))
    assert hours.active(night) is True
    assert hours.active(morning) is False


def test_group_policy_defaults_and_overrides(tmp_path):
    registry = TenancyRegistry(tmp_path / "t.db")
    registry.discover_group("local", "g1", platform_chat_id=-1)
    scope = Scope("local", "g1", 1)
    registry.add_member(scope, 1, Role.OWNER)
    registry.set_group_state(scope, GroupState.ACTIVE)
    policy = load_group_policy(registry, scope)
    assert policy.enabled is True
    assert policy.reply_mode == "mention_or_reply"
    registry.set_setting(scope, "reply_mode", "mention_only")
    registry.set_setting(scope, "language", "fa")
    registry.set_setting(scope, "quiet_hours", {"start": "01:00", "end": "02:00", "timezone": "UTC"})
    policy = load_group_policy(registry, scope)
    assert policy.reply_mode == "mention_only"
    assert policy.language == "fa"
    assert policy.quiet_hours is not None
