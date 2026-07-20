import asyncio,time
from zero.memory_v2.service import MemoryItem,MemoryV2Service
from zero.models import IncomingMessage

def m(text,user=1,reply=None,target=None): return IncomingMessage(10,'g',user,'u',text,message_id=1,reply_sender_id=reply,resolved_target_user_id=target)

from zero.brain import ZeroBrain


def test_target_resolution_priority_without_cross_user_fallback():
    assert ZeroBrain._memory_target(None,m('@member کیه',user=1,target=2)) == (2,'mentioned_user')
    assert ZeroBrain._memory_target(None,m('این کیه',user=1,reply=3)) == (3,'reply_target')
    assert ZeroBrain._memory_target(None,m('من کی هستم',user=1)) == (1,'speaker')

def test_old_temporal_event_not_presented_as_today(tmp_path):
 async def run():
  s=MemoryV2Service(str(tmp_path/'v2.db'));old=time.time()-7*86400
  iid=await s.put(MemoryItem('', 'episode','group_user','قرار شد امروز امتحانم کنی','قرار شد امروز امتحانم کنی',1,10,group_id=10,importance=1,confidence=1,created_at=old))
  import sqlite3
  c=sqlite3.connect(s.path);c.execute('update memory_v2_items set updated_at=? where id=?',(old,iid));c.commit();c.close()
  block,meta=await s.context(m('امروز امتحانم کن'))
  assert block=='' and meta['temporal_rejected']==1
 asyncio.run(run())

def test_persistent_fact_survives_age(tmp_path):
 async def run():
  s=MemoryV2Service(str(tmp_path/'v2.db'));old=time.time()-180*86400
  await s.put(MemoryItem('', 'profile','group_user','نام کاربر مهراس است','نام کاربر مهراس است',1,10,group_id=10,importance=1,confidence=1,updated_at=old,created_at=old))
  block,_=await s.context(m('نام کاربر چیست'))
  assert 'مهراس' in block
 asyncio.run(run())

def test_target_scope_uses_mentioned_or_reply_user_only(tmp_path):
 async def run():
  s=MemoryV2Service(str(tmp_path/'v2.db'))
  await s.put(MemoryItem('', 'profile','group_user','معرفی target_user','معرفی target_user',2,10,group_id=10,importance=1,confidence=1))
  await s.put(MemoryItem('', 'profile','group_user','معرفی speaker_user','معرفی speaker_user',1,10,group_id=10,importance=1,confidence=1))
  block,_=await s.context(m('@target_user کیه',target=2),target_user_id=2,identity_lookup=True)
  assert 'target_user' in block and 'speaker_user' not in block
 asyncio.run(run())
