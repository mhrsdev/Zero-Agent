import asyncio
import sqlite3

from zero.storage import ZeroStore


async def reserve(store, trace):
    return await store.reserve_incoming_message(
        platform='telegram', account_scope='listener.session', chat_id=-1001,
        message_id=77, thread_id=None, sender_id=10, trace_id=trace,
    )


def test_concurrent_reserve_has_one_owner_and_one_llm_slot(tmp_path):
    async def scenario():
        store = ZeroStore(str(tmp_path / 'zero.db'))
        claims = await asyncio.gather(*(reserve(store, f't{i}') for i in range(2)))
        assert sum(int(x['claimed']) for x in claims) == 1
        assert sorted(x['status'] for x in claims) == ['processing', 'processing']

    asyncio.run(scenario())


def test_replied_message_is_skipped_after_new_store_restart(tmp_path):
    async def scenario():
        db = str(tmp_path / 'zero.db')
        first = ZeroStore(db)
        assert (await reserve(first, 'first'))['claimed']
        await first.mark_incoming_message_replied(
            platform='telegram', account_scope='listener.session', chat_id=-1001,
            message_id=77, reply_message_id=88, trace_id='first',
        )
        second = ZeroStore(db)
        claim = await reserve(second, 'after-restart')
        assert claim['claimed'] is False
        assert claim['status'] == 'replied'
        assert claim['reply_message_id'] == 88

    asyncio.run(scenario())


def test_failed_send_is_terminal_and_does_not_duplicate_send(tmp_path):
    async def scenario():
        store = ZeroStore(str(tmp_path / 'zero.db'))
        assert (await reserve(store, 'first'))['claimed']
        await store.mark_incoming_message_failed(
            platform='telegram', account_scope='listener.session', chat_id=-1001,
            message_id=77, trace_id='first', reason='TimeoutError',
        )
        retry_event = await reserve(store, 'retry-event')
        assert retry_event['claimed'] is True
        assert retry_event['status'] == 'processing'
        row = sqlite3.connect(str(tmp_path / 'zero.db')).execute(
            'SELECT status, reason, attempt_count FROM incoming_message_dedup'
            ' WHERE platform=? AND account_scope=? AND chat_id=? AND message_id=?',
            ('telegram', 'listener.session', -1001, 77),
        ).fetchone()
        assert row == ('processing', '', 2)

    asyncio.run(scenario())


def test_edited_replied_message_cannot_be_reprocessed(tmp_path):
    async def scenario():
        store = ZeroStore(str(tmp_path / 'zero.db'))
        assert (await reserve(store, 'first'))['claimed']
        await store.mark_incoming_message_replied(
            platform='telegram', account_scope='listener.session', chat_id=-1001,
            message_id=77, reply_message_id=88, trace_id='first',
        )
        claim = await store.reserve_incoming_message(
            platform='telegram', account_scope='listener.session', chat_id=-1001,
            message_id=77, thread_id=None, sender_id=10, trace_id='edited', reprocess=True,
        )
        assert claim['claimed'] is False
        assert claim['status'] == 'replied'

    asyncio.run(scenario())


def test_edited_unanswered_message_can_be_reprocessed(tmp_path):
    async def scenario():
        store = ZeroStore(str(tmp_path / 'zero.db'))
        assert (await reserve(store, 'first'))['claimed']
        await store.mark_incoming_message_expired(
            platform='telegram', account_scope='listener.session', chat_id=-1001,
            message_id=77, trace_id='first', reason='no_need',
        )
        claim = await store.reserve_incoming_message(
            platform='telegram', account_scope='listener.session', chat_id=-1001,
            message_id=77, thread_id=None, sender_id=10, trace_id='edited', reprocess=True,
        )
        assert claim['claimed'] is True
        assert claim['status'] == 'processing'

    asyncio.run(scenario())
