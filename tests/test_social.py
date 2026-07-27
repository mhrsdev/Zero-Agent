from pathlib import Path

import pytest

from zero.social import (
    is_sensitive_social_context,
    is_social_optout_text,
    parse_social_command,
)
from zero.storage import ZeroStore


def test_social_command_parser():
    assert parse_social_command(['status']) == ('status', None)
    assert parse_social_command(['welcome', 'on']) == ('welcome_on', None)
    assert parse_social_command(['inactive', 'days', '3']) == ('inactive_days', 3)
    assert parse_social_command(['leave-dm', 'off']) == ('leave_dm_off', None)
    with pytest.raises(ValueError):
        parse_social_command(['inactive', 'days', '0'])


def test_social_optout_and_sensitive_context_detection():
    assert is_social_optout_text('لطفاً منو تگ نکن') is True
    assert is_social_optout_text('مزاحم نشو') is True
    assert is_social_optout_text('سلام خوبی؟') is False
    assert is_sensitive_social_context('امروز دعوا و بحث جدی شد') is True
    assert is_sensitive_social_context('بحث پایتون چطور بود؟') is False


@pytest.mark.asyncio
async def test_group_user_state_welcome_once_and_inactive_eligibility(tmp_path: Path):
    store = ZeroStore(str(tmp_path / 'state.db'))
    now = 2_000_000_000
    await store.touch_group_user(101, -100, '@active', now=now - 10 * 86400)
    for offset in (9, 8, 7):
        await store.record_group_user_message(101, -100, '@active', now=now - offset * 86400)

    assert await store.claim_group_welcome(101, -100, now=now) is True
    assert await store.claim_group_welcome(101, -100, now=now) is False

    eligible = await store.list_inactive_group_users(
        -100, inactive_before=now - 3 * 86400, min_messages=3, limit=5,
    )
    assert [row['user_id'] for row in eligible] == [101]

    await store.set_group_social_opt_out(101, -100, True)
    eligible = await store.list_inactive_group_users(
        -100, inactive_before=now - 3 * 86400, min_messages=3, limit=5,
    )
    assert eligible == []


@pytest.mark.asyncio
async def test_dm_permission_and_leave_state_are_persisted(tmp_path: Path):
    store = ZeroStore(str(tmp_path / 'state.db'))
    await store.touch_group_user(202, -100, '@dmuser', now=1_000)
    assert await store.user_dm_allowed_for_group(202, -100) is False
    await store.mark_user_dm_allowed(202)
    assert await store.user_dm_allowed_for_group(202, -100) is True
    await store.mark_group_user_left(202, -100, now=2_000)
    state = await store.get_group_user_state(202, -100)
    assert state['left_at'] == 2_000


@pytest.mark.asyncio
async def test_social_service_enforces_inactive_and_leave_safety(tmp_path: Path):
    from zero.social import SocialService

    store = ZeroStore(str(tmp_path / 'state.db'))
    social = SocialService(store)
    now = 2_000_000_000
    await store.set_setting('inactive_ping_enabled', 'true')
    await store.set_setting('inactive_days_threshold', '3')
    await store.set_setting('inactive_ping_daily_limit', '2')
    await store.touch_group_user(303, -100, '@olduser', now=now - 10 * 86400)
    for offset in (9, 8, 7):
        await store.record_group_user_message(303, -100, '@olduser', now=now - offset * 86400)

    ping, reason = await social.next_inactive_ping(-100, 'بحث پایتون دوستانه بود', now=now)
    assert reason == 'ready'
    assert ping and ping.user_id == 303
    await social.record_inactive_ping(303, -100, now=now)
    ping, reason = await social.next_inactive_ping(-100, 'بحث پایتون دوستانه بود', now=now)
    assert ping is None and reason == 'no_eligible_user'
    ping, reason = await social.next_inactive_ping(-100, 'دعوا شد', now=now)
    assert ping is None and reason == 'sensitive_context'

    await store.set_setting('leave_dm_enabled', 'true')
    allowed, reason = await social.leave_dm_allowed(303, -100)
    assert allowed is False and reason == 'dm_not_allowed'
    await store.mark_user_dm_allowed(303, now=now)
    allowed, reason = await social.leave_dm_allowed(303, -100)
    assert allowed is True and reason == 'ready'
