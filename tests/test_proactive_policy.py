import asyncio, json, os
from datetime import datetime, timezone
from zero.proactive_policy import PolicyEngine, PolicyDecision
from zero.proactive_followups import ProactiveFollowups
from zero.proactive_transport import TransportResult
from zero.storage import ZeroStore
from zero.models import RouteResult

NOW=2_000_000_000

def row(**kw):
 d={'id':'c1','chat_id':-10,'subject_user_id':7,'topic_summary':'topic-a'};d.update(kw);return d

def test_user_limit_boundary_is_scoped_and_persistent(tmp_path,monkeypatch):
 monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_START','0');monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_END','24')
 path=str(tmp_path/'x.db');s=ZeroStore(path);p=PolicyEngine(s)
 assert p.decide(row(),NOW).action=='allow';p.record(row(),'sent',NOW-72*3600+1)
 assert p.decide(row(),NOW).reason=='user_rate_limit'
 assert PolicyEngine(ZeroStore(path)).decide(row(subject_user_id=8),NOW).action=='allow'
 assert PolicyEngine(ZeroStore(path)).decide(row(),NOW+2).action=='allow'

def test_group_limit_boundary_and_restart(tmp_path,monkeypatch):
 monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_START','0');monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_END','24')
 path=str(tmp_path/'x.db');p=PolicyEngine(ZeroStore(path));p.record(row(subject_user_id=1,id='a'),'sent',NOW-10);p.record(row(subject_user_id=2,id='b'),'sent',NOW-9)
 assert PolicyEngine(ZeroStore(path)).decide(row(subject_user_id=3),NOW).reason=='group_rate_limit'
 assert p.decide(row(chat_id=-11,subject_user_id=3),NOW).action=='allow'
 assert p.decide(row(subject_user_id=3),NOW+24*3600+1).action=='allow'

def test_topic_lock_scope_and_resolution(tmp_path,monkeypatch):
 monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_START','0');monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_END','24')
 p=PolicyEngine(ZeroStore(str(tmp_path/'x.db')));p.record(row(),'open',NOW-2)
 assert p.decide(row(),NOW).reason=='topic_open'
 assert p.decide(row(topic_summary='topic-b'),NOW).action=='allow'
 assert p.decide(row(subject_user_id=8),NOW).action=='allow'
 p.record(row(),'resolved',NOW-1);assert p.decide(row(),NOW).action=='allow'

def test_quiet_hours_boundaries_and_retry(tmp_path,monkeypatch):
 monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_START','9');monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_END','22')
 p=PolicyEngine(ZeroStore(str(tmp_path/'x.db')))
 assert p.decide(row(),NOW,local_hour=9).action=='allow'
 assert p.decide(row(),NOW,local_hour=21).action=='allow'
 d=p.decide(row(),NOW,local_hour=22);assert d.action=='postpone' and d.reason=='quiet_hours' and d.retry_at>NOW
 assert p.decide(row(),NOW,local_hour=3).action=='postpone'
 monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_START','22');monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_END','9')
 assert p.decide(row(),NOW,local_hour=23).action=='allow'
 assert p.decide(row(),NOW,local_hour=8).action=='allow'
 assert p.decide(row(),NOW,local_hour=9).action=='postpone'

def test_invalid_timezone_falls_back_to_utc(tmp_path,monkeypatch):
 monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_START','0');monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_END','1');monkeypatch.setenv('ZERO_PROACTIVE_FALLBACK_TIMEZONE','invalid/zone');monkeypatch.delenv('TZ',raising=False)
 p=PolicyEngine(ZeroStore(str(tmp_path/'x.db')));assert p.decide(row(user_timezone='also/invalid'),0).action=='allow'


def test_malformed_candidate_fails_closed_and_decision_persists_without_text(tmp_path,monkeypatch):
 monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_START','0');monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_END','24')
 s=ZeroStore(str(tmp_path/'x.db'));p=PolicyEngine(s);assert p.decide({},NOW).action=='block';p.record_decision(row(),PolicyDecision('block','test'),NOW)
 with s._conn() as c:
  r=c.execute('select action,reason from proactive_policy_events').fetchone();cols={x[1] for x in c.execute('pragma table_info(proactive_policy_events)')}
 assert tuple(r)==('block','test') and 'text' not in cols

class Router:
 def __init__(self,action='send'):self.action=action
 async def complete(self,p,**k):
  if 'Write one short' in p:return RouteResult('سلام، آخرش چطور شد؟','x','x',1)
  return RouteResult(json.dumps({'version':1,'action':self.action,'confidence':.9,'postpone_hours':2,'reason_code':'model'}),'x','x',1)
class Transport:
 def __init__(self):self.calls=0
 async def send(self,*a):self.calls+=1;return TransportResult(True,'mock')
class FixedPolicy:
 def __init__(self,d):self.d=d;self.records=[]
 def decide(self,row,now=None):return self.d
 def record_decision(self,row,d,now=None):self.records.append(d)
 def record(self,row,event,now=None):self.records.append(event)

def insert_due(s):
 with s._conn() as c:c.execute("insert into proactive_followups(id,chat_id,subject_user_id,created_at,due_at,follow_up_type,topic_summary,goal,confidence,status,dedup_key) values('c',-1,7,0,0,'task_outcome','t','g',.9,'pending','d')")

def test_scheduler_policy_allow_calls_transport_once(tmp_path,monkeypatch):
 async def run():
  monkeypatch.setenv('ZERO_PROACTIVE_FOLLOWUP_SEND_ENABLED','false');s=ZeroStore(str(tmp_path/'x.db'));t=Transport();p=ProactiveFollowups(s,Router(),transport=t);insert_due(s);p.policy=FixedPolicy(PolicyDecision('allow','allowed'));o=await p.tick('w');assert t.calls==1 and o[0]['would_send']
 asyncio.run(run())

def test_scheduler_postpone_and_block_never_call_transport(tmp_path):
 async def run(decision):
  s=ZeroStore(str(tmp_path/(decision.action+'.db')));t=Transport();p=ProactiveFollowups(s,Router(),transport=t);insert_due(s);p.policy=FixedPolicy(decision);await p.tick('w')
  with s._conn() as c:r=c.execute('select status,cancel_reason,due_at from proactive_followups').fetchone()
  assert t.calls==0 and r['cancel_reason']==decision.reason and (r['status']=='postponed' if decision.action=='postpone' else r['status']=='blocked')
 awaitable=[PolicyDecision('postpone','quiet',NOW+3600),PolicyDecision('block','limit')]
 for d in awaitable:asyncio.run(run(d))

def test_non_send_reevaluation_still_records_policy_and_never_transports(tmp_path):
 async def run(action):
  s=ZeroStore(str(tmp_path/(action+'.db')));t=Transport();p=ProactiveFollowups(s,Router(action),transport=t);insert_due(s);fp=FixedPolicy(PolicyDecision('allow','allowed'));p.policy=fp;await p.tick('w');assert t.calls==0 and fp.records
 for action in ('cancel','postpone'):asyncio.run(run(action))


def test_policy_exception_fails_closed(tmp_path):
 class Bad:
  def decide(self,*a):raise RuntimeError('x')
  def record_decision(self,*a):pass
 async def run():
  s=ZeroStore(str(tmp_path/'x.db'));t=Transport();p=ProactiveFollowups(s,Router(),transport=t);insert_due(s);p.policy=Bad();await p.tick('w')
  with s._conn() as c:r=c.execute('select status,cancel_reason from proactive_followups').fetchone()
  assert t.calls==0 and r['status']=='postponed' and r['cancel_reason']=='policy_error'
 asyncio.run(run())
