from conftest import CONFIG_EXAMPLE
import asyncio
from unittest.mock import AsyncMock
from pathlib import Path

from zero.brain import ZeroBrain
from zero.config import ZeroConfig
from zero.models import IncomingMessage
from zero.storage import ZeroStore


class NoCallRouter:
    keys = []

    async def complete(self, *args, **kwargs):
        raise AssertionError("limit challenge must not call the LLM")


def test_real_configured_limit_offers_challenge_and_bonus_bypasses_refusal(tmp_path: Path):
    async def scenario():
        config = ZeroConfig.load(CONFIG_EXAMPLE)
        config = config.model_copy(update={
            'memory': config.memory.model_copy(update={'db_path': str(tmp_path / 'zero.db')}),
            'policy': config.policy.model_copy(update={
                'user_max_replies_per_window': 2,
                'user_max_replies_per_day': 10,
                'user_window_seconds': 1800,
            }),
        })
        store = ZeroStore(config.memory.db_path)
        brain = ZeroBrain(config, store, NoCallRouter())
        message = IncomingMessage(chat_id=-99, chat_title='t', sender_id=55, sender_label='u', text='زیرو جواب بده')
        await store.add_rate_event(55, 'reply', message.chat_id)
        await store.add_rate_event(55, 'reply', message.chat_id)

        decision, text = await brain._pre_check(message)
        assert decision.reason == 'limit_challenge'
        assert 'مرحله ۱' in text
        active = await store.get_limit_challenge_active(55, -99)

        answer_only = IncomingMessage(chat_id=-99, chat_title='t', sender_id=55, sender_label='u', text=active['answer'].strip('[]\"'))
        decision, text = await brain._pre_check(answer_only)
        assert decision.reason == 'limit_challenge'
        assert '+5' in text
        assert (await store.get_limit_challenge_progress(55, -99))['bonus_quota'] == 5

        await store.upsert_limit_challenge_progress(55, -99, bonus_quota=1)
        decision, text = await brain._pre_check(message)
        assert decision is not None and decision.should_reply and decision.continue_generation and text == ''
        assert (await store.get_limit_challenge_progress(55, -99))['bonus_quota'] == 0
    asyncio.run(scenario())


def test_zero_user_limits_disable_only_human_quota(tmp_path: Path):
    async def scenario():
        config = ZeroConfig.load(CONFIG_EXAMPLE)
        config = config.model_copy(update={
            'memory': config.memory.model_copy(update={'db_path': str(tmp_path / 'zero.db')}),
            'policy': config.policy.model_copy(update={
                'user_max_replies_per_window': 0,
                'user_max_replies_per_day': 0,
                'anti_spam_enabled': False,
            }),
        })
        store = ZeroStore(config.memory.db_path)
        brain = ZeroBrain(config, store, NoCallRouter())
        message = IncomingMessage(chat_id=-99, chat_title='t', sender_id=55, sender_label='u', text='زیرو جواب بده')
        for _ in range(100):
            await store.add_rate_event(55, 'reply', message.chat_id)
        decision, text = await brain._pre_check(message)
        assert decision is not None and decision.should_reply and decision.continue_generation and text == ''
    asyncio.run(scenario())


def test_bot_chain_limit_is_silent_only_for_mynovachatbot(tmp_path: Path):
    async def scenario():
        config = ZeroConfig.load(CONFIG_EXAMPLE)
        config = config.model_copy(update={'memory': config.memory.model_copy(update={'db_path': str(tmp_path / 'zero.db')})})
        store = ZeroStore(config.memory.db_path)
        brain = ZeroBrain(config, store, NoCallRouter())
        message = IncomingMessage(
            chat_id=-99, chat_title='t', sender_id=8252811591,
            sender_label='@MyNovaChatBot', sender_is_bot=True, text='زیرو ادامه بده',
        )
        for _ in range(config.policy.bot_max_chain_turns):
            await store.add_rate_event(message.sender_id, 'bot_reply', message.chat_id)
        decision, text = await brain._pre_check(message)
        assert decision.reason == 'bot_chain_silent'
        assert text == ''
    asyncio.run(scenario())


