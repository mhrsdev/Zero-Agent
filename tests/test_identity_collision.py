import asyncio

import pytest

from zero.identity import canonical_user_key
from zero.storage import ZeroStore


def test_canonical_user_key_is_id_based_and_thread_scoped():
    assert canonical_user_key(-100123, 456) == 'chat:-100123:user:456'
    assert canonical_user_key(-100123, 456, 77) == 'chat:-100123:thread:77:user:456'
    assert canonical_user_key(-100123, 456) != canonical_user_key(-100123, 457)


@pytest.mark.asyncio
async def test_profiles_are_scoped_by_chat_and_sender_not_label(tmp_path):
    store = ZeroStore(str(tmp_path / 'identity.db'))
    await store.upsert_profile(-1001, 111, 'Ali', topics=['a'])
    await store.upsert_profile(-1002, 222, 'Ali', topics=['b'])
    await store.upsert_profile(-1001, 111, 'Renamed', topics=['c'])
    a = await store.get_profile(-1001, 111)
    b = await store.get_profile(-1002, 222)
    assert a['label'] == 'Renamed' and json_topics(a) == {'a', 'c'}
    assert b['label'] == 'Ali' and json_topics(b) == {'b'}


def json_topics(profile):
    import json
    return set(json.loads(profile['topics_json']))


@pytest.mark.asyncio
async def test_legacy_profile_without_chat_is_not_used_for_scoped_lookup(tmp_path):
    store = ZeroStore(str(tmp_path / 'identity.db'))
    await store.upsert_profile(0, 111, 'Legacy', topics=['ambiguous'])
    assert await store.get_profile(-1001, 111) is None


@pytest.mark.asyncio
async def test_rate_events_are_scoped_by_chat_when_chat_is_supplied(tmp_path):
    store = ZeroStore(str(tmp_path / 'rate.db'))
    await store.add_rate_event(111, 'reply', chat_id=-1001)
    assert await store.count_rate_events(111, 'reply', 60, chat_id=-1001) == 1
    assert await store.count_rate_events(111, 'reply', 60, chat_id=-1002) == 0
