"""Local conversational runtime used by the Zero terminal UI.

The Telegram listener remains the production transport. This module provides a
small, transport-neutral adapter around the real :class:`ZeroBrain` for local
interactive use; it does not duplicate routing, memory, or policy logic.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .brain import ZeroBrain
from .config import ZeroConfig
from .models import IncomingMessage
from .router import IndependentRouter
from .runtime_config import load_effective_config
from .storage import ZeroStore


@dataclass(slots=True)
class ChatMessage:
    role: str
    text: str
    created_at: float = field(default_factory=time.time)


@dataclass
class ChatSession:
    session_id: str
    title: str
    messages: list[ChatMessage] = field(default_factory=list)


class ChatState:
    """In-memory session state with deterministic slash-command handling."""

    def __init__(self) -> None:
        self.sessions: list[ChatSession] = []
        self.active_index = 0
        self.new_session()

    @property
    def active(self) -> ChatSession:
        return self.sessions[self.active_index]

    def new_session(self, title: str = "New session") -> ChatSession:
        session = ChatSession(session_id=uuid.uuid4().hex[:12], title=title)
        self.sessions.append(session)
        self.active_index = len(self.sessions) - 1
        return session

    def select(self, session_id: str) -> bool:
        for index, session in enumerate(self.sessions):
            if session.session_id == session_id:
                self.active_index = index
                return True
        return False

    def clear(self) -> None:
        self.active.messages.clear()
        self.active.title = "New session"

    def slash(self, text: str) -> tuple[bool, str]:
        """Handle local commands; return ``(handled, display_text)``."""
        command, _, argument = text.strip().partition(" ")
        command = command.casefold()
        if command in {"/help", "/?"}:
            return True, "/new  /clear  /sessions  /use <id>  /help  /quit"
        if command in {"/new", "/session"} and not argument.strip():
            self.new_session()
            return True, f"Started session {self.active.session_id}."
        if command == "/clear":
            self.clear()
            return True, "Current session cleared."
        if command == "/sessions":
            rows = [
                f"{'*' if i == self.active_index else ' '} {s.session_id}  {s.title} ({len(s.messages)} messages)"
                for i, s in enumerate(self.sessions)
            ]
            return True, "\n".join(rows)
        if command == "/use" and argument.strip():
            session_id = argument.strip().split()[0]
            if self.select(session_id):
                return True, f"Switched to session {session_id}."
            return True, f"Unknown session: {session_id}"
        return False, ""


class ChatRuntime:
    """Async adapter around the real ZeroBrain.

    ``brain`` is injectable so the UI and policy behavior can be tested without
    network credentials or a provider call.
    """

    def __init__(self, brain: Any, state: ChatState | None = None) -> None:
        self.brain = brain
        self.state = state or ChatState()

    async def ask(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""
        handled, local_result = self.state.slash(text)
        if handled:
            return local_result

        session = self.state.active
        message = IncomingMessage(
            chat_id=-1,
            chat_title="Zero TUI",
            sender_id=0,
            sender_label="local-user",
            text=text,
            mention_zero=True,
            reply_to_zero=False,
            trace_id=uuid.uuid4().hex[:8],
            message_id=int(time.time_ns() // 1_000_000),
            platform="local",
            account_scope="tui",
            thread_id=None,
        )
        session.messages.append(ChatMessage("user", text))
        try:
            await self.brain.remember_message(message)
            decision, answer = await self.brain.maybe_reply(message)
            answer = answer.strip() if answer else ""
            if not decision.should_reply or not answer or answer == "__NO_REPLY__":
                answer = f"No reply ({decision.reason})."
            session.messages.append(ChatMessage("assistant", answer))
            await self.brain.remember_reply(message, answer)
            if session.title == "New session":
                session.title = text.replace("\n", " ")[:48]
            return answer
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            answer = f"Runtime error ({type(exc).__name__}). Check `zero doctor` and the logs."
            session.messages.append(ChatMessage("system", answer))
            return answer


def build_chat_runtime(
    *,
    runtime_config_path: Path,
    store_path: Path | None = None,
    brain: ZeroBrain | None = None,
) -> ChatRuntime:
    """Build the real ZeroBrain adapter from the legacy runtime config."""
    if brain is not None:
        return ChatRuntime(brain)
    config = load_effective_config(runtime_config_path, ZeroConfig)
    path = store_path or Path(config.memory.db_path)
    store = ZeroStore(
        str(path),
        recent_messages_limit=config.memory.recent_messages_limit,
        long_term_limit=config.memory.long_term_limit,
    )
    router = IndependentRouter(config)
    return ChatRuntime(ZeroBrain(config, store, router))
