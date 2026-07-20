import asyncio
import sqlite3
from zero.memory_v2.service import MemoryItem, MemoryV2Service
from zero.models import IncomingMessage

def msg(uid=1, chat=10, text='ریاضی'):
    return IncomingMessage(chat_id=chat, chat_title='g', sender_id=uid, sender_label='u', text=text, message_id=1)

def test_crud_supersede_and_isolation(tmp_path):
    async def run():
        s=MemoryV2Service(str(tmp_path/'v2.db'))
        old=await s.put(MemoryItem('', 'fact','group_user','education_track = تجربی','education_track=تجربی',1,10,group_id=10,subject='user',predicate='education_track',object='تجربی'))
        new=await s.put(MemoryItem('', 'fact','group_user','education_track = ریاضی','education_track=ریاضی',1,10,group_id=10,subject='user',predicate='education_track',object='ریاضی'))
        text,_=await s.context(msg()); assert new != old and 'ریاضی' in text and 'تجربی' not in text
        assert not (await s.context(msg(uid=2)))[0]
    asyncio.run(run())

def test_secret_shadow_and_session_scope(tmp_path, monkeypatch):
    monkeypatch.setenv('ZERO_MEMORY_V2_SHADOW','true'); monkeypatch.setenv('ZERO_MEMORY_V2_ENABLED','false')
    async def run():
        s=MemoryV2Service(str(tmp_path/'v2.db')); assert not s.sanitize('token=123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef')
        a=msg(text='میخوام پروژه رو deploy کنم'); await s.observe(a)
        assert s.shadow and not s.enabled and (await s.session_state(a))['user_goal']=='پروژه رو deploy کنم'
        assert (await s.session_state(msg(uid=2)))['user_goal'] is None
        assert (await MemoryV2Service(str(tmp_path/'v2.db')).session_state(a))['user_goal']=='پروژه رو deploy کنم'
    asyncio.run(run())

def test_rollback_on_bad_item(tmp_path):
    async def run():
        s=MemoryV2Service(str(tmp_path/'v2.db'))
        try: await s.put(MemoryItem('', 'fact','bad','x','x'))
        except ValueError: pass
        else: assert False
        assert sqlite3.connect(tmp_path/'v2.db').execute('select count(*) from memory_v2_items').fetchone()[0]==0
    asyncio.run(run())
