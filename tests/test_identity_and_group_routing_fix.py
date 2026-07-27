import pytest

from zero.storage import ZeroStore


@pytest.mark.asyncio
async def test_profile_identity_uses_id_username_display_name_and_nickname(tmp_path):
    store = ZeroStore(str(tmp_path / 'identity.db'))
    chat_id = -1001
    await store.upsert_profile(
        chat_id,
        42,
        '@ali_new',
        username='ali_new',
        display_name='علی رضایی',
        nicknames=['علی‌جون'],
    )
    assert await store.find_users_by_identity(chat_id, '42') == [42]
    assert await store.find_users_by_identity(chat_id, '@ali_new') == [42]
    assert await store.find_users_by_identity(chat_id, 'علی رضایی') == [42]
    assert await store.find_users_by_identity(chat_id, 'علی‌جون') == [42]
    profile = await store.get_profile(chat_id, 42)
    assert profile['username'] == 'ali_new'
    assert profile['display_name'] == 'علی رضایی'


@pytest.mark.asyncio
async def test_profile_identity_updates_on_plain_message_metadata(tmp_path):
    store = ZeroStore(str(tmp_path / 'identity.db'))
    await store.upsert_profile(-1001, 42, '@ali_new', username='ali_new', display_name='علی')
    await store.upsert_profile(-1001, 42, 'علی رضایی', display_name='علی رضایی')
    profile = await store.get_profile(-1001, 42)
    assert profile['username'] == 'ali_new'
    assert profile['display_name'] == 'علی رضایی'
