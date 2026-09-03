"""Per-group options layered on top of installation defaults.

Missing settings inherit from ZeroConfig / code defaults. Unknown keys never
fail-open: loaders ignore them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .tenancy.models import Scope

REPLY_MODES = frozenset({"mention_or_reply", "mention_only", "always_allowed", "silent"})
LANGUAGES = frozenset({"auto", "fa", "en", "mix"})
REPLY_PROFILES = frozenset({"compact", "normal", "long"})
INJECT_DEPTHS = frozenset({"off", "light", "standard", "deep"})


@dataclass(frozen=True, slots=True)
class QuietHours:
    start: str  # "HH:MM"
    end: str
    timezone: str = "Asia/Tehran"

    def active(self, now: datetime | None = None) -> bool:
        try:
            zone = ZoneInfo(self.timezone)
        except Exception:
            zone = ZoneInfo("UTC")
        stamp = now.astimezone(zone) if now is not None else datetime.now(zone)
        current = stamp.hour * 60 + stamp.minute

        def minutes(value: str) -> int:
            hour, minute = value.split(":", 1)
            return int(hour) * 60 + int(minute)

        try:
            start, end = minutes(self.start), minutes(self.end)
        except (TypeError, ValueError):
            return False
        if start == end:
            return False
        if start < end:
            return start <= current < end
        return current >= start or current < end


@dataclass(frozen=True, slots=True)
class GroupPolicy:
    enabled: bool = True
    reply_mode: str = "mention_or_reply"
    language: str = "auto"
    quiet_hours: QuietHours | None = None
    reply_profile: str = "compact"
    persona: str | None = None
    provider_profile: str | None = None
    memory_enabled: bool = True
    memory_inject_depth: str = "standard"
    web_search_enabled: bool | None = None
    automation_reactions: bool = True
    automation_interject: bool = True
    automation_proactive: bool = False
    automation_stickers: bool = True
    automation_gifs: bool = True
    office_enabled: bool = False
    custom_style: str = ""
    observe_only: bool = False
    max_replies_per_hour: int | None = None
    daily_budget_usd: float | None = None
    forum_topics: dict[str, list[int]] | None = None


def _as_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _as_int(value: Any, default: int | None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float | None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _forum_topics(value: Any, default: dict[str, list[int]] | None) -> dict[str, list[int]] | None:
    if value in (None, "", False):
        return default
    if not isinstance(value, dict):
        return default
    parsed: dict[str, list[int]] = {}
    for key in ("allow", "deny"):
        raw = value.get(key)
        if not isinstance(raw, list):
            continue
        ids: list[int] = []
        for item in raw:
            try:
                ids.append(int(item))
            except (TypeError, ValueError):
                continue
        parsed[key] = ids
    return parsed or default


def _quiet_hours(value: Any) -> QuietHours | None:
    if not value:
        return None
    if isinstance(value, QuietHours):
        return value
    if not isinstance(value, dict):
        return None
    start, end = value.get("start"), value.get("end")
    if not start or not end:
        return None
    return QuietHours(str(start), str(end), str(value.get("timezone") or "Asia/Tehran"))


def load_group_policy(registry: Any, scope: Scope, *, defaults: GroupPolicy | None = None) -> GroupPolicy:
    base = defaults or GroupPolicy()
    settings = registry.settings(scope) if registry is not None else {}
    reply_mode = str(settings.get("reply_mode") or base.reply_mode)
    if reply_mode not in REPLY_MODES:
        reply_mode = base.reply_mode
    language = str(settings.get("language") or base.language)
    if language not in LANGUAGES:
        language = base.language
    reply_profile = str(settings.get("reply_profile") or base.reply_profile)
    if reply_profile not in REPLY_PROFILES:
        reply_profile = base.reply_profile
    inject = str(settings.get("memory_inject_depth") or base.memory_inject_depth)
    if inject not in INJECT_DEPTHS:
        inject = base.memory_inject_depth
    style = str(settings.get("custom_style") or base.custom_style)[:400]
    return GroupPolicy(
        enabled=_as_bool(settings.get("enabled"), base.enabled),
        reply_mode=reply_mode,
        language=language,
        quiet_hours=_quiet_hours(settings.get("quiet_hours")) or base.quiet_hours,
        reply_profile=reply_profile,
        persona=settings.get("persona") if settings.get("persona") not in {None, ""} else base.persona,
        provider_profile=settings.get("provider_profile") or base.provider_profile,
        memory_enabled=_as_bool(settings.get("memory_enabled"), base.memory_enabled),
        memory_inject_depth=inject,
        web_search_enabled=_as_bool(settings.get("web_search_enabled"), True) if "web_search_enabled" in settings else base.web_search_enabled,
        automation_reactions=_as_bool(settings.get("automation_reactions"), base.automation_reactions),
        automation_interject=_as_bool(settings.get("automation_interject"), base.automation_interject),
        automation_proactive=_as_bool(settings.get("automation_proactive"), base.automation_proactive),
        automation_stickers=_as_bool(settings.get("automation_stickers"), base.automation_stickers),
        automation_gifs=_as_bool(settings.get("automation_gifs"), base.automation_gifs),
        office_enabled=_as_bool(settings.get("office_enabled"), base.office_enabled),
        custom_style=style,
        observe_only=_as_bool(settings.get("observe_only"), base.observe_only),
        max_replies_per_hour=_as_int(settings.get("max_replies_per_hour"), base.max_replies_per_hour),
        daily_budget_usd=_as_float(settings.get("daily_budget_usd"), base.daily_budget_usd),
        forum_topics=_forum_topics(settings.get("forum_topics"), base.forum_topics),
    )
