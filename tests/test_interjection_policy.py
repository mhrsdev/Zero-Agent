import asyncio
from pathlib import Path

from zero.brain import ZeroBrain
from zero.config import ZeroConfig
from zero.models import IncomingMessage
from zero.storage import ZeroStore


class NoCallRouter:
    keys = []



def _brain(tmp_path: Path) -> ZeroBrain:
    config = ZeroConfig.load('/root/zero/config/zero.example.yaml')
    config = config.model_copy(update={
        'memory': config.memory.model_copy(update={'db_path': str(tmp_path / 'zero.db')}),
        'policy': config.policy.model_copy(update={'anti_spam_enabled': False}),
        'persona': config.persona.model_copy(update={'interject_probability': 1.0}),
    })
    return ZeroBrain(config, ZeroStore(config.memory.db_path), NoCallRouter())


def test_random_interjection_does_not_override_technical_discussion(tmp_path: Path, monkeypatch):
    async def scenario():
        monkeypatch.setattr('zero.brain.random.random', lambda: 0.0)
        brain = _brain(tmp_path)
        decision, text = await brain._pre_check(IncomingMessage(
            chat_id=-99, chat_title='t', sender_id=55, sender_label='@user',
            text='این traceback پایتون چرا میاد؟',
        ))
        assert decision is not None
        assert decision.should_reply is False
        assert decision.reason == 'social_not_addressed'
        assert text == ''

    asyncio.run(scenario())


def test_random_interjection_remains_available_for_relevant_neutral_chat(tmp_path: Path, monkeypatch):
    async def scenario():
        monkeypatch.setattr('zero.brain.random.random', lambda: 0.0)
        brain = _brain(tmp_path)
        decision, text = await brain._pre_check(IncomingMessage(
            chat_id=-99, chat_title='t', sender_id=55, sender_label='@user',
            text='امروز یه اتفاق جالب افتاد',
        ))
        assert decision is not None
        assert decision.reason == 'interject'
        assert decision.interject is True
        assert text == ''

    asyncio.run(scenario())
