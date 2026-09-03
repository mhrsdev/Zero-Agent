"""Soft daily LLM budget. Estimates cost from prompt size; never invents a bill."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def estimate_usd(prompt: str, output: str = "", *, dollars_per_million_tokens: float = 0.15) -> float:
    tokens = max(1.0, (len(prompt or "") + len(output or "")) / 4.0)
    return round(tokens / 1_000_000.0 * float(dollars_per_million_tokens), 8)


def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def spend_key(chat_id: int, day: str | None = None) -> str:
    return f"llm_spend_usd:{day or _day()}:{int(chat_id)}"


async def spent_today(store: Any, chat_id: int) -> float:
    raw = await store.get_setting(spend_key(chat_id), "0")
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


async def add_spend(store: Any, chat_id: int, usd: float) -> float:
    if usd <= 0:
        return await spent_today(store, chat_id)
    day = _day()
    current = await spent_today(store, chat_id)
    total = current + float(usd)
    await store.set_setting(spend_key(chat_id, day), str(total))
    try:
        await store.incr_daily_stats(day, total_cost_usd=float(usd))
    except Exception:
        pass
    return total


def budget_limit(config: Any, policy: Any) -> float:
    group = getattr(policy, "daily_budget_usd", None) if policy is not None else None
    if group is not None and float(group) > 0:
        return float(group)
    router = getattr(config, "router", None)
    soft = float(getattr(router, "daily_budget_soft_limit_usd", 0) or 0)
    return soft
