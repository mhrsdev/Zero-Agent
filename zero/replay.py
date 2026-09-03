"""Offline replay of a stored group message. Never sends to Telegram or a live provider."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from .brain import ZeroBrain
from .config import ZeroConfig
from .models import IncomingMessage
from .storage import ZeroStore


class ReplayRouter:
    """Records the prompt that would have been sent; never opens a socket."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.last_route: dict[str, Any] = {"provider": "replay", "model": "none"}
        self.gemini_keys: list[str] = []
        self.keys: list[str] = []

    async def complete(self, prompt: str, *, max_output_tokens: int = 700):
        from .models import RouteResult

        self.prompts.append(prompt)
        return RouteResult(text="__NO_REPLY__", provider="replay", model="none", attempts=0, metadata={"replay": True})

    async def complete_with_tools(self, prompt: str, tools, *, max_output_tokens: int = 700):
        return await self.complete(prompt, max_output_tokens=max_output_tokens)

    def status(self) -> dict[str, Any]:
        return {"providers": {}, "last_route": dict(self.last_route)}


def _row_message(chat_id: int, row: dict[str, Any], *, message_id: int) -> IncomingMessage:
    text = str(row.get("text") or "")
    sender_id = int(row.get("sender_id") or 0)
    return IncomingMessage(
        chat_id=int(chat_id),
        chat_title=str(row.get("chat_title") or ""),
        sender_id=sender_id,
        sender_label=str(row.get("sender_label") or f"user:{sender_id}"),
        text=text,
        message_id=int(row.get("telegram_message_id") or row.get("message_id") or message_id),
        trace_id="replay",
        sender_username=str(row.get("sender_username") or ""),
        sender_display_name=str(row.get("sender_display_name") or ""),
        thread_id=row.get("thread_id"),
        reply_to_message_id=row.get("reply_to_message_id"),
        platform=str(row.get("platform") or "telegram"),
        account_scope=str(row.get("account_scope") or "replay"),
    )


async def replay_message(
    *,
    config: ZeroConfig,
    chat_id: int,
    message_id: int,
    db_path: str | None = None,
) -> dict[str, Any]:
    store = ZeroStore(db_path or config.memory.db_path)
    recent = await store.get_recent(int(chat_id), limit=5000)
    row = None
    for item in recent:
        tid = int(item.get("telegram_message_id") or item.get("message_id") or 0)
        if tid == int(message_id):
            row = item
            break
    if row is None:
        return {"ok": False, "error": "message_not_found", "chat_id": chat_id, "message_id": message_id}
    incoming = _row_message(chat_id, row, message_id=message_id)
    router = ReplayRouter()
    brain = ZeroBrain(config, store, router)
    decision, answer = await brain.maybe_reply(incoming)
    prompt = router.prompts[-1] if router.prompts else ""
    return {
        "ok": True,
        "chat_id": chat_id,
        "message_id": message_id,
        "sender_id": incoming.sender_id,
        "decision": {"should_reply": decision.should_reply, "reason": decision.reason, "interject": decision.interject},
        "answer_chars": len(answer or ""),
        "prompt_chars": len(prompt),
        "provider": "replay",
        "would_send": bool(decision.should_reply and answer and answer.strip() not in {"", "__NO_REPLY__"}),
    }


def replay_to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def run_replay(config: ZeroConfig, *, chat_id: int, message_id: int, db_path: str | None = None) -> dict[str, Any]:
    return asyncio.run(replay_message(config=config, chat_id=chat_id, message_id=message_id, db_path=db_path))
