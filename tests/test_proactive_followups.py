import asyncio,json
from zero.proactive_followups import ProactiveFollowups
from zero.storage import ZeroStore
from zero.models import IncomingMessage,RouteResult
class R:
 def __init__(self,d):self.d=d
 async def complete(self,*a,**k):return RouteResult(json.dumps(self.d),'t','t',1)
def msg(t):return IncomingMessage(-1,'g',7,'u',t,message_id=3)
def test_candidate_created_without_future_text(tmp_path,monkeypatch):
 async def x():
  monkeypatch.setenv('ZERO_PROACTIVE_FOLLOWUP_ENABLED','true');monkeypatch.setenv('ZERO_PROACTIVE_FOLLOWUP_CREATE_ENABLED','true')
  d={'version':1,'should_schedule':True,'confidence':.8,'follow_up_type':'repair_or_purchase','topic':'phone registration','goal':'check outcome','delay_hours':48,'sensitivity':'normal','intrusiveness':'low'};s=ZeroStore(str(tmp_path/'x.db'));o=await ProactiveFollowups(s,R(d)).consider(msg('x'));assert o['created'];
  with s._conn() as c:r=c.execute('select goal,status from proactive_followups').fetchone();assert r['goal']=='check outcome' and r['status']=='pending'
 asyncio.run(x())
def test_vague_or_sensitive_is_rejected(tmp_path,monkeypatch):
 async def x():
  monkeypatch.setenv('ZERO_PROACTIVE_FOLLOWUP_ENABLED','true');monkeypatch.setenv('ZERO_PROACTIVE_FOLLOWUP_CREATE_ENABLED','true')
  d={'should_schedule':True,'confidence':.8,'follow_up_type':'health_or_wellbeing','topic':'x','goal':'x','delay_hours':24,'sensitivity':'sensitive','intrusiveness':'low'};assert not (await ProactiveFollowups(ZeroStore(str(tmp_path/'x.db')),R(d)).consider(msg('x')))['created']
 asyncio.run(x())
