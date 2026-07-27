import asyncio,sqlite3
from zero.memory_v2.service import MemoryItem,MemoryV2Service
from zero.models import IncomingMessage

def m():return IncomingMessage(1,'g',1,'u','query',message_id=1)
def test_missing_fts_fails_closed_and_process_continues(tmp_path):
 async def run():
  s=MemoryV2Service(str(tmp_path/'x.db'));c=sqlite3.connect(s.path);c.execute('drop table memory_v2_fts');c.close()
  block,meta=await s.context(m());assert block=='' and meta['error']=='search_failed'
  # No destructive repair: missing FTS stays absent.
  assert 'memory_v2_fts' not in [x[0] for x in sqlite3.connect(s.path).execute("select name from sqlite_master")]
 asyncio.run(run())
def test_missing_required_column_fails_closed(tmp_path):
 async def run():
  p=tmp_path/'x.db';c=sqlite3.connect(p);c.execute('create table memory_v2_schema(version integer primary key)');c.execute('insert into memory_v2_schema values(1)');c.execute('create table memory_v2_items(id text primary key)');c.execute('create virtual table memory_v2_fts using fts5(id,content,subjects)');c.commit();c.close()
  s=MemoryV2Service(str(p));block,meta=await s.context(m());assert block==''
 asyncio.run(run())
def test_readonly_write_failure_is_fail_open_for_session(tmp_path,monkeypatch):
 async def run():
  s=MemoryV2Service(str(tmp_path/'x.db'));original=s._conn
  def ro():raise sqlite3.OperationalError('attempt to write a readonly database')
  monkeypatch.setattr(s,'_conn',ro);result=await s.update_session_state(m(),patch={'user_goal':'x'});assert not result['changed']
  monkeypatch.setattr(s,'_conn',original);assert (await s.update_session_state(m(),patch={'user_goal':'x'}))['changed']
 asyncio.run(run())
