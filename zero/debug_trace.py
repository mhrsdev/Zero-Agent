"""Redacted JSONL traces for reply debugging. Never writes raw prompts by default."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .paths import zero_home_path

logger = logging.getLogger("zero.debug")

_REDACT_KEYS = frozenset({"prompt", "text", "reply", "memory_context", "api_key", "token", "secret"})


def _debug_enabled(config: Any, flag: str) -> bool:
    debug = getattr(config, "debug", None)
    return bool(getattr(debug, flag, False)) if debug is not None else False


def _trace_path(config: Any) -> Path:
    configured = getattr(getattr(config, "debug", None), "trace_path", "") or ""
    if configured:
        return Path(configured)
    return zero_home_path("logs", "trace.jsonl")


def redact_event(event: dict[str, Any], *, log_prompts: bool = False) -> dict[str, Any]:
    """Drop raw user/prompt text unless an operator explicitly opted in."""
    out: dict[str, Any] = {}
    for key, value in event.items():
        if key in _REDACT_KEYS and not (log_prompts and key in {"prompt"}):
            if isinstance(value, str):
                out[f"{key}_chars"] = len(value)
            continue
        out[key] = value
    return out


def emit_reply_trace(config: Any, event: dict[str, Any]) -> None:
    """Append one redacted JSON line. Failures never affect the reply path."""
    if not _debug_enabled(config, "trace_replies"):
        return
    payload = redact_event(
        {"ts": time.time(), **event},
        log_prompts=_debug_enabled(config, "log_prompts"),
    )
    try:
        path = _trace_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        logger.warning("TRACE_WRITE_FAILED exception_type=%s", type(exc).__name__)
