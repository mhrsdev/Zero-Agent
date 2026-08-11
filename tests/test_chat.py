from __future__ import annotations

import asyncio

from zero.chat import ChatRuntime, ChatState
from zero.models import Decision


class FakeBrain:
    def __init__(self, answer: str = "hello from Zero"):
        self.answer = answer
        self.seen = []

    async def remember_message(self, message):
        self.seen.append(("message", message.text))

    async def maybe_reply(self, message):
        self.seen.append(("reply", message.text))
        return Decision(True, "triggered"), self.answer

    async def remember_reply(self, message, answer):
        self.seen.append(("answer", answer))


def test_chat_state_commands_and_sessions():
    state = ChatState()
    first = state.active.session_id
    handled, help_text = state.slash("/help")
    assert handled and "/new" in help_text
    handled, result = state.slash("/new")
    assert handled and state.active.session_id != first
    handled, result = state.slash("/sessions")
    assert handled and first in result and state.active.session_id in result
    handled, result = state.slash("/clear")
    assert handled and not state.active.messages


def test_chat_runtime_uses_real_brain_contract():
    brain = FakeBrain()
    runtime = ChatRuntime(brain)
    answer = asyncio.run(runtime.ask("Explain the status"))
    assert answer == "hello from Zero"
    assert [item.role for item in runtime.state.active.messages] == ["user", "assistant"]
    assert brain.seen == [
        ("message", "Explain the status"),
        ("reply", "Explain the status"),
        ("answer", "hello from Zero"),
    ]


def test_chat_runtime_handles_brain_failure_without_raw_exception():
    class BrokenBrain(FakeBrain):
        async def maybe_reply(self, message):
            raise RuntimeError("provider-secret-value")

    runtime = ChatRuntime(BrokenBrain())
    answer = asyncio.run(runtime.ask("hello"))
    assert "Runtime error (RuntimeError)" in answer
    assert "provider-secret-value" not in answer
    assert runtime.state.active.messages[-1].role == "system"
