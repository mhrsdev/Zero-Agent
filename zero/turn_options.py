"""Pure helpers that apply existing per-group / policy options. No I/O."""
from __future__ import annotations

import re
from typing import Any

from .group_policy import GroupPolicy, QuietHours
from .models import IncomingMessage

_PROFILE_CHARS = {"compact": 900, "normal": 1800, "long": 3900}
_MEMORY_OFF_RE = re.compile(r"^\s*/?memory\s+off\s*$", re.I)
_MEMORY_FORGET_RE = re.compile(r"^\s*/?memory\s+forget(?:\s+(.+))?\s*$", re.I)


def web_allowed(policy: GroupPolicy, global_enabled: bool) -> bool:
    if policy.web_search_enabled is False:
        return False
    return bool(global_enabled)


def office_allowed(policy: GroupPolicy, global_enabled: bool) -> bool:
    return bool(global_enabled and policy.office_enabled)


def reply_char_limit_for(config: Any, text: str, policy: GroupPolicy | None = None) -> int:
    from .brain_generate import needs_long_reply

    if needs_long_reply(text or ""):
        return 3900
    profile = (policy.reply_profile if policy is not None else None) or getattr(getattr(config, "policy", None), "reply_profile", "compact")
    return int(_PROFILE_CHARS.get(str(profile), getattr(config.policy, "max_reply_chars", 900)))


def prompt_option_block(policy: GroupPolicy) -> str:
    parts: list[str] = []
    if policy.language and policy.language != "auto":
        lang = {"fa": "Persian (fa)", "en": "English", "mix": "mix of Persian and English"}.get(policy.language, policy.language)
        parts.append(f"Reply language: {lang}.")
    if policy.persona:
        parts.append(f"Persona for this group: {policy.persona}.")
    if policy.custom_style:
        parts.append(f"Style notes: {policy.custom_style[:400]}")
    if policy.reply_profile == "compact":
        parts.append("Keep the reply compact.")
    elif policy.reply_profile == "long":
        parts.append("A longer, structured reply is allowed.")
    return ("\n".join(parts) + "\n") if parts else ""


def think_prefix(config: Any) -> str:
    if getattr(getattr(config, "policy", None), "think_marker", False):
        return "در حال فکر…\n"
    return ""


def should_skip_private_memory(config: Any, message: IncomingMessage) -> bool:
    if int(message.chat_id) >= 0 and not getattr(config.memory, "remember_private", False):
        if int(message.sender_id or 0) != int(getattr(config, "owner_user_id", 0) or 0):
            return True
    return False


def parse_owner_memory_command(text: str) -> tuple[str, str] | None:
    raw = text or ""
    if _MEMORY_OFF_RE.match(raw):
        return ("off", "")
    match = _MEMORY_FORGET_RE.match(raw)
    if match:
        return ("forget", (match.group(1) or "").strip())
    return None


def quiet_hours_block_automation(policy: GroupPolicy, now=None) -> bool:
    hours: QuietHours | None = policy.quiet_hours
    return bool(hours is not None and hours.active(now))


def observe_only(policy: GroupPolicy) -> bool:
    return bool(getattr(policy, "observe_only", False))
