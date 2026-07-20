import asyncio,json,time
from zero.proactive_followups import ProactiveFollowups
from zero.storage import ZeroStore
from zero.models import IncomingMessage,RouteResult
class R:
 def __init__(self,action='postpone'):self.action=action
 async def complete(self,p,*a,**k):
  if 'should_schedule' in p:return RouteResult(json.dumps({'should_schedule':True,'confidence':.9,'follow_up_type':'task_outcome','topic':'topic','goal':'goal','delay_hours':6,'sensitivity':'normal','intrusiveness':'low'}),'t','t',1)
  return RouteResult(json.dumps({'version':1,'action':self.action,'confidence':.9,'postpone_hours':2,'reason_code':'test'}),'t','t',1)
def msg():return IncomingMessage(-1,'g',7,'u','message',message_id=1)
def test_claim_is_atomic_and_shadow_send(tmp_path,monkeypatch):
 async def x():
  monkeypatch.setenv('ZERO_PROACTIVE_FOLLOWUP_ENABLED','true');monkeypatch.setenv('ZERO_PROACTIVE_FOLLOWUP_CREATE_ENABLED','true');monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_START','0');monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_END','24');s=ZeroStore(str(tmp_path/'x.db'));p=ProactiveFollowups(s,R('send'));assert (await p.consider(msg()))['created']
  with s._conn() as c:c.execute('update proactive_followups set due_at=?',(int(time.time())-1,))
  a,b=await asyncio.gather(p.tick('a'),p.tick('b'));assert len(a)+len(b)==1 and (a+b)[0]['would_send']
 asyncio.run(x())
def test_invalid_plan_postpones(tmp_path,monkeypatch):
 async def x():
  monkeypatch.setenv('ZERO_PROACTIVE_FOLLOWUP_ENABLED','true');monkeypatch.setenv('ZERO_PROACTIVE_FOLLOWUP_CREATE_ENABLED','true');s=ZeroStore(str(tmp_path/'x.db'));p=ProactiveFollowups(s,R());await p.consider(msg());
  with s._conn() as c:c.execute('update proactive_followups set due_at=?',(int(time.time())-1,))
  p.router=type('Bad',(),{'complete':lambda *_,**__: asyncio.sleep(0,result=RouteResult('bad','t','t',1))})();out=await p.tick();assert out[0]['action']=='postpone'
 asyncio.run(x())
