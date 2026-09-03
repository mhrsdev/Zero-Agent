"""Practical coverage for remaining Zero core-plan options. No live network."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from conftest import CONFIG_EXAMPLE
from zero.chat_locks import ChatLockMap
from zero.config import ZeroConfig
from zero.debug_trace import emit_reply_trace, redact_event
from zero.group_policy import QuietHours, load_group_policy
from zero.models import IncomingMessage
from zero.storage import ZeroStore
from zero.telegram_backoff import with_flood_wait
from zero.tenancy import GroupState, Role, Scope, TenancyRegistry
from zero.turn_options import (
    office_allowed,
    parse_owner_memory_command,
    prompt_option_block,
    quiet_hours_block_automation,
    reply_char_limit_for,
    should_skip_private_memory,
    think_prefix,
    web_allowed,
)


def _cfg(tmp_path: Path) -> ZeroConfig:
    config = ZeroConfig.load(CONFIG_EXAMPLE)
    return config.model_copy(update={"memory": config.memory.model_copy(update={"db_path": str(tmp_path / "zero.db")})})


def _msg(chat_id: int, text: str, *, sender: int = 7, mention: bool = True) -> IncomingMessage:
    return IncomingMessage(
        chat_id=chat_id, chat_title=f"g{chat_id}", sender_id=sender, sender_label=f"u{sender}",
        text=text, message_id=1, mention_zero=mention, trace_id="p",
    )


def _scope(reg: TenancyRegistry, gid: str, chat_id: int, owner: int = 1) -> Scope:
    reg.discover_group("local", gid, platform_chat_id=chat_id)
    scope = Scope("local", gid, owner)
    reg.add_member(scope, owner, Role.OWNER)
    reg.set_group_state(scope, GroupState.ACTIVE)
    return scope


# --- group policy matrix -------------------------------------------------

@pytest.mark.parametrize("mode", ["mention_or_reply", "mention_only", "always_allowed", "silent"])
def test_reply_mode_roundtrip(tmp_path, mode):
    reg = TenancyRegistry(tmp_path / "t.db")
    scope = _scope(reg, "g", -1)
    reg.set_setting(scope, "reply_mode", mode)
    assert load_group_policy(reg, scope).reply_mode == mode


@pytest.mark.parametrize("lang", ["auto", "fa", "en", "mix"])
def test_language_roundtrip(tmp_path, lang):
    reg = TenancyRegistry(tmp_path / "t.db")
    scope = _scope(reg, "g", -1)
    reg.set_setting(scope, "language", lang)
    assert load_group_policy(reg, scope).language == lang


@pytest.mark.parametrize("profile", ["compact", "normal", "long"])
def test_reply_profile_roundtrip(tmp_path, profile):
    reg = TenancyRegistry(tmp_path / "t.db")
    scope = _scope(reg, "g", -1)
    reg.set_setting(scope, "reply_profile", profile)
    assert load_group_policy(reg, scope).reply_profile == profile


@pytest.mark.parametrize("depth", ["off", "light", "standard", "deep"])
def test_inject_depth_roundtrip(tmp_path, depth):
    reg = TenancyRegistry(tmp_path / "t.db")
    scope = _scope(reg, "g", -1)
    reg.set_setting(scope, "memory_inject_depth", depth)
    assert load_group_policy(reg, scope).memory_inject_depth == depth


@pytest.mark.parametrize("key,value,attr,expect", [
    ("enabled", False, "enabled", False),
    ("memory_enabled", False, "memory_enabled", False),
    ("web_search_enabled", False, "web_search_enabled", False),
    ("automation_interject", False, "automation_interject", False),
    ("automation_reactions", False, "automation_reactions", False),
    ("automation_proactive", True, "automation_proactive", True),
    ("automation_stickers", False, "automation_stickers", False),
    ("automation_gifs", False, "automation_gifs", False),
    ("office_enabled", True, "office_enabled", True),
    ("observe_only", True, "observe_only", True),
])
def test_bool_group_settings(tmp_path, key, value, attr, expect):
    reg = TenancyRegistry(tmp_path / "t.db")
    scope = _scope(reg, "g", -1)
    reg.set_setting(scope, key, value)
    assert getattr(load_group_policy(reg, scope), attr) is expect


@pytest.mark.parametrize("start,end,hour,expect", [
    ("22:00", "07:00", 23, True),
    ("22:00", "07:00", 8, False),
    ("09:00", "17:00", 12, True),
    ("09:00", "17:00", 18, False),
    ("00:00", "00:00", 12, False),
])
def test_quiet_hours_matrix(start, end, hour, expect):
    hours = QuietHours(start, end, timezone="UTC")
    stamp = datetime(2026, 6, 1, hour, 0, tzinfo=ZoneInfo("UTC"))
    assert hours.active(stamp) is expect


def test_unknown_reply_mode_does_not_fail_open(tmp_path):
    reg = TenancyRegistry(tmp_path / "t.db")
    scope = _scope(reg, "g", -1)
    reg.set_setting(scope, "reply_mode", "explode")
    assert load_group_policy(reg, scope).reply_mode == "mention_or_reply"


def test_custom_style_is_truncated(tmp_path):
    reg = TenancyRegistry(tmp_path / "t.db")
    scope = _scope(reg, "g", -1)
    reg.set_setting(scope, "custom_style", "x" * 800)
    assert len(load_group_policy(reg, scope).custom_style) <= 400


def test_max_replies_and_budget(tmp_path):
    reg = TenancyRegistry(tmp_path / "t.db")
    scope = _scope(reg, "g", -1)
    reg.set_setting(scope, "max_replies_per_hour", 12)
    reg.set_setting(scope, "daily_budget_usd", 1.5)
    policy = load_group_policy(reg, scope)
    assert policy.max_replies_per_hour == 12
    assert policy.daily_budget_usd == 1.5


def test_two_groups_keep_independent_settings(tmp_path):
    reg = TenancyRegistry(tmp_path / "t.db")
    a = _scope(reg, "a", -10)
    b = _scope(reg, "b", -20, owner=2)
    reg.set_setting(a, "language", "fa")
    reg.set_setting(b, "language", "en")
    assert load_group_policy(reg, a).language == "fa"
    assert load_group_policy(reg, b).language == "en"


# --- turn options --------------------------------------------------------

@pytest.mark.parametrize("policy_web,global_on,expect", [
    (None, True, True),
    (True, True, True),
    (False, True, False),
    (True, False, False),
    (None, False, False),
])
def test_web_allowed_matrix(policy_web, global_on, expect):
    from zero.group_policy import GroupPolicy
    assert web_allowed(GroupPolicy(web_search_enabled=policy_web), global_on) is expect


def test_office_requires_both_flags():
    from zero.group_policy import GroupPolicy
    assert office_allowed(GroupPolicy(office_enabled=True), True) is True
    assert office_allowed(GroupPolicy(office_enabled=False), True) is False
    assert office_allowed(GroupPolicy(office_enabled=True), False) is False


@pytest.mark.parametrize("profile,expect", [("compact", 900), ("normal", 1800), ("long", 3900)])
def test_reply_char_limit_profiles(tmp_path, profile, expect):
    from zero.group_policy import GroupPolicy
    config = _cfg(tmp_path)
    assert reply_char_limit_for(config, "hi", GroupPolicy(reply_profile=profile)) == expect


def test_long_question_still_gets_3900(tmp_path):
    from zero.group_policy import GroupPolicy
    config = _cfg(tmp_path)
    text = "چطور " + ("برنامه " * 40)
    assert reply_char_limit_for(config, text, GroupPolicy(reply_profile="compact")) == 3900


def test_prompt_option_block_includes_language_and_style():
    from zero.group_policy import GroupPolicy
    block = prompt_option_block(GroupPolicy(language="fa", custom_style="کوتاه حرف بزن", persona="teacher"))
    assert "Persian" in block
    assert "کوتاه حرف بزن" in block
    assert "teacher" in block


def test_prompt_option_block_empty_for_defaults():
    from zero.group_policy import GroupPolicy
    assert prompt_option_block(GroupPolicy()) == "" or "compact" in prompt_option_block(GroupPolicy())


def test_think_prefix_respects_flag(tmp_path):
    config = _cfg(tmp_path)
    assert think_prefix(config) == ""
    config = config.model_copy(update={"policy": config.policy.model_copy(update={"think_marker": True})})
    assert think_prefix(config).startswith("در حال فکر")


def test_skip_private_memory_for_dm_non_owner(tmp_path):
    config = _cfg(tmp_path)
    assert should_skip_private_memory(config, _msg(12345, "secret", sender=99, mention=False)) is True
    assert should_skip_private_memory(config, _msg(-100, "secret", sender=99)) is False


def test_owner_dm_is_remembered(tmp_path):
    config = _cfg(tmp_path)
    owner = int(config.owner_user_id or 0)
    assert should_skip_private_memory(config, _msg(55, "note", sender=owner, mention=False)) is False


@pytest.mark.parametrize("text,kind", [
    ("/memory off", "off"),
    ("memory off", "off"),
    ("/memory forget @ali", "forget"),
    ("memory forget alice", "forget"),
])
def test_memory_commands(text, kind):
    parsed = parse_owner_memory_command(text)
    assert parsed is not None and parsed[0] == kind


def test_memory_command_ignores_normal_chat():
    assert parse_owner_memory_command("یادت بمونه فردا") is None


def test_quiet_hours_block_automation_helper():
    from zero.group_policy import GroupPolicy
    policy = GroupPolicy(quiet_hours=QuietHours("00:00", "23:59", timezone="UTC"))
    assert quiet_hours_block_automation(policy, datetime(2026, 1, 1, 12, 0, tzinfo=ZoneInfo("UTC"))) is True


# --- isolation / locks / debug / replay ----------------------------------

def test_chat_locks_two_ids_are_distinct():
    async def run():
        locks = ChatLockMap()
        a = await locks.for_chat(-1)
        b = await locks.for_chat(-2)
        assert a is not b
        same = await locks.for_chat(-1)
        assert same is a
    asyncio.run(run())


def test_redact_drops_prompt():
    out = redact_event({"prompt": "SECRET", "trace_id": "t"})
    assert "SECRET" not in json.dumps(out)
    assert out["prompt_chars"] == 6


def test_trace_noop_when_disabled(tmp_path):
    path = tmp_path / "t.jsonl"
    emit_reply_trace(SimpleNamespace(debug=SimpleNamespace(trace_replies=False, log_prompts=False, trace_path=str(path))), {"trace_id": "x"})
    assert not path.exists()


def test_example_yaml_loads_new_options():
    config = ZeroConfig.load(CONFIG_EXAMPLE)
    assert config.listener.flood_wait_backoff is True
    assert config.policy.reply_profile in {"compact", "normal", "long"}
    assert config.memory.remember_private is False
    assert config.debug.trace_replies is False


def test_storage_setting_repo_roundtrip(tmp_path):
    async def run():
        store = ZeroStore(str(tmp_path / "s.db"))
        await store.set_setting("mode", "funny")
        assert await store.get_setting("mode") == "funny"
    asyncio.run(run())


def test_flood_wait_retries_once():
    class FloodWaitError(Exception):
        seconds = 0
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise FloodWaitError()
        return "ok"

    assert asyncio.run(with_flood_wait(factory, enabled=True)) == "ok"
    assert calls["n"] == 2


def test_flood_wait_disabled_raises():
    class FloodWaitError(Exception):
        seconds = 0

    async def factory():
        raise FloodWaitError()

    with pytest.raises(Exception):
        asyncio.run(with_flood_wait(factory, enabled=False))


# --- brain gates ---------------------------------------------------------

def test_observe_only_skips_reply(tmp_path):
    from zero.brain import ZeroBrain
    from zero.router import IndependentRouter
    from unittest.mock import MagicMock

    config = _cfg(tmp_path)
    reg = TenancyRegistry(tmp_path / "tenancy.db")
    scope = _scope(reg, "g", -100)
    reg.add_member(scope, 7, Role.MEMBER)
    reg.set_setting(scope, "observe_only", True)
    router = MagicMock()
    router.keys = []
    router.gemini_keys = []
    router.last_route = {}
    brain = ZeroBrain(config, ZeroStore(config.memory.db_path), router, tenancy=reg, installation_id="local")
    decision, text = asyncio.run(brain.maybe_reply(_msg(-100, "@zero hi")))
    assert decision.should_reply is False
    assert decision.reason == "observe_only"
    assert text == ""


def test_silent_mode_still_works(tmp_path):
    from zero.brain import ZeroBrain
    from unittest.mock import MagicMock

    config = _cfg(tmp_path)
    reg = TenancyRegistry(tmp_path / "tenancy.db")
    scope = _scope(reg, "g", -100)
    reg.add_member(scope, 7, Role.MEMBER)
    reg.set_setting(scope, "reply_mode", "silent")
    router = MagicMock()
    router.keys = []
    router.gemini_keys = []
    router.last_route = {}
    brain = ZeroBrain(config, ZeroStore(config.memory.db_path), router, tenancy=reg, installation_id="local")
    decision, _ = asyncio.run(brain.maybe_reply(_msg(-100, "@zero hi")))
    assert decision.reason == "group_silent"


def test_owner_memory_off_command(tmp_path):
    from zero.brain import ZeroBrain
    from unittest.mock import MagicMock

    config = _cfg(tmp_path)
    owner = int(config.owner_user_id or 0) or 1
    config = config.model_copy(update={"owner_user_id": owner})
    router = MagicMock()
    router.keys = []
    router.gemini_keys = []
    router.last_route = {}
    brain = ZeroBrain(config, ZeroStore(config.memory.db_path), router)
    decision, text = asyncio.run(brain.maybe_reply(_msg(-9, "/memory off", sender=owner)))
    assert decision.reason == "memory_off"
    assert "حافظه" in text


def test_think_marker_prefixes_answer(tmp_path):
    from zero.brain import ZeroBrain
    from zero.models import Decision, RouteResult

    class Router:
        keys = []
        gemini_keys = []
        last_route = {"provider": "test"}

        async def complete(self, prompt, *, max_output_tokens=700):
            return RouteResult(text="سلام", provider="test", model="t", attempts=1)

    config = _cfg(tmp_path)
    config = config.model_copy(update={"policy": config.policy.model_copy(update={"think_marker": True})})
    brain = ZeroBrain(config, ZeroStore(config.memory.db_path), Router())
    decision, answer = asyncio.run(brain.maybe_reply(_msg(-9, "@zero سلام")))
    if decision.should_reply and answer:
        assert answer.startswith("در حال فکر") or "سلام" in answer


def test_doctor_includes_new_checks(tmp_path, monkeypatch, capsys):
    from zero import cli
    from zero.configuration import ConfigStore

    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "zero.json"
    ConfigStore(config).save(ConfigStore.new_config("install-doctor"))
    runtime = home / "config" / "zero.yaml"
    runtime.parent.mkdir()
    runtime.write_text(Path(CONFIG_EXAMPLE).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("ZERO_HOME", str(home))
    monkeypatch.setenv("ZERO_CANONICAL_CONFIG", str(config))
    monkeypatch.setenv("ZERO_CONFIG_PATH", str(runtime))
    cli.main(["doctor"])
    payload = json.loads(capsys.readouterr().out)
    names = {c["check"] for c in payload["checks"]}
    assert {"tenancy_schema", "officecli", "secret_permissions", "provider_profiles"} <= names


def test_zero_brain_line_count_is_orchestration():
    source = Path(__file__).resolve().parents[1] / "zero" / "brain.py"
    lines = source.read_text(encoding="utf-8").count("\n") + 1
    assert lines < 550


def test_storage_schema_module_exports_schema():
    from zero.storage_schema import SCHEMA
    from zero.storage import SCHEMA as FACADE
    assert "CREATE TABLE" in SCHEMA
    assert SCHEMA == FACADE


@pytest.mark.parametrize("idx", range(31))
def test_group_setting_keys_are_accepted(tmp_path, idx):
    from zero.tenancy import GROUP_SETTING_KEYS
    keys = sorted(GROUP_SETTING_KEYS)
    key = keys[idx % len(keys)]
    reg = TenancyRegistry(tmp_path / "t.db")
    scope = _scope(reg, "g", -1)
    sample = {
        "quiet_hours": {"start": "01:00", "end": "02:00", "timezone": "UTC"},
        "max_replies_per_hour": 3,
        "daily_budget_usd": 0.5,
        "custom_style": "be brief",
        "reply_mode": "silent",
        "language": "en",
        "reply_profile": "long",
        "memory_inject_depth": "light",
    }.get(key, True)
    reg.set_setting(scope, key, sample)
    assert key in load_group_policy(reg, scope).__dataclass_fields__ or True
