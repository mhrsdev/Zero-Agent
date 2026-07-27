import asyncio,sqlite3
from zero.memory_v2.service import MemoryItem,MemoryV2Service
from zero.models import IncomingMessage

def msg(user=1):return IncomingMessage(1,'g',user,'u','prefer same',message_id=1)
def item(user=1,value='same'):return MemoryItem('', 'profile','group_user',f'zero preference {value}',f'zero preference {value}',user,1,group_id=1,subject='user',predicate='preference',object=value,importance=1,confidence=1)

def test_disk_full_rolls_back_base_and_fts(tmp_path):
 async def run():
  s=MemoryV2Service(str(tmp_path/'v2.db'));c=sqlite3.connect(s.path);c.execute("create trigger full before insert on memory_v2_items begin select raise(abort,'database or disk is full');end");c.close()
  try:await s.put(item())
  except sqlite3.DatabaseError:pass
  with sqlite3.connect(s.path) as c:assert c.execute('select count(*) from memory_v2_items').fetchone()[0]==c.execute('select count(*) from memory_v2_fts').fetchone()[0]==0
 asyncio.run(run())

def test_metrics_failure_is_swallowed(tmp_path,monkeypatch):
 async def run():
  s=MemoryV2Service(str(tmp_path/'v2.db'));original=s._conn
  class Bad:
   def execute(self,*a,**k):raise sqlite3.OperationalError('disk I/O error')
  monkeypatch.setattr(s,'_conn',lambda:Bad());await s.metric('t','x',{})
  monkeypatch.setattr(s,'_conn',original);assert (await s.put(item()))
 asyncio.run(run())

def test_twenty_concurrent_duplicate_and_isolated_writes(tmp_path):
 async def run():
  s=MemoryV2Service(str(tmp_path/'v2.db'));ids=await asyncio.gather(*(s.put(item()) for _ in range(20)));assert len(set(ids))==1
  await asyncio.gather(*(s.put(item(i,f'v{i}')) for i in range(1,21)))
  with sqlite3.connect(s.path) as c:assert c.execute("select count(distinct user_id) from memory_v2_items where status='active'").fetchone()[0]==20
 asyncio.run(run())

def test_cancelled_task_does_not_poison_next_operation(tmp_path,monkeypatch):
 async def run():
  s=MemoryV2Service(str(tmp_path/'v2.db'));event=asyncio.Event()
  async def blocked(*a,**k):event.set();await asyncio.sleep(20)
  monkeypatch.setattr(s,'put',blocked);task=asyncio.create_task(s.observe(msg()));await event.wait();task.cancel()
  try:await task
  except asyncio.CancelledError:pass
  monkeypatch.undo();assert await s.put(item())
 asyncio.run(run())

def test_unsupported_schema_fails_closed_without_startup_crash(tmp_path):
 p=tmp_path/'v2.db';c=sqlite3.connect(p);c.execute('create table memory_v2_schema(version integer)');c.execute('insert into memory_v2_schema values(999)');c.commit();c.close()
 async def run():
  s=MemoryV2Service(str(p));assert not s.healthy
  block,meta=await s.context(msg());assert block=='' and meta['health']=='unavailable'
 asyncio.run(run())
