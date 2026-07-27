import asyncio
from pathlib import Path

from zero.models import IncomingMessage
from zero.social_awareness import SocialAwareness, classify_emotion, parse_awareness_command
from zero.storage import ZeroStore


def message(text: str, **changes) -> IncomingMessage:
    values = dict(chat_id=-100, chat_title='group', sender_id=10, sender_label='@user', text=text, trace_id='socialtest')
    values.update(changes)
    return IncomingMessage(**values)


def test_awareness_panel_parser_uses_required_runtime_settings():
    assert parse_awareness_command(['on']) == ('social_awareness_enabled', True)
    assert parse_awareness_command(['delay', 'off']) == ('human_delay_enabled', False)
    assert parse_awareness_command(['reaction', 'on']) == ('reaction_awareness_enabled', True)


def test_emotion_awareness_is_lightweight_and_conservative():
    assert classify_emotion('خیلی ناراحتم، امروز خراب شد') == 'sad'
    assert classify_emotion('این traceback پایتون چرا میاد؟') == 'technical'
    assert classify_emotion('جوک خیلی خنده‌دار بود 😂') == 'funny'
    assert classify_emotion('دعوا نکنید') == 'conflict'


def test_direct_conversation_and_already_answered_threads_are_silent():
    engine = SocialAwareness(None, random_value=0.0)
    direct = engine.evaluate(message('@ali نظرت چیه؟'))
    assert direct.should_ignore and not direct.should_reply
    replied_to_someone = engine.evaluate(message('ممنون، جوابم را گرفتم', reply_text='پاسخ یک عضو دیگر'))
    assert replied_to_someone.should_ignore and replied_to_someone.reason == 'conversation_in_progress'


def test_emotion_blocks_inappropriate_action_and_keeps_direct_technical_help():
    engine = SocialAwareness(None, random_value=0.0)
    sad = engine.evaluate(message('خیلی ناراحتم 😔', mention_zero=True))
    assert sad.should_ignore and not sad.should_react
    technical = engine.evaluate(message('زیرو این ارور پایتون چیه؟', mention_zero=True))
    assert technical.should_reply and not technical.should_react


def test_funny_message_prefers_reaction_over_unnecessary_reply():
    engine = SocialAwareness(None, random_value=0.0)
    decision = engine.evaluate(message('این جوک خیلی خنده‌دار بود 😂'))
    assert decision.should_react and decision.should_ignore


def test_interesting_project_can_request_bounded_curiosity():
    engine = SocialAwareness(None, random_value=0.0)
    decision = engine.evaluate(message('دارم یه ربات تلگرامی برای مدیریت گروه می‌سازم'))
    assert decision.should_ask and decision.should_reply
    assert decision.reason == 'bounded_curiosity'


def test_feedback_requires_multiple_distinct_users_before_adapting(tmp_path: Path):
    async def scenario():
        store = ZeroStore(str(tmp_path / 'social.db'))
        engine = SocialAwareness(store, random_value=0.0)
        await engine.record_feedback(-100, 1, 'خفه شو')
        state = await engine.group_state(-100)
        assert state['social_reputation'] == 0
        await engine.record_feedback(-100, 2, 'زیادی حرف میزنی')
        await engine.record_feedback(-100, 3, 'لازم نیست جواب بدی')
        state = await engine.group_state(-100)
        assert state['social_reputation'] < 0
        assert state['social_confidence'] < 1.0
    asyncio.run(scenario())


def test_positive_feedback_and_negative_reactions_update_aggregate_state(tmp_path: Path):
    async def scenario():
        store = ZeroStore(str(tmp_path / 'social.db'))
        engine = SocialAwareness(store, random_value=0.0)
        await engine.record_feedback(-100, 1, 'دمت گرم، عالی بود')
        await engine.record_feedback(-100, 2, 'مرسی')
        await engine.record_reaction_feedback(-100, positive=5, negative=0)
        state = await engine.group_state(-100)
        assert state['positive_feedback_count'] >= 3
        await engine.record_reaction_feedback(-100, positive=0, negative=4)
        state = await engine.group_state(-100)
        assert state['negative_feedback_count'] >= 1
    asyncio.run(scenario())


def test_human_answer_does_not_supersede_direct_zero_call_but_supersedes_chat(tmp_path: Path):
    async def scenario():
        store = ZeroStore(str(tmp_path / 'social.db'))
        engine = SocialAwareness(store)
        direct = message('زیرو این ارور چیه؟', mention_zero=True)
        await store.append_recent(-100, 10, '@user', 'user', direct.text)
        await store.append_recent(-100, 11, '@helper', 'user', 'این خطا از تنظیمات env هست')
        assert await engine.superseded_by_recent_human(direct) is False
        chat_message = message('این ارور چیه؟')
        await store.append_recent(-100, 12, '@user2', 'user', chat_message.text)
        await store.append_recent(-100, 13, '@helper', 'user', 'این خطا از تنظیمات env هست')
        assert await engine.superseded_by_recent_human(chat_message) is True
    asyncio.run(scenario())
