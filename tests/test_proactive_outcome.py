import asyncio, json
from zero.models import RouteResult
from zero.proactive_outcome import OutcomeDetector, OutcomeResult
from zero.proactive_policy import PolicyDecision
from zero.proactive_followups import ProactiveFollowups
from zero.proactive_transport import TransportResult
from zero.storage import ZeroStore


def candidate(**kw):
 d={'id':'c1','chat_id':-1,'subject_user_id':7,'source_message_id':10,'created_at':100,'status':'evaluating','topic_summary':'ثبت گوشی','goal':'بررسی نتیجه ثبت گوشی'};d.update(kw);return d

def message(text,**kw):
 d={'chat_id':-1,'sender_id':7,'role':'user','text':text,'telegram_message_id':20,'reply_to_message_id':10,'created_at':200};d.update(kw);return d

class Router:
 def __init__(self,text='bad'):self.text=text
 async def complete(self,*a,**k):return RouteResult(self.text,'x','x',1)

def test_completed_task_is_resolved_and_persistent(tmp_path):
 async def run():
  path=str(tmp_path/'x.db');d=OutcomeDetector(ZeroStore(path),Router());r=await d.detect(candidate(),[message('ثبت گوشی انجام شد')]);assert r.status=='resolved' and r.evidence==20
  with ZeroStore(path)._conn() as c:assert c.execute('select status from proactive_followup_outcomes where candidate_id="c1"').fetchone()[0]=='resolved'
 asyncio.run(run())

def test_pending_and_unknown_evidence(tmp_path):
 async def run():
  d=OutcomeDetector(ZeroStore(str(tmp_path/'x.db')),Router())
  assert (await d.detect(candidate(id='a'),[message('ثبت گوشی هنوز انجام نشده')])).status=='pending'
  assert (await d.detect(candidate(id='b'),[message('امروز هوا خوبه',reply_to_message_id=None)])).status=='unknown'
 asyncio.run(run())

def test_malformed_model_output_and_failure_are_unknown(tmp_path):
 class Broken:
  async def complete(self,*a,**k):raise RuntimeError('provider')
 async def run():
  s=ZeroStore(str(tmp_path/'x.db'))
  assert (await OutcomeDetector(s,Router('bad')).detect(candidate(id='a'),[message('درباره ثبت گوشی خبر تازه دارم')])).status=='unknown'
  assert (await OutcomeDetector(s,Broken()).detect(candidate(id='b'),[message('درباره ثبت گوشی خبر تازه دارم')])).status=='unknown'
 asyncio.run(run())

def test_duplicate_detection_is_idempotent_and_terminal_candidate_resolves(tmp_path):
 async def run():
  s=ZeroStore(str(tmp_path/'x.db'));d=OutcomeDetector(s,Router());c=candidate(status='cancelled')
  assert (await d.detect(c,[])).status=='resolved';assert (await d.detect(c,[])).status=='resolved'
  with s._conn() as conn:assert conn.execute('select count(*) from proactive_followup_outcomes where candidate_id=?',(c['id'],)).fetchone()[0]==1
 asyncio.run(run())


class EvalRouter:
 async def complete(self,p,**k):
  if 'Write one short' in p:return RouteResult('پیگیری','x','x',1)
  return RouteResult(json.dumps({'version':1,'action':'send','confidence':.9,'postpone_hours':1,'reason_code':'x'}),'x','x',1)
class Transport:
 def __init__(self):self.calls=0
 async def send(self,*a):self.calls+=1;return TransportResult(True,'mock:k')
class Policy:
 def __init__(self):self.calls=0
 def decide(self,*a):self.calls+=1;return PolicyDecision('allow','allowed')
 def record_decision(self,*a):pass
 def record(self,*a):pass
class FixedOutcome:
 def __init__(self,status):self.status=status
 async def detect(self,*a):return OutcomeResult(self.status,'test',None,.9)

def insert(s):
 with s._conn() as c:c.execute("insert into proactive_followups(id,chat_id,subject_user_id,source_message_id,created_at,due_at,follow_up_type,topic_summary,goal,confidence,status,dedup_key) values('c',-1,7,10,0,0,'task_outcome','t','g',.9,'pending','d')")

def test_resolved_skips_policy_and_transport(tmp_path):
 async def run():
  s=ZeroStore(str(tmp_path/'x.db'));t=Transport();p=ProactiveFollowups(s,EvalRouter(),transport=t);insert(s);policy=Policy();p.policy=policy;p.outcomes=FixedOutcome('resolved');await p.tick('w')
  with s._conn() as c:r=c.execute('select status,cancel_reason from proactive_followups').fetchone()
  assert policy.calls==0 and t.calls==0 and r['status']=='cancelled' and r['cancel_reason']=='outcome:test'
 asyncio.run(run())

def test_pending_and_unknown_continue_to_policy(tmp_path):
 for state in ('pending','unknown'):
  async def run(state=state):
   s=ZeroStore(str(tmp_path/(state+'.db')));t=Transport();p=ProactiveFollowups(s,EvalRouter(),transport=t);insert(s);policy=Policy();p.policy=policy;p.outcomes=FixedOutcome(state);await p.tick('w');assert policy.calls==1 and t.calls==1
  asyncio.run(run())
