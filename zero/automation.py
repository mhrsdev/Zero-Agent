"""Global gates for every autonomous action (reactions, interjection, follow-ups).

Two independent controls:

* Kill switch -- ``ZERO_AUTOMATION_DISABLED=true`` env var or the persisted
  ``automation_enabled`` setting stops ALL autonomous actions immediately.
  This is the emergency stop required for incident response.
* Observe mode -- ``ZERO_PROACTIVE_OBSERVE_ONLY=true`` lets the proactive
  pipeline compute and log decisions without ever sending a message.

Kill-switch semantics are fail-closed. ``automation_disabled`` returns a
reason string (meaning "block") unless the setting is *explicitly* enabled:

* explicit enabled value  -> ``None`` (normal operation)
* explicit disabled value -> ``"setting_disabled"``
* missing/empty setting   -> ``None`` (fresh installs default to enabled)
* invalid setting value   -> ``"setting_invalid"`` + warning (conservative)
* storage read error      -> ``"setting_read_error"`` (block until recovery)

An operator must therefore explicitly enable automation after an incident;
ambiguity or infrastructure failure never silently re-enables sending.
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
    """Env-level kill switch; checked first so it works even before DB access.

    Fail-closed on ambiguity: an explicitly-set but unrecognisable value
    (e.g. ``ZERO_AUTOMATION_DISABLED=maybe``) BLOCKS autonomous actions --
    an operator who typed something unexpected meant to stop things, not to
    silently re-enable them.
    """
    raw = os.getenv(KILL_ENV)
    if raw is None or not raw.strip():
        return False
    normalized = raw.strip().lower()
    if normalized in _TRUTHY:
        return True
    if normalized in _FALSY:
        return False
    logger.warning(
        "AUTOMATION_KILL_ENV_INVALID value_class=%s action=conservative_block",
        type(raw).__name__,
    )
    return True


def observe_only() -> bool:
    """When true, proactive decisions are logged but never sent."""
    return _flag(os.getenv(OBSERVE_ENV))


async def automation_disabled(store: Any = None) -> str | None:
    """Return a reason string when autonomous actions must stop, else ``None``.

    Fail-closed: any ambiguity or read failure blocks autonomous actions.
    """
    if kill_switch_active():
        return "env_kill_switch"
    if store is None:
        return None
    try:
        value = await store.get_setting(SETTING_KEY)
    except Exception as exc:
        logger.warning(
            "AUTOMATION_SWITCH_READ_FAILED exception_type=%s action=block_until_recovery",
            type(exc).__name__,
        )
        return "setting_read_error"
    if value is None or str(value).strip() == "":
        return None  # unset: fresh-install default is enabled
    normalized = str(value).strip().lower()
    if normalized in _FALSY:
        return "setting_disabled"
    if normalized in _TRUTHY:
        return None
    logger.warning(
        "AUTOMATION_SWITCH_INVALID_VALUE value_class=%s action=conservative_block",
        type(value).__name__,
    )
    return "setting_invalid"