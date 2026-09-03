"""ZeroBrain must bind MemoryService per request when tenancy is provided."""
from __future__ import annotations

import asyncio
from pathlib import Path

from conftest import CONFIG_EXAMPLE
from zero.brain import ZeroBrain
from zero.config import ZeroConfig
from zero.core.memory_service import MemoryService
from zero.models import IncomingMessage
from zero.storage import ZeroStore
from zero.tenancy import GroupState, Role, Scope, TenancyRegistry


class CapturingRouter:
    keys = []
    gemini_keys = []
    last_route = {"provider": "test"}

    def __init__(self):
        self.prompts: list[str] = []

    async def complete(self, prompt, *, max_output_tokens=700):
        from zero.models import RouteResult

        self.prompts.append(prompt)
        return RouteResult(text="ok", provider="test", model="test", attempts=1)


def _config(tmp_path: Path) -> ZeroConfig:
    config = ZeroConfig.load(CONFIG_EXAMPLE)
    return config.model_copy(update={"memory": config.memory.model_copy(update={"db_path": str(tmp_path / "zero.db")})})


def _message(chat_id: int, text: str, *, sender: int = 7, message_id: int = 1) -> IncomingMessage:
    return IncomingMessage(
        chat_id=chat_id,
        chat_title=f"g{chat_id}",
        sender_id=sender,
        sender_label=f"u{sender}",
        text=text,
        message_id=message_id,
        mention_zero=True,
        trace_id="iso",
    )


def _approved(registry: TenancyRegistry, group: str, chat_id: int, owner: int = 1) -> Scope:
    registry.discover_group("local", group, platform_chat_id=chat_id)
    scope = Scope("local", group, owner)
    registry.add_member(scope, owner, Role.OWNER)
    registry.set_group_state(scope, GroupState.ACTIVE)
    return scope


def test_unbound_brain_keeps_working_without_tenancy(tmp_path):
    config = _config(tmp_path)
    brain = ZeroBrain(config, ZeroStore(config.memory.db_path), CapturingRouter())
    assert brain.memory.scope is None
    decision, _ = asyncio.run(brain.maybe_reply(_message(-99, "@zero ping")))
    assert decision.reason != "tenancy_unresolved"


def test_brain_fail_closed_when_chat_is_not_registered(tmp_path):
    config = _config(tmp_path)
    registry = TenancyRegistry(tmp_path / "tenancy.db")
    brain = ZeroBrain(config, ZeroStore(config.memory.db_path), CapturingRouter(), tenancy=registry, installation_id="local")
    decision, text = asyncio.run(brain.maybe_reply(_message(-404, "@zero ping")))
    assert decision.should_reply is False
    assert decision.reason == "tenancy_unresolved"
    assert text == ""


def test_two_groups_do_not_share_memory_context(tmp_path):
    config = _config(tmp_path)
    registry = TenancyRegistry(tmp_path / "tenancy.db")
    a = _approved(registry, "group-a", -100)
    b = _approved(registry, "group-b", -200, owner=2)
    registry.add_member(a, 7, Role.MEMBER)
    registry.add_member(b, 7, Role.MEMBER)
    store = ZeroStore(config.memory.db_path)
    router = CapturingRouter()
    brain = ZeroBrain(config, store, router, tenancy=registry, installation_id="local")

    secret = "fixture"
    asyncio.run(brain.remember_message(_message(-100, f"my password is {secret}")))
    asyncio.run(brain.maybe_reply(_message(-200, "@zero what was the password?", message_id=2)))
    leaked = any(secret in prompt for prompt in router.prompts)
    assert leaked is False

    bound_a = MemoryService(brain.memory_v3).bind(a.for_user(7), registry)
    from types import SimpleNamespace

    foreign = SimpleNamespace(chat_id=-200, sender_id=7, trace_id="t", message_id=9)
    from zero.tenancy import ScopeViolation
    import pytest

    with pytest.raises(ScopeViolation):
        asyncio.run(bound_a.context(foreign))


def test_group_silent_mode_skips_replies(tmp_path):
    config = _config(tmp_path)
    registry = TenancyRegistry(tmp_path / "tenancy.db")
    scope = _approved(registry, "group-a", -100)
    registry.add_member(scope, 7, Role.MEMBER)
    registry.set_setting(scope, "reply_mode", "silent")
    brain = ZeroBrain(config, ZeroStore(config.memory.db_path), CapturingRouter(), tenancy=registry, installation_id="local")
    decision, text = asyncio.run(brain.maybe_reply(_message(-100, "@zero hello")))
    assert decision.should_reply is False
    assert decision.reason == "group_silent"
    assert text == ""
