from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from .storage import ZeroStore

WELCOME_DEFAULT = False
INACTIVE_DEFAULT = False
LEAVE_DM_DEFAULT = False
INACTIVE_DAYS_DEFAULT = 3
INACTIVE_DAILY_LIMIT_DEFAULT = 2
INACTIVE_USER_COOLDOWN_SECONDS = 7 * 86400
MIN_ACTIVE_MESSAGES = 3

_OPT_OUT_RE = re.compile(r'(?:مزاحم\s*نشو|تگ\s*(?:نکن|نکنید)|منو\s*تگ\s*نکن|پینگ\s*نکن)', re.IGNORECASE)
_SENSITIVE_RE = re.compile(r'(?:دعوا|درگیری|جنگ|مرگ|فوت|خودکشی|سوگ|بیمارستان|تهدید|خشونت)', re.IGNORECASE)


def parse_social_command(parts: list[str]) -> tuple[str, int | None]:
    args = [part.lower() for part in parts]
    if not args or args == ['status']:
        return 'status', None
    if len(args) == 2 and args[0] == 'welcome' and args[1] in {'on', 'off'}:
        return f'welcome_{args[1]}', None
    if len(args) == 2 and args[0] == 'inactive' and args[1] in {'on', 'off'}:
        return f'inactive_{args[1]}', None
    if len(args) == 3 and args[:2] == ['inactive', 'days']:
        try:
            days = int(args[2])
        except ValueError as exc:
            raise ValueError('days باید عدد صحیح باشد.') from exc
        if not 1 <= days <= 90:
            raise ValueError('days باید بین 1 و 90 باشد.')
        return 'inactive_days', days
    if len(args) == 2 and args[0] == 'leave-dm' and args[1] in {'on', 'off'}:
        return f'leave_dm_{args[1]}', None
    raise ValueError('Usage: /zero social [status|welcome on/off|inactive on/off|inactive days <1-90>|leave-dm on/off]')


def is_social_optout_text(text: str) -> bool:
    return bool(_OPT_OUT_RE.search(text or ''))


def is_sensitive_social_context(text: str) -> bool:
    return bool(_SENSITIVE_RE.search(text or ''))


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


@dataclass(frozen=True)
class InactivePing:
    user_id: int
    label: str
    text: str


class SocialService:
    """DB-backed, low-volume social behavior. Sending remains in the listener."""

    def __init__(self, store: ZeroStore):
        self.store = store

    async def status(self) -> dict[str, int | bool]:
        return {
            'welcome_enabled': await self.enabled('welcome_enabled', WELCOME_DEFAULT),
            'inactive_ping_enabled': await self.enabled('inactive_ping_enabled', INACTIVE_DEFAULT),
            'inactive_days_threshold': await self.int_setting('inactive_days_threshold', INACTIVE_DAYS_DEFAULT),
            'inactive_ping_daily_limit': await self.int_setting('inactive_ping_daily_limit', INACTIVE_DAILY_LIMIT_DEFAULT),
            'leave_dm_enabled': await self.enabled('leave_dm_enabled', LEAVE_DM_DEFAULT),
        }

    async def enabled(self, key: str, default: bool) -> bool:
        return _as_bool(await self.store.get_setting(key), default)

    async def int_setting(self, key: str, default: int) -> int:
        raw = await self.store.get_setting(key)
        try:
            return int(raw) if raw is not None else default
        except ValueError:
            return default

    async def welcome_text(self, chat_id: int, users: list[tuple[int, str]], *, now: int | None = None) -> tuple[str | None, str]:
        if not await self.enabled('welcome_enabled', WELCOME_DEFAULT):
            return None, 'disabled'
        names: list[str] = []
        for user_id, label in users:
            if await self.store.claim_group_welcome(user_id, chat_id, now=now):
                names.append(label)
        if not names:
            return None, 'already_welcomed'
        safe_names = names[:5]
        suffix = ' و بقیه' if len(names) > len(safe_names) else ''
        if len(safe_names) == 1:
            return f'{safe_names[0]} خوش اومدی 🌱', 'ready'
        return f"{'، '.join(safe_names)}{suffix} خوش اومدین 🌱", 'ready'

    async def next_inactive_ping(self, chat_id: int, recent_text: str, *, now: int | None = None) -> tuple[InactivePing | None, str]:
        now = int(now or time.time())
        if not await self.enabled('inactive_ping_enabled', INACTIVE_DEFAULT):
            return None, 'disabled'
        if is_sensitive_social_context(recent_text):
            return None, 'sensitive_context'
        limit = max(1, await self.int_setting('inactive_ping_daily_limit', INACTIVE_DAILY_LIMIT_DEFAULT))
        used = await self.store.count_rate_events(chat_id, 'inactive_ping', 86400)
        if used >= limit:
            return None, 'daily_limit'
        days = max(1, await self.int_setting('inactive_days_threshold', INACTIVE_DAYS_DEFAULT))
        rows = await self.store.list_inactive_group_users(
            chat_id,
            inactive_before=now - days * 86400,
            min_messages=MIN_ACTIVE_MESSAGES,
            last_ping_before=now - INACTIVE_USER_COOLDOWN_SECONDS,
            limit=10,
        )
        for row in rows:
            label = (row.get('label') or '').strip()
            if label.startswith('@') and len(label) > 1 and ' ' not in label:
                return InactivePing(int(row['user_id']), label, f'{label} چند روزه کم‌پیدایی، اوکی‌ای؟'), 'ready'
        return None, 'no_eligible_user'

    async def record_inactive_ping(self, user_id: int, chat_id: int, *, now: int | None = None) -> None:
        await self.store.mark_inactive_ping(user_id, chat_id, now=now)
        await self.store.add_rate_event(chat_id, 'inactive_ping')

    async def leave_dm_allowed(self, user_id: int, chat_id: int) -> tuple[bool, str]:
        if not await self.enabled('leave_dm_enabled', LEAVE_DM_DEFAULT):
            return False, 'disabled'
        if await self.store.group_user_social_opt_out(user_id, chat_id):
            return False, 'social_opt_out'
        if not await self.store.user_dm_allowed_for_group(user_id, chat_id):
            return False, 'dm_not_allowed'
        if await self.store.count_rate_events(user_id, 'leave_dm_followup', 10 * 365 * 86400):
            return False, 'already_sent'
        return True, 'ready'

    @staticmethod
    def leave_dm_text() -> str:
        return 'دیدم از گروه رفتی. اگه چیزی ناراحتت کرده، امیدوارم اوکی باشی. هر وقت خواستی برگردی، خوشحال می‌شیم ببینیمت.'
