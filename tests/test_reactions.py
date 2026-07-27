import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from zero.config import ReactionsConfig
from zero.models import IncomingMessage
from zero.reactions import (
    ReactionContext,
    ReactionService,
    explicit_reaction_request,
    choose_reaction,
    parse_reaction_command,
    should_react,
    summarize_reactions,
)
from zero.storage import ZeroStore


def msg(text: str, *, sender_id: int = 99, bot: bool = False) -> IncomingMessage:
    return IncomingMessage(
        chat_id=-1001, chat_title="test", sender_id=sender_id, sender_label="user",
        text=text, sender_is_bot=bot, trace_id="unittrace",
    )


def context(**changes):
    values = dict(owner_id=1, self_id=2, enabled=True, chance_percent=100, random_value=0.1)
    values.update(changes)
    return ReactionContext(**values)


def test_funny_message_is_eligible_without_llm():
    decision = should_react(msg("این جوک خیلی خنده‌دار بود 😂"), context())
    assert decision.should_react is True
    assert decision.allowed is True
    assert decision.reason == "funny"
    assert decision.skipped_reason is None
    assert decision.confidence > 0.8
    assert choose_reaction(msg("این جوک خیلی خنده‌دار بود 😂"), context()) in {"😂", "🤣"}


def test_serious_technical_message_is_skipped():
    decision = should_react(msg("این traceback پایتون را چطور debug کنم؟"), context())
    assert decision.should_react is False
    assert decision.skipped_reason == "technical_or_serious"
    assert decision.rate_limited is False


def test_sensitive_message_is_skipped():
    decision = should_react(msg("بحث سیاست و جنگ خیلی حساسه"), context())
    assert decision.should_react is False
    assert decision.skipped_reason == "sensitive_topic"


def test_bot_message_is_skipped():
    decision = should_react(msg("خیلی خنده‌دار بود 😂", bot=True), context())
    assert decision.should_react is False
    assert decision.skipped_reason == "bot_sender"


def test_self_message_is_skipped():
    decision = should_react(msg("خیلی خنده‌دار بود 😂", sender_id=2), context())
    assert decision.should_react is False
    assert decision.skipped_reason == "self_message"


def test_disabled_reactions_are_skipped():
    decision = should_react(msg("خیلی خنده‌دار بود 😂"), context(enabled=False))
    assert decision.should_react is False
    assert decision.skipped_reason == "disabled"


def test_explicit_reaction_uses_replied_message_context():
    assert explicit_reaction_request('این پیامم ری اکشن بزن') is True
    requested = replace(msg('این پیامم ری اکشن بزن'), reply_text='دقیقاً همینطوره')
    decision = should_react(requested, context(chance_percent=0))
    assert decision.should_react is True
    assert decision.emoji == '👍'
    assert decision.reason == 'explicit_reaction_contextual'
    assert should_react(msg('ری اکشن بزن'), context()).skipped_reason == 'no_contextual_signal'


def test_choose_approval_question_and_surprise_reactions():
    assert choose_reaction(msg("دقیقاً همینطوره"), context(random_value=0.2)) == "👍"
    assert choose_reaction(msg("واقعاً؟"), context(random_value=0.8)) == "🤔"
    assert choose_reaction(msg("وای باورم نمیشه"), context(random_value=0.8)) == "🤯"


def test_choose_approval_and_cringe_reactions():
    assert choose_reaction(msg("دمت گرم، عالی بود"), context()) == "👍"
    assert choose_reaction(msg("این خیلی کرینج بود"), context()) == "🫠"


def test_reaction_summary_parsing_uses_only_aggregate_counts():
    summary = summarize_reactions([
        {"reaction": {"emoticon": "👍"}, "count": 3},
        {"reaction": {"emoticon": "😂"}, "count": 2},
        {"reaction": {"emoticon": "👎"}, "count": 1},
    ])
    assert summary == {
        "total_reactions": 6,
        "top_emojis": ["👍", "😂", "👎"],
        "positive_score": 3,
        "funny_score": 2,
        "negative_score": 1,
    }


def test_panel_command_parsing():
    assert parse_reaction_command([]) == ("status", None)
    assert parse_reaction_command(["on"]) == ("on", None)
    assert parse_reaction_command(["chance", "100"]) == ("chance", 100)
    assert parse_reaction_command(["limit", "4"]) == ("limit", 4)
    assert parse_reaction_command(["cooldown", "300"]) == ("cooldown", 300)
    assert parse_reaction_command(["read", "off"]) == ("read_off", None)
    try:
        parse_reaction_command(["limit", "11"])
    except ValueError:
        pass
    else:
        raise AssertionError("invalid limit accepted")


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


def test_settings_db_cooldown_and_hourly_limit(tmp_path: Path):
    async def scenario():
        store = ZeroStore(str(tmp_path / "zero.db"))
        for key, value in {
            "reactions_enabled": "true",
            "reactions_chance": "100",
            "reactions_limit": "1",
            "reactions_cooldown_seconds": "300",
            "reactions_global_cooldown_seconds": "60",
            "reactions_read_enabled": "true",
        }.items():
            await store.set_setting(key, value)
        config = SimpleNamespace(owner_user_id=1, reactions=ReactionsConfig())
        client = FakeClient()
        service = ReactionService(config, store, client, self_id=2)
        status = await service.status()
        assert status["enabled"] is True and status["chance_percent"] == 100
        first = await service.maybe_react(FakeEvent(), msg("جوک خنده‌دار 😂", sender_id=50))
        assert first.should_react is True and len(client.calls) == 1
        second = await service.maybe_react(FakeEvent(), msg("جوک خنده‌دار 😂", sender_id=50))
        assert second.should_react is False and second.rate_limited is True
        assert second.skipped_reason == "duplicate_message"
        # New message / user hits the explicit hourly policy after the first send.
        another = SimpleNamespace(id=701, chat_id=-1001, get_input_chat=FakeEvent().get_input_chat)
        third = await service.maybe_react(another, msg("جوک خنده‌دار 😂", sender_id=51))
        assert third.should_react is False and third.skipped_reason == "hourly_rate_limit"
        assert await store.count_rate_events(0, "reaction_sent", 3600) == 1

    asyncio.run(scenario())
