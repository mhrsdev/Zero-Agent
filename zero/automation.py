"""Global gates for every autonomous action (reactions, interjection, follow-ups).

Two independent controls:

* Kill switch -- ``ZERO_AUTOMATION_DISABLED=true`` env var or the persisted
  ``automation_enabled=false`` setting stops ALL autonomous actions immediately.
  This is the emergency stop required for incident response.
* Observe mode -- ``ZERO_PROACTIVE_OBSERVE_ONLY=true`` lets the proactive
  pipeline compute and log decisions without ever sending a message.

The kill switch fails open on storage errors: an operator must explicitly turn
it off; a transient DB failure must not silently change behaviour.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

KILL_ENV = "ZERO_AUTOMATION_DISABLED"
OBSERVE_ENV = "ZERO_PROACTIVE_OBSERVE_ONLY"
SETTING_KEY = "automation_enabled"
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _flag(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def kill_switch_active() -> bool:
    """Env-level kill switch; checked first so it works even before DB access."""
    return _flag(os.getenv(KILL_ENV))


def observe_only() -> bool:
    """When true, proactive decisions are logged but never sent."""
    return _flag(os.getenv(OBSERVE_ENV))


async def automation_disabled(store: Any = None) -> str | None:
    """Return a reason string when autonomous actions must stop, else ``None``."""
    if kill_switch_active():
        return "env_kill_switch"
    if store is None:
        return None
    try:
        value = await store.get_setting(SETTING_KEY)
    except Exception as exc:  # fail open: operator must opt out explicitly
        logger.warning("AUTOMATION_SWITCH_READ_FAILED exception_type=%s", type(exc).__name__)
        return None
    if value is not None and str(value).strip().lower() in _FALSY:
        return "setting_disabled"
    return None
