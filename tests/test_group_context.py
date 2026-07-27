import asyncio,json
from zero.storage import ZeroStore
from zero.group_context import GroupContext
from zero.models import RouteResult,IncomingMessage
class R:
 async def complete(self,*a,**k): return RouteResult(json.dumps({'active_topics':['f'], 'recent_decisions':[], 'ongoing_questions':[], 'projects_discussed':[], 'important_recent_events':[], 'resolved_topics':[]}), 't','t',1)
async def run(tmp_path,n):
 s=ZeroStore(str(tmp_path/'x.db'));g=GroupContext(s,R())
 for i in range(n): await s.append_recent(-1,i%3+1,'u','user',f'message {i}',telegram_message_id=i+1)
 return await g.build(IncomingMessage(-1,'g',9,'x','trigger',message_id=999)),s

def test_incremental_no_padding_and_dedup(tmp_path):
 async def x():
  (live,_,m),_=await run(tmp_path,3);assert len(live.splitlines())==3 and m['recent_selected_count']==3
  (live2,_,m2),_=await run(tmp_path,0);assert live2=='' and m2['recent_selected_count']==0
 asyncio.run(x())
def test_backlog_summarizes_old_and_keeps_latest_raw(tmp_path):
 async def x():
  (live,summary,m),_=await run(tmp_path,40);assert len(live.splitlines())==20 and m['backlog_summarized_count']==20 and summary['active_topics']==['f']
 asyncio.run(x())
def test_group_isolation(tmp_path):
 async def x():
  s=ZeroStore(str(tmp_path/'x.db'));g=GroupContext(s,R());await s.append_recent(-1,1,'u','user','a',telegram_message_id=1);await s.append_recent(-2,1,'u','user','b',telegram_message_id=1)
  a=await g.build(IncomingMessage(-1,'a',1,'u','t'));b=await g.build(IncomingMessage(-2,'b',1,'u','t'));assert 'a' in a[0] and 'b' in b[0]
 asyncio.run(x())
