import sqlite3
import time

import pytest

from zero.semantic_memory import SemanticUserMemory
from zero.storage import ZeroStore
from zero.deferred_memory import DeferredMemory
from zero.models import IncomingMessage


@pytest.mark.asyncio
async def test_medium_memory_dedup_and_conflict_resolution(tmp_path):
    store = ZeroStore(str(tmp_path / 'zero.db'))
    first = await store.add_medium_memory(1, 'project', 'نسخهٔ اول', confidence=.7)
    same = await store.add_medium_memory(1, 'project', '  نسخهٔ اول  ', confidence=.8)
    newer = await store.add_medium_memory(1, 'project', 'نسخهٔ اصلاح‌شده', confidence=.9)
    assert first == same == newer
    rows = await store.retrieve_layered_memory(1, 'project')
    assert len(rows['medium']) == 1
    assert rows['medium'][0]['summary'] == 'نسخهٔ اصلاح‌شده'


@pytest.mark.asyncio
async def test_long_memory_dedup_and_conflict_revision(tmp_path):
    store = ZeroStore(str(tmp_path / 'zero.db'))
    first = await store.add_long_memory(1, 'preference', 'چای دوست دارد', created_by=7, subject_user_id=9)
    same = await store.add_long_memory(1, 'preference', ' چای دوست دارد ', created_by=7, subject_user_id=9)
    changed = await store.add_long_memory(1, 'preference', 'قهوه دوست دارد', created_by=7, subject_user_id=9)
    assert first == same == changed
    with sqlite3.connect(tmp_path / 'zero.db') as con:
        row = con.execute('select content,revision from long_term_memory where memory_id=?', (first,)).fetchone()
        assert row == ('قهوه دوست دارد', 2)
        assert con.execute("select count(*) from long_term_memory where chat_id=1 and category='preference' and status='active'").fetchone()[0] == 1


def test_semantic_memory_same_value_is_deduped(tmp_path):
    memory = SemanticUserMemory(tmp_path / 'zero.db')
    a = memory.candidate(chat_id=1, sender_id=2, category='interest', key='topic', value='AI', confidence=.9, source_text='a')
    first = memory.approve(a, 2)
    b = memory.candidate(chat_id=1, sender_id=2, category='interest', key='topic', value='AI', confidence=.9, source_text='b')
    second = memory.approve(b, 2)
    assert first == second
    assert len(memory.retrieve(1, 2)) == 1


def test_deferred_continuation_requires_scope_or_topic(tmp_path):
    memory = DeferredMemory(tmp_path / 'zero.db')
    with sqlite3.connect(tmp_path / 'zero.db') as con:
        now = int(time.time())
        con.execute("insert into deferred_memories(chat_id,sender_id,status,title,details,state_json,created_at,updated_at) values(?,?,?,?,?,?,?,?)", (1,2,'collecting','مدرسه','فردا مدرسه','{}',now,now))
        con.commit()
    unrelated_future = IncomingMessage(chat_id=1, chat_title='x', sender_id=2, sender_label='u', text='فردا قرار کریمی')
    assert memory.should_process(unrelated_future) is False
    related = IncomingMessage(chat_id=1, chat_title='x', sender_id=2, sender_label='u', text='فردا ساعت ۸ مدرسه')
    assert memory.should_process(related) is True
