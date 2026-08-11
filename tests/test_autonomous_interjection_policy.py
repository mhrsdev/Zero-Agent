import asyncio
from pathlib import Path

from conftest import CONFIG_EXAMPLE
from zero.brain import ZeroBrain
from zero.config import ZeroConfig
from zero.models import IncomingMessage
from zero.social_awareness import SocialAwareness
from zero.storage import ZeroStore


class NoCallRouter:
    keys = []

    async def complete(self, *args, **kwargs):
        raise AssertionError("an active human conversation must not call the LLM")


def message(text: str, *, sender: int = 1, message_id: int = 0, **changes) -> IncomingMessage:
    values = dict(
        chat_id=-99, chat_title="group", sender_id=sender, sender_label=f"u{sender}",
        text=text, message_id=message_id, trace_id="interjection-test",
    )
    values.update(changes)
    return IncomingMessage(**values)


def make_brain(tmp_path: Path) -> tuple[ZeroBrain, ZeroStore]:
    config = ZeroConfig.load(CONFIG_EXAMPLE)
    config = config.model_copy(update={
        "memory": config.memory.model_copy(update={"db_path": str(tmp_path / "zero.db")}),
        "persona": config.persona.model_copy(update={
            "allow_random_interject": True,
            "interject_probability": 1.0,
            "min_interject_gap_seconds": 0,
        }),
    })
    store = ZeroStore(config.memory.db_path)
    return ZeroBrain(config, store, NoCallRouter()), store


def test_recent_two_person_exchange_is_classified_as_active_conversation(tmp_path: Path):
    async def scenario():
        store = ZeroStore(str(tmp_path / "social.db"))
        await store.append_recent(-99, 1, "u1", "user", "فردا میای بیرون؟", telegram_message_id=1)
        await store.append_recent(-99, 2, "u2", "user", "آره ساعت هشت خوبه", telegram_message_id=2)
        decision = await SocialAwareness(store).decide(message("باشه پس خبرت می‌کنم", sender=1, message_id=3))
        assert decision.should_ignore is True
        assert decision.reason == "active_human_conversation"

    asyncio.run(scenario())


def test_unrelated_message_is_never_an_autonomous_interjection_candidate():
    engine = SocialAwareness(None)
    current = message("باشه فردا ساعت هشت می‌بینمت")
    social = engine.evaluate(current)
    assert engine.allows_autonomous_interjection(current, social) is False


def test_random_chance_cannot_override_active_human_conversation(tmp_path: Path):
    async def scenario():
        brain, store = make_brain(tmp_path)
        await store.append_recent(-99, 1, "u1", "user", "فردا میای بیرون؟", telegram_message_id=1)
        await store.append_recent(-99, 2, "u2", "user", "آره ساعت هشت خوبه", telegram_message_id=2)
        decision, text = await brain.maybe_reply(message("باشه پس خبرت می‌کنم", sender=1, message_id=3))
        assert decision.should_reply is False
        assert decision.reason == "social_active_human_conversation"
        assert text == ""

    asyncio.run(scenario())


def test_relevant_interjection_decision_survives_generation_boundary(tmp_path: Path, monkeypatch):
    async def fake_handle(self, current, decision, intent):
        return decision, "ok"

    async def scenario():
        brain, _ = make_brain(tmp_path)
        monkeypatch.setattr(ZeroBrain, "_handle_no_media", fake_handle)
        decision, text = await brain.maybe_reply(message("دارم یه ربات برای مدیریت گروه می‌سازم"))
        assert text == "ok"
        assert decision.reason == "interject"
        assert decision.interject is True
        assert decision.continue_generation is True

    asyncio.run(scenario())
