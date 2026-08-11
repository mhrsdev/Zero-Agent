from pathlib import Path
from types import SimpleNamespace

from zero.admin import GroupAdminService
from zero.sessions import SessionRegistry
from zero.tenancy import Scope
from zero.tenancy.registry import TenancyRegistry


def _config(chat_ids):
    return SimpleNamespace(listener=SimpleNamespace(
        allowed_group_ids=list(chat_ids),
        allowed_group_usernames=[],
        allowed_group_titles=[],
        session_path="/legacy/account",
    ))


def _event(chat_id):
    return SimpleNamespace(chat_id=chat_id, chat=SimpleNamespace(username="", title=""))


def test_listener_allowed_chat_uses_registry_as_authority(tmp_path):
    from scripts.run_listener import _allowed_chat

    registry = TenancyRegistry(tmp_path / "tenancy.db")
    admin = GroupAdminService(registry, installation_id="install-a", owner_user_id=42)
    admin.add_group(-222)
    config = _config([])
    assert _allowed_chat(_event(-222), config, tenancy=registry, installation_id="install-a") is True

    config.listener.allowed_group_ids = [-222]
    admin.disable_group(-222)
    assert _allowed_chat(_event(-222), config, tenancy=registry, installation_id="install-a") is False


def test_listener_runtime_group_ids_prefer_registry_even_when_empty(tmp_path):
    from scripts.run_listener import _runtime_group_ids

    registry = TenancyRegistry(tmp_path / "tenancy.db")
    admin = GroupAdminService(registry, installation_id="install-a", owner_user_id=42)
    admin.add_group(-333)
    assert _runtime_group_ids(_config([]), registry, "install-a") == [-333]
    admin.disable_group(-333)
    assert _runtime_group_ids(_config([-999]), registry, "install-a") == []


def test_listener_applies_active_session_registry(tmp_path):
    from scripts.run_listener import _apply_active_session

    sessions = SessionRegistry(tmp_path / "sessions")
    record = sessions.add("active")
    Path(str(record.session_path) + ".session").write_bytes(b"credential")
    sessions.mark_authorized("active", user_id=42, username="owner")
    sessions.activate("active")
    config = _config([])
    _apply_active_session(config, session_root=sessions.root)
    assert Path(config.listener.session_path) == record.session_path


def test_listener_group_quota_is_human_only_and_atomic(tmp_path):
    from scripts.run_listener import _consume_human_reply_quota

    registry = TenancyRegistry(tmp_path / "tenancy.db")
    admin = GroupAdminService(registry, installation_id="install-a", owner_user_id=42)
    group = admin.add_group(-444)
    admin.set_reply_limits(-444, {"hour": 1, "day": 1, "week": 1, "month": 1})
    human = SimpleNamespace(chat_id=-444, sender_is_bot=False)
    bot = SimpleNamespace(chat_id=-444, sender_is_bot=True)
    assert _consume_human_reply_quota(registry, "install-a", bot).allowed is True
    assert _consume_human_reply_quota(registry, "install-a", human).allowed is True
    blocked = _consume_human_reply_quota(registry, "install-a", human)
    assert blocked.allowed is False
    assert blocked.blocked_period == "hour"
    scope = Scope("install-a", group.group_id)
    assert registry.usage(scope, "human_replies", period="month") == 1



class _ReplyEvent:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    async def reply(self, text):
        self.calls.append(text)
        if self.fail:
            raise OSError("transport failed")
        return SimpleNamespace(id=123)


async def _quota_fixture(tmp_path):
    registry = TenancyRegistry(tmp_path / "tenancy.db")
    admin = GroupAdminService(registry, installation_id="install-a", owner_user_id=42)
    group = admin.add_group(-555)
    admin.set_reply_limits(-555, {"hour": 1, "day": 1, "week": 1, "month": 1})
    incoming = SimpleNamespace(chat_id=-555, sender_is_bot=False)
    return registry, group, incoming


import pytest


@pytest.mark.asyncio
async def test_quota_transport_refunds_failure_and_blocks_second_success(tmp_path):
    from scripts.run_listener import _reply_with_human_quota

    registry, group, incoming = await _quota_fixture(tmp_path)
    failing = _ReplyEvent(fail=True)
    with pytest.raises(OSError):
        await _reply_with_human_quota(failing, "reply", tenancy=registry, installation_id="install-a", incoming=incoming)
    scope = Scope("install-a", group.group_id)
    assert registry.usage(scope, "human_replies", period="hour") == 0

    successful = _ReplyEvent()
    sent, decision = await _reply_with_human_quota(successful, "reply", tenancy=registry, installation_id="install-a", incoming=incoming)
    assert sent.id == 123
    assert decision.allowed is True
    blocked_event = _ReplyEvent()
    sent, decision = await _reply_with_human_quota(blocked_event, "blocked", tenancy=registry, installation_id="install-a", incoming=incoming)
    assert sent is None
    assert decision.allowed is False
    assert blocked_event.calls == []
