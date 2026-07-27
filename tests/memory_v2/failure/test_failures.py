import asyncio
import sqlite3
from zero.memory_v2.service import MemoryItem, MemoryV2Service
from zero.models import IncomingMessage

def msg(): return IncomingMessage(chat_id=1,chat_title='g',sender_id=1,sender_label='u',text='zero deployment',message_id=1)

def test_retrieval_failure_fails_closed_without_exception(tmp_path, monkeypatch):
    async def run():
        s=MemoryV2Service(str(tmp_path/'v2.db'))
        monkeypatch.setattr(s,'_search_sync',lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError('locked')))
        block,meta=await s.context(msg())
        assert block=='' and meta['error']=='search_failed'
    asyncio.run(run())

def test_invalid_write_rolls_back_and_no_duplicate_retry(tmp_path):
    async def run():
        s=MemoryV2Service(str(tmp_path/'v2.db'))
        item=MemoryItem('', 'fact','group_user','zero deployment','zero deployment',1,1,group_id=1,importance=1,confidence=1)
        first=await s.put(item); second=await s.put(item)
        assert first==second
        try: await s.put(MemoryItem('', 'fact','bad','x','x'))
        except ValueError: pass
        assert sqlite3.connect(tmp_path/'v2.db').execute('select count(*) from memory_v2_items').fetchone()[0]==1
    asyncio.run(run())

def test_session_stale_write_is_rejected(tmp_path):
    async def run():
        s=MemoryV2Service(str(tmp_path/'v2.db')); one=await s.update_session_state(msg(),patch={'user_goal':'deploy'})
        stale=await s.update_session_state(msg(),patch={'user_goal':'other'},expected_version=0)
        assert one['changed'] and stale['conflict'] and (await s.session_state(msg()))['user_goal']=='deploy'
    asyncio.run(run())
