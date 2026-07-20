import asyncio,json
from zero.models import RouteResult
from zero.proactive_rollout import RolloutController, ProactiveProductionHealth
from zero.proactive_followups import ProactiveFollowups
from zero.proactive_policy import PolicyDecision
from zero.proactive_outcome import OutcomeResult
from zero.storage import ZeroStore

class Client:
 def __init__(self):self.calls=0
 async def send_message(self,*a):self.calls+=1;return type('R',(),{'id':77})()
class Router:
 async def complete(self,p,**k):
  if 'Write one short' in p:return RouteResult('پیگیری','x','x',1)
  return RouteResult(json.dumps({'version':1,'action':'send','confidence':.9,'postpone_hours':1,'reason_code':'ready'}),'x','x',1)
class Policy:
 def decide(self,*a):return PolicyDecision('allow','allowed')
 def record_decision(self,*a):pass
 def record(self,*a):pass
class Outcome:
 async def detect(self,*a):return OutcomeResult('unknown','none',None,0)
def seed(s,cid='c',chat=-10,user=20):
 with s._conn() as c:c.execute("insert into proactive_followups(id,chat_id,subject_user_id,created_at,due_at,follow_up_type,topic_summary,goal,priority,confidence,status,dedup_key) values(?,?,?,0,0,'task_outcome','t','g','normal',.9,'pending',?)",(cid,chat,user,cid))

def configure(monkeypatch,chats='-10',users='20',percent='100',send='true',disabled='false'):
 monkeypatch.setenv('ZERO_PROACTIVE_FOLLOWUP_SEND_ENABLED',send);monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_CHAT_IDS',chats);monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_USER_IDS',users);monkeypatch.setenv('ZERO_PROACTIVE_CANARY_PERCENT',percent);monkeypatch.setenv('ZERO_PROACTIVE_ADMIN_DISABLED',disabled);monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_START','0');monkeypatch.setenv('ZERO_PROACTIVE_ALLOWED_HOUR_END','24')

def test_allowlist_empty_and_nonmember_fail_closed(tmp_path,monkeypatch):
 s=ZeroStore(str(tmp_path/'x.db'));configure(monkeypatch,chats='',users='');r=RolloutController(s);assert not r.decide(-10,20).allowed
 configure(monkeypatch,chats='-11',users='20');r=RolloutController(s);assert r.decide(-10,20).reason=='chat_not_allowed'

def test_allowlisted_e2e_real_receipt_and_restart_state(tmp_path,monkeypatch):
 async def run():
  configure(monkeypatch);path=str(tmp_path/'x.db');s=ZeroStore(path);client=Client();p=ProactiveFollowups(s,Router(),client=client);p.policy=Policy();p.outcomes=Outcome();seed(s);out=await p.tick('w');assert client.calls==1 and out[0]['action']=='send'
  with ZeroStore(path)._conn() as c:assert tuple(c.execute('select send_state,receipt from proactive_followup_outbox').fetchone())==('sent','77')
  assert RolloutController(ZeroStore(path)).metrics()['real_sends']==1
 asyncio.run(run())

def test_nonallowlisted_never_calls_real_transport(tmp_path,monkeypatch):
 async def run():
  configure(monkeypatch,chats='-99');s=ZeroStore(str(tmp_path/'x.db'));client=Client();p=ProactiveFollowups(s,Router(),client=client);p.policy=Policy();p.outcomes=Outcome();seed(s);out=await p.tick('w');assert client.calls==0 and out[0]['reason']=='chat_not_allowed'
  with s._conn() as c:assert c.execute('select count(*) from proactive_followup_outbox').fetchone()[0]==0
 asyncio.run(run())

def test_canary_is_deterministic_and_bounded(tmp_path,monkeypatch):
 s=ZeroStore(str(tmp_path/'x.db'))
 for pct in ('0','1','5','10','25','50','100'):
  configure(monkeypatch,percent=pct);r=RolloutController(s);assert r.decide(-10,20).allowed==r.decide(-10,20).allowed
 configure(monkeypatch,percent='0');assert not RolloutController(s).decide(-10,20).allowed
 configure(monkeypatch,percent='100');assert RolloutController(s).decide(-10,20).allowed

def test_kill_switch_preserves_candidate_retry_and_outbox(tmp_path,monkeypatch):
 s=ZeroStore(str(tmp_path/'x.db'));configure(monkeypatch,disabled='true');p=ProactiveFollowups(s,Router(),client=Client());seed(s)
 with s._conn() as c:c.execute("update proactive_followups set retry_count=2,next_retry_at=123 where id='c'")
 assert not p.rollout.decide(-10,20).allowed
 with s._conn() as c:r=c.execute("select retry_count,next_retry_at,status from proactive_followups").fetchone();assert tuple(r)==(2,123,'pending')

def test_wildcard_disabled_by_default(tmp_path,monkeypatch):
 configure(monkeypatch,chats='*',users='*');monkeypatch.delenv('ZERO_PROACTIVE_ALLOW_WILDCARD',raising=False);r=RolloutController(ZeroStore(str(tmp_path/'x.db')));assert not r.decide(-10,20).allowed and not r.config_sane

def test_full_chain_receipt_outcome_feedback(tmp_path,monkeypatch):
 from zero.models import IncomingMessage
 async def run():
  configure(monkeypatch);s=ZeroStore(str(tmp_path/'x.db'));client=Client();p=ProactiveFollowups(s,Router(),client=client);seed(s);await p.tick('w')
  feedback=await p.feedback.observe(IncomingMessage(chat_id=-10,sender_id=20,message_id=99,text='ممنون مفید بود',chat_title='t',sender_label='u'))
  with s._conn() as c:
   assert c.execute("select status from proactive_followup_outcomes where candidate_id='c'").fetchone()[0]=='unknown'
   assert c.execute("select feedback_type from proactive_followup_feedback where candidate_id='c'").fetchone()[0]=='helpful'
  assert feedback['recorded']
 asyncio.run(run())

def test_rollback_modes_never_call_real_client(tmp_path,monkeypatch):
 async def one(name,**cfg):
  configure(monkeypatch,**cfg);s=ZeroStore(str(tmp_path/(name+'.db')));client=Client();p=ProactiveFollowups(s,Router(),client=client);p.policy=Policy();p.outcomes=Outcome();seed(s);await p.tick('w');return client.calls
 assert asyncio.run(one('disabled',send='false'))==0
 assert asyncio.run(one('admin',disabled='true'))==0
 assert asyncio.run(one('canary',percent='0'))==0


def test_health_check_and_monitoring_are_structured(tmp_path,monkeypatch):
 configure(monkeypatch);s=ZeroStore(str(tmp_path/'x.db'));p=ProactiveFollowups(s,Router(),client=Client());p.rollout.heartbeat();h=ProactiveProductionHealth(s,p.rollout,p.transport).check();assert h['status']=='healthy' and all(k in h['checks'] for k in ('scheduler_alive','lease_cleanup','outbox_consistency','retry_queue_consistency','policy_storage','sqlite_integrity','migration_version','transport_selection','configuration_sanity'))
 m=h['metrics'];assert all(k in m for k in ('scheduler_health','transport_health','outbox_depth','retry_queue','policy_blocks','policy_postpones','allowlist_rejects','canary_accepts','real_sends','mock_sends','ambiguous_reservations','retry_exhausted','deadline_expired'))
