"""Remaining plan gaps: forget, budget, forum topics, local OpenAI, config, repos, office health, gated live E2E."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from conftest import CONFIG_EXAMPLE
from zero.config import ProviderConfig, RouterProvidersConfig, ZeroConfig
from zero.group_policy import load_group_policy
from zero.memory_v3 import MemoryV3Item, MemoryV3Service
from zero.models import IncomingMessage
from zero.spend import add_spend, budget_limit, estimate_usd, spent_today
from zero.storage import ZeroStore
from zero.storage_repos import DailyStatsRepo, RecentMessagesRepo, SettingsRepo
from zero.tenancy import GroupState, Role, Scope, TenancyRegistry


def _msg(chat_id: int, text: str, *, sender: int = 7, mention: bool = True, thread_id=None, reply_sender=None) -> IncomingMessage:
    return IncomingMessage(
        chat_id=chat_id, chat_title=f"g{chat_id}", sender_id=sender, sender_label=f"u{sender}",
        text=text, message_id=1, mention_zero=mention, trace_id="g", thread_id=thread_id,
        reply_sender_id=reply_sender,
    )


def _scope(reg: TenancyRegistry, gid: str, chat_id: int, owner: int = 1) -> Scope:
    reg.discover_group("local", gid, platform_chat_id=chat_id)
    scope = Scope("local", gid, owner)
    reg.add_member(scope, owner, Role.OWNER)
    reg.set_group_state(scope, GroupState.ACTIVE)
    return scope


def test_v3_forget_user_marks_items_deleted(tmp_path):
    service = MemoryV3Service(str(tmp_path / "v3.db"))

    async def run():
        await service.put(MemoryV3Item.personal(chat_id=-10, user_id=42, content="ترجیح کاربر: چای"))
        await service.put(MemoryV3Item.personal(chat_id=-10, user_id=99, content="ترجیح کاربر: قهوه"))
        removed = await service.forget_user(-10, 42)
        assert removed == 1
        ctx, meta = await service.context(_msg(-10, "یادت اسم من چیه", sender=42))
        assert "چای" not in ctx
        other, _ = await service.context(_msg(-10, "یادت اسم من چیه", sender=99))
        assert "قهوه" in other
        return removed

    assert asyncio.run(run()) == 1


def test_owner_memory_forget_deletes_v3(tmp_path):
    from zero.brain import ZeroBrain

    config = ZeroConfig.load(CONFIG_EXAMPLE)
    owner = int(config.owner_user_id or 0) or 1
    config = config.model_copy(update={
        "owner_user_id": owner,
        "memory": config.memory.model_copy(update={"db_path": str(tmp_path / "zero.db")}),
    })
    store = ZeroStore(config.memory.db_path)
    router = MagicMock()
    router.keys = []
    router.gemini_keys = []
    router.last_route = {}
    brain = ZeroBrain(config, store, router)

    async def run():
        await brain.memory_v3.put(MemoryV3Item.personal(chat_id=-9, user_id=55, content="ترجیح کاربر: آبی"))
        decision, text = await brain.maybe_reply(_msg(-9, "/memory forget 55", sender=owner, reply_sender=55))
        assert decision.reason == "memory_forget"
        assert "حذف" in text
        ctx, _ = await brain.memory_v3.context(_msg(-9, "یادت اسم من چیه", sender=55))
        assert "آبی" not in ctx

    asyncio.run(run())


def test_spend_estimate_and_budget_enforced(tmp_path):
    store = ZeroStore(str(tmp_path / "s.db"))
    assert estimate_usd("abcd" * 250) > 0

    async def run():
        assert await spent_today(store, -1) == 0
        await add_spend(store, -1, 1.25)
        assert await spent_today(store, -1) == pytest.approx(1.25)
        stats = await store.get_today_stats(__import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%d"))
        assert float(stats.get("total_cost_usd") or 0) == pytest.approx(1.25)

    asyncio.run(run())
    policy = SimpleNamespace(daily_budget_usd=0.5)
    config = SimpleNamespace(router=SimpleNamespace(daily_budget_soft_limit_usd=5.0))
    assert budget_limit(config, policy) == 0.5
    assert budget_limit(config, SimpleNamespace(daily_budget_usd=None)) == 5.0


def test_brain_stops_when_group_budget_exhausted(tmp_path):
    from zero.brain import ZeroBrain

    config = ZeroConfig.load(CONFIG_EXAMPLE)
    config = config.model_copy(update={"memory": config.memory.model_copy(update={"db_path": str(tmp_path / "zero.db")})})
    store = ZeroStore(config.memory.db_path)
    reg = TenancyRegistry(tmp_path / "t.db")
    scope = _scope(reg, "g", -100)
    reg.add_member(scope, 7, Role.MEMBER)
    reg.set_setting(scope, "daily_budget_usd", 0.01)
    router = MagicMock()
    router.keys = []
    router.gemini_keys = []
    router.last_route = {}
    brain = ZeroBrain(config, store, router, tenancy=reg, installation_id="local")

    async def run():
        await add_spend(store, -100, 1.0)
        decision, text = await brain.maybe_reply(_msg(-100, "@zero hi"))
        assert decision.reason == "budget_exhausted"
        assert "بودجه" in text

    asyncio.run(run())


def test_forum_topics_allow_and_deny(tmp_path):
    from zero.brain import ZeroBrain

    config = ZeroConfig.load(CONFIG_EXAMPLE)
    config = config.model_copy(update={"memory": config.memory.model_copy(update={"db_path": str(tmp_path / "zero.db")})})
    store = ZeroStore(config.memory.db_path)
    reg = TenancyRegistry(tmp_path / "t.db")
    scope = _scope(reg, "g", -100)
    reg.add_member(scope, 7, Role.MEMBER)
    reg.set_setting(scope, "forum_topics", {"allow": [11], "deny": [22]})
    policy = load_group_policy(reg, scope)
    assert policy.forum_topics["allow"] == [11]
    router = MagicMock()
    router.keys = []
    router.gemini_keys = []
    router.last_route = {}
    brain = ZeroBrain(config, store, router, tenancy=reg, installation_id="local")

    async def run():
        denied, _ = await brain.maybe_reply(_msg(-100, "@zero hi", thread_id=22))
        assert denied.reason == "forum_topic_denied"
        off_allow, _ = await brain.maybe_reply(_msg(-100, "@zero hi", thread_id=99))
        assert off_allow.reason == "forum_topic_denied"

    asyncio.run(run())


def test_local_openai_registers_without_cloud_keys():
    from zero.providers.from_config import registry_from_runtime_config

    local = ProviderConfig(enabled=True, model="llama3", base_url="http://127.0.0.1:11434/v1", keys=[])
    providers = RouterProvidersConfig(
        gemini=ProviderConfig(enabled=False, model="g", keys=[]),
        openrouter=ProviderConfig(enabled=False, model="o", keys=[]),
        local_openai=local,
    )
    config = SimpleNamespace(router=SimpleNamespace(providers=providers, request_timeout_seconds=5, max_provider_retries=0))
    registry = registry_from_runtime_config(config, http_post=lambda *a, **k: None)
    assert registry is not None
    assert "local_openai" in registry.names()
    profile = registry.profile("local_openai")
    assert profile.secret_ref is None
    assert profile.base_url.endswith("/v1")


def test_zero_config_loads_json_and_yaml(tmp_path):
    yaml_src = Path(CONFIG_EXAMPLE).read_text(encoding="utf-8")
    data = yaml.safe_load(yaml_src)
    json_path = tmp_path / "zero.json"
    json_path.write_text(json.dumps(data), encoding="utf-8")
    yaml_path = tmp_path / "zero.yaml"
    yaml_path.write_text(yaml_src, encoding="utf-8")
    from_json = ZeroConfig.load(json_path)
    from_yaml = ZeroConfig.load(yaml_path)
    assert from_json.owner_user_id == from_yaml.owner_user_id
    assert from_json.router.providers.gemini.model == from_yaml.router.providers.gemini.model


def test_storage_repos_roundtrip(tmp_path):
    store = ZeroStore(str(tmp_path / "s.db"))

    async def run():
        await store.append_recent(-3, 1, "ali", "user", "hello")
        rows = await store.get_recent(-3, 10)
        assert rows and rows[0]["text"] == "hello"
        await store.set_setting("mode", "funny")
        assert await store.get_setting("mode") == "funny"

        def _op(conn):
            settings = SettingsRepo(conn)
            recent = RecentMessagesRepo(conn)
            stats = DailyStatsRepo(conn)
            settings.set("k", "v")
            stats.add("2099-01-01", total_cost_usd=0.2)
            return settings.get("k"), len(recent.list(-3, 5))

        return await store._exec(_op)

    key, n = asyncio.run(run())
    assert key == "v"
    assert n == 1


def test_office_health_reports_unavailable_without_binary(tmp_path):
    from zero.config import OfficeConfig
    from zero.office.adapter import OfficeCliAdapter
    from zero.office.workspace import create_workspace

    workspace = create_workspace(tmp_path, chat_id=1, job_id="health")
    missing = tmp_path / "no-officecli"
    cfg = OfficeConfig(enabled=True, cli_path=str(missing), workspace_root=str(tmp_path / "jobs"))
    report = OfficeCliAdapter(cfg, workspace).health()
    assert report["available"] is False
    assert report["error_code"] == "officecli_unavailable"


def test_live_e2e_is_gated():
    source = Path(__file__).resolve().parent.joinpath("test_live_providers_e2e.py").read_text(encoding="utf-8")
    assert "ZERO_LIVE_E2E" in source
    assert "pytest.skip" in source