def test_search_command_reaches_normal_reply_path(tmp_path: Path):
    async def scenario():
        config = ZeroConfig.load(CONFIG_EXAMPLE)
        config = config.model_copy(update={'memory': config.memory.model_copy(update={'db_path': str(tmp_path / 'zero.db')})})
        brain = ZeroBrain(config, ZeroStore(config.memory.db_path), NoCallRouter())
        decision, text = await brain._pre_check(IncomingMessage(
            chat_id=-99, chat_title='t', sender_id=55, sender_label='@user', text='/search آخرین اخبار',
        ))
        assert decision is not None
        assert decision.should_reply and decision.continue_generation
        assert text == ''
    asyncio.run(scenario())


def test_search_command_without_query_returns_usage_without_llm(tmp_path: Path):
    async def scenario():
        config = ZeroConfig.load(CONFIG_EXAMPLE)
        config = config.model_copy(update={'memory': config.memory.model_copy(update={'db_path': str(tmp_path / 'zero.db')})})
        brain = ZeroBrain(config, ZeroStore(config.memory.db_path), NoCallRouter())

        decision, text = await brain.maybe_reply(IncomingMessage(
            chat_id=-99, chat_title='t', sender_id=55, sender_label='@user', text='/search',
        ))

        assert decision.should_reply is True
        assert decision.reason == 'search_usage'
        assert text == 'بعد از /search موضوع جستجو رو بنویس.'
    asyncio.run(scenario())


def test_social_awareness_silences_direct_member_conversation_before_llm(tmp_path: Path):
    async def scenario():
        config = ZeroConfig.load(CONFIG_EXAMPLE)
        config = config.model_copy(update={'memory': config.memory.model_copy(update={'db_path': str(tmp_path / 'zero.db')})})
        brain = ZeroBrain(config, ZeroStore(config.memory.db_path), NoCallRouter())
        decision, text = await brain._pre_check(IncomingMessage(
            chat_id=-99, chat_title='t', sender_id=55, sender_label='u', text='@ali تو نظرت چیه؟',
        ))
        assert decision is not None and decision.should_reply is False
        assert decision.reason == 'social_conversation_in_progress'
        assert text == ''
    asyncio.run(scenario())


def test_direct_zero_trigger_is_not_silenced_by_sensitive_social_context(tmp_path: Path):
    async def scenario():
        config = ZeroConfig.load(CONFIG_EXAMPLE)
        config = config.model_copy(update={'memory': config.memory.model_copy(update={'db_path': str(tmp_path / 'zero.db')})})
        brain = ZeroBrain(config, ZeroStore(config.memory.db_path), NoCallRouter())
        decision, text = await brain._pre_check(IncomingMessage(
            chat_id=-99, chat_title='t', sender_id=55, sender_label='u', text='زیرو خیلی ناراحتم، کمکم کن',
        ))
        assert decision is not None and decision.should_reply and decision.continue_generation and text == ''
    asyncio.run(scenario())


def test_direct_gif_request_enters_media_path_without_mention(tmp_path: Path):
    async def scenario():
        config = ZeroConfig.load(CONFIG_EXAMPLE)
        config = config.model_copy(update={"memory": config.memory.model_copy(update={"db_path": str(tmp_path / "zero.db")})})
        brain = ZeroBrain(config, ZeroStore(config.memory.db_path), NoCallRouter())
        brain._should_interject = AsyncMock(return_value=False)
        decision, text = await brain._pre_check(IncomingMessage(
            chat_id=-99, chat_title="t", sender_id=55, sender_label="u",
            text="یه گیف خنده دار بفرست",
        ))
        assert decision is not None
        assert decision.should_reply and decision.continue_generation
        assert text == ""
    asyncio.run(scenario())
