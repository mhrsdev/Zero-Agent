import pytest

from zero.memory import build_group_summary
from zero.prompts import build_reply_prompt
from zero.storage import ZeroStore
from zero.config import ZeroConfig


@pytest.mark.asyncio
async def test_layered_user_memory_does_not_leak_between_same_name_users(tmp_path):
    store = ZeroStore(str(tmp_path / 'leak.db'))
    chat = -1001
    await store.add_medium_memory(chat, 'gold', 'A asked gold', participants=[111], source_message_ids=[10])
    await store.add_medium_memory(chat, 'unrelated', 'B own memory', participants=[222], source_message_ids=[11])
    await store.add_long_memory(chat, 'note', 'A private note', created_by=111, subject_user_id=111, source_message_ids=[10])
    a = await store.retrieve_layered_memory(chat, 'gold', sender_id=111)
    b = await store.retrieve_layered_memory(chat, 'gold', sender_id=222)
    assert any(r['summary'] == 'A asked gold' for r in a['medium'])
    assert all(r['summary'] != 'A asked gold' for r in b['medium'])
    assert all(r['content'] != 'A private note' for r in b['long'])


def test_prompt_has_canonical_sender_identity_and_does_not_use_label_as_key():
    cfg = ZeroConfig.load('/root/zero/config/zero.example.yaml')
    prompt = build_reply_prompt(
        cfg, mode='default', sender_label='Ali', chat_id=-1001, sender_id=222,
        user_text='سلام', reply_text='', recent=[
            {'sender_id': 111, 'sender_label': 'Ali', 'role': 'user', 'text': 'قیمت طلا؟'},
            {'sender_id': 222, 'sender_label': 'Ali', 'role': 'user', 'text': 'سلام'},
        ], group_summary='موضوعات پرتکرار: طلا(1)',
    )
    assert 'chat_id=-1001 sender_id=222' in prompt
    assert '"sender_id": 111' in prompt and '"sender_id": 222' in prompt
    assert 'sender_label فقط نمایشی' in prompt


def test_prompt_keeps_reply_target_identity_separate_from_current_sender():
    cfg = ZeroConfig.load('/root/zero/config/zero.example.yaml')
    prompt = build_reply_prompt(
        cfg, mode='default', sender_label='YSN RFD', chat_id=-1001, sender_id=222, message_id=88,
        user_text='زیرو جواب بده', reply_text='این متن متعلق به Loaded است',
        reply_sender_id=111, reply_sender_label='Loaded', reply_to_message_id=77,
        recent=[], group_summary='',
    )
    assert 'sender_id=222' in prompt
    assert 'reply_target_sender_id=111' in prompt
    assert 'reply_target_sender_label=Loaded' in prompt
    assert 'current_message_id=88' in prompt
    assert 'reply_target_message_id=77' in prompt
    assert 'reply_target_sender_id=111' in prompt
    assert 'این پیام متعلق به target است، نه فرستندهٔ فعلی' in prompt


def test_prompt_separates_personal_memory_from_group_context_and_hardens_identity():
    cfg = ZeroConfig.load('/root/zero/config/zero.example.yaml')
    prompt = build_reply_prompt(
        cfg, mode='default', sender_label='@owner', chat_id=-1001, sender_id=708,
        user_text='سلام', reply_text='', recent=[
            {'sender_id': 592, 'sender_label': '@MA_bombel', 'role': 'user', 'text': 'ممد جان'},
        ], group_summary='موضوعات گروه: برنامه‌نویسی',
        memory_context='[LONG_MEMORY] فقط متعلق به sender_id=708',
    )
    assert 'کانتکست حافظهٔ جدید، فقط متعلق به فرستندهٔ فعلی' in prompt
    assert '[LONG_MEMORY] فقط متعلق به sender_id=708' in prompt
    assert 'اگر بین متن کانتکست و هویت اختلاف بود، chat_id و sender_id معتبرند' in prompt
    assert 'نام یا خاطرهٔ کاربر دیگری را به فرستندهٔ فعلی نسبت نده' in prompt


@pytest.mark.asyncio
async def test_remember_reply_records_zero_as_assistant_sender(tmp_path):
    from zero.brain import ZeroBrain
    from zero.models import IncomingMessage
    from zero.router import IndependentRouter

    cfg = ZeroConfig.load('/root/zero/config/zero.example.yaml')
    store = ZeroStore(str(tmp_path / 'reply.db'))
    brain = ZeroBrain(cfg, store, IndependentRouter(cfg))
    brain.zero_user_id = 999
    message = IncomingMessage(
        chat_id=-1001, chat_title='group', sender_id=708,
        sender_label='@owner', text='سلام', message_id=12,
    )
    await brain.remember_reply(message, 'سلام رفیق')
    rows = await store.get_recent(-1001, limit=2)
    assert rows[-1]['role'] == 'assistant'
    assert rows[-1]['sender_id'] == 999
    assert rows[-1]['sender_label'] == 'Zero'
