"""Kill switch + observe mode for every autonomous action path."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zero.automation import (
    KILL_ENV,
    OBSERVE_ENV,
    SETTING_KEY,
    automation_disabled,
    kill_switch_active,
    observe_only,
)
from zero.config import ReactionsConfig, ZeroConfig
from zero.models import IncomingMessage
from zero.proactive_followups import ProactiveFollowups
from zero.reactions import ReactionService
from zero.storage import ZeroStore


def _msg(text: str = "سلام", **kw) -> IncomingMessage:
    base = dict(
        chat_id=-1001, chat_title="test", sender_id=50, sender_label="user",
        text=text, message_id=1, trace_id="t", sender_is_bot=False,
    )
    base.update(kw)
    return IncomingMessage(**base)


class FakeEvent:
    id = 700
    chat_id = -1001

    async def get_input_chat(self):
        return "input-chat"


class FakeClient:
    def __init__(self):
        self.calls = []

    async def __call__(self, request):
        self.calls.append(request)


class FakeRouter:
    """Router that would be called if the pipeline ever reached the LLM."""

    def __init__(self):
        self.calls = 0

    async def complete(self, prompt, max_output_tokens=100):
        self.calls += 1
        if "should_schedule" in prompt:
            payload = {"version": 1, "should_schedule": True, "confidence": 0.9,
                       "follow_up_type": "task_outcome", "topic": "report",
                       "goal": "send report", "delay_hours": 24,
                       "deadline_hours": None, "sensitivity": "normal",
                       "intrusiveness": "low"}
        elif '"action"' in prompt:
            payload = {"version": 1, "action": "send", "confidence": 0.9,
                       "postpone_hours": 0, "reason_code": "ok"}
        else:
            payload = {"text": "پیگیری: گزارش رو فرستادی؟"}
        return SimpleNamespace(text=json.dumps(payload), provider="fake")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(KILL_ENV, raising=False)
    monkeypatch.delenv(OBSERVE_ENV, raising=False)


# ---------------------------------------------------------------- helpers

def test_env_kill_switch_parsing(monkeypatch):
    assert kill_switch_active() is False
    monkeypatch.setenv(KILL_ENV, "true")
    assert kill_switch_active() is True
    monkeypatch.setenv(KILL_ENV, "1")
    assert kill_switch_active() is True
    monkeypatch.setenv(KILL_ENV, "false")
    assert kill_switch_active() is False


def test_observe_mode_parsing(monkeypatch):
    assert observe_only() is False
    monkeypatch.setenv(OBSERVE_ENV, "true")
    assert observe_only() is True


def test_automation_disabled_setting_and_fail_open(tmp_path: Path):
    async def scenario():
        store = ZeroStore(str(tmp_path / "zero.db"))
        assert await automation_disabled(store) is None
        await store.set_setting(SETTING_KEY, "false")
        assert await automation_disabled(store) == "setting_disabled"
        await store.set_setting(SETTING_KEY, "true")
        assert await automation_disabled(store) is None

    asyncio.run(scenario())


def test_automation_disabled_env_overrides_setting(tmp_path: Path, monkeypatch):
    async def scenario():
        store = ZeroStore(str(tmp_path / "zero.db"))
        await store.set_setting(SETTING_KEY, "true")
        monkeypatch.setenv(KILL_ENV, "true")
        assert await automation_disabled(store) == "env_kill_switch"

    asyncio.run(scenario())


# ---------------------------------------------------------------- reactions

def test_reaction_service_respects_kill_switch(tmp_path: Path, monkeypatch):
    async def scenario():
        store = ZeroStore(str(tmp_path / "zero.db"))
        await store.set_setting("reactions_enabled", "true")
        config = SimpleNamespace(owner_user_id=1, reactions=ReactionsConfig())
        client = FakeClient()
        service = ReactionService(config, store, client, self_id=2)
        # Setting-based switch.
        await store.set_setting(SETTING_KEY, "false")
        decision = await service.maybe_react(FakeEvent(), _msg("جوک خنده‌دار 😂"))
        assert decision.should_react is False
        assert decision.skipped_reason == "kill_switch"
        assert client.calls == []
        # Env-based switch wins even when the setting allows automation.
        await store.set_setting(SETTING_KEY, "true")
        monkeypatch.setenv(KILL_ENV, "true")
        decision = await service.maybe_react(FakeEvent(), _msg("جوک خنده‌دار 😂"))
        assert decision.skipped_reason == "kill_switch"
        assert client.calls == []

    asyncio.run(scenario())


# ---------------------------------------------------------------- proactive

def _install_candidate(pf, tmp_path: Path) -> str:
    from zero.sqlite_tx import sqlite_txn
    with sqlite_txn(pf.store._conn()) as c:
        c.execute(
            "INSERT INTO proactive_followups(id,chat_id,subject_user_id,created_at,due_at,"
            "follow_up_type,topic_summary,goal,priority,sensitivity,confidence,status,dedup_key)"
            " VALUES('cand1',-1001,50,1,1,'task_outcome','report','send report','normal',"
            "'normal',0.9,'pending','k1')"
        )


def test_proactive_tick_blocked_by_kill_switch(tmp_path: Path, monkeypatch):
    async def scenario():
        store = ZeroStore(str(tmp_path / "zero.db"))
        router = FakeRouter()
        pf = ProactiveFollowups(store, router)
        _install_candidate(pf, tmp_path)
        monkeypatch.setenv(KILL_ENV, "true")
        out = await pf.tick(worker="test")
        assert out and out[0]["action"] == "blocked"
        assert out[0]["reason"].startswith("kill_switch:")
        assert router.calls == 0  # nothing evaluated, nothing sent

    asyncio.run(scenario())


def test_proactive_consider_blocked_by_kill_switch(tmp_path: Path, monkeypatch):
    async def scenario():
        store = ZeroStore(str(tmp_path / "zero.db"))
        router = FakeRouter()
        pf = ProactiveFollowups(store, router)
        monkeypatch.setenv("ZERO_PROACTIVE_FOLLOWUP_ENABLED", "true")
        monkeypatch.setenv("ZERO_PROACTIVE_FOLLOWUP_CREATE_ENABLED", "true")
        monkeypatch.setenv(KILL_ENV, "true")
        result = await pf.consider(_msg())
        assert result == {"created": False, "reason": "kill_switch"}
        assert router.calls == 0

    asyncio.run(scenario())


def test_proactive_observe_mode_records_without_sending(tmp_path: Path, monkeypatch):
    async def scenario():
        store = ZeroStore(str(tmp_path / "zero.db"))
        router = FakeRouter()
        pf = ProactiveFollowups(store, router)
        _install_candidate(pf, tmp_path)
        monkeypatch.setenv(OBSERVE_ENV, "true")
        out = await pf.tick(worker="test")
        assert len(out) == 1
        entry = out[0]
        assert entry["action"] == "observe"
        assert entry["would_send"] is True
        assert entry["reason"] == "observe_only"
        # Nothing was reserved or sent through the outbox.
        from zero.sqlite_tx import sqlite_txn
        with sqlite_txn(store._conn()) as c:
            count = c.execute("SELECT count(*) FROM proactive_followup_outbox").fetchone()[0]
            status = c.execute(
                "SELECT status FROM proactive_followups WHERE id='cand1'"
            ).fetchone()[0]
        assert count == 0
        assert status == "postponed"  # re-checked later, never lost

    asyncio.run(scenario())


def test_proactive_still_sends_when_switches_off(tmp_path: Path, monkeypatch):
    """Guard against the switches accidentally disabling normal operation."""
    async def scenario():
        store = ZeroStore(str(tmp_path / "zero.db"))
        router = FakeRouter()

        class MockTransport:
            def __init__(self):
                self.sent = []

            async def send(self, chat_id, text, key):
                self.sent.append((chat_id, text))
                from zero.proactive_transport import TransportResult
                return TransportResult(True, receipt=f"mock:{key}")

        transport = MockTransport()
        pf = ProactiveFollowups(store, router, transport=transport)
        _install_candidate(pf, tmp_path)
        out = await pf.tick(worker="test")
        assert out[0]["action"] == "send"
        assert len(transport.sent) == 1

    asyncio.run(scenario())