import asyncio, json, time
from zero.models import IncomingMessage, RouteResult
from zero.proactive_feedback import FeedbackService
from zero.proactive_followups import ProactiveFollowups
from zero.proactive_policy import PolicyEngine
from zero.storage import ZeroStore

NOW=2_000_000_000

def msg(text,mid=20,chat=-1,user=7):return IncomingMessage(chat,'g',user,'u',text,message_id=mid)
class Router:
 def __init__(self,text='bad'):self.text=text
 async def complete(self,*a,**k):return RouteResult(self.text,'x','x',1)

def seed(s,cid='c',chat=-1,user=7,topic='ثبت گوشی',sent_at=NOW-10):
 with s._conn() as c:
  c.execute("insert into proactive_followups(id,chat_id,subject_user_id,created_at,due_at,follow_up_type,topic_summary,goal,confidence,status,dedup_key) values(?,?,?,?,0,'task_outcome',?, 'g',.9,'postponed',?)",(cid,chat,user,100,topic,cid))
  c.execute("insert into proactive_policy_events(candidate_id,chat_id,subject_user_id,topic,event,created_at) values(?,?,?,?, 'sent',?)",(cid,chat,user,topic,sent_at))

def test_positive_feedback_is_idempotent_and_persistent(tmp_path):
 async def run():
  path=str(tmp_path/'x.db');s=ZeroStore(path);ProactiveFollowups(s,Router());seed(s);f=FeedbackService(s,Router(),now=lambda:NOW)
  assert (await f.observe(msg('مرسی، پیگیریت مفید بود')))['feedback_type']=='helpful'
  await f.observe(msg('مرسی، پیگیریت مفید بود'))
  with ZeroStore(path)._conn() as c:
   assert c.execute('select count(*) from proactive_followup_feedback').fetchone()[0]==1
   assert c.execute('select positive_count from proactive_feedback_preferences').fetchone()[0]==1
 asyncio.run(run())

def test_opt_out_persists_and_policy_blocks(tmp_path):
 async def run():
  path=str(tmp_path/'x.db');s=ZeroStore(path);ProactiveFollowups(s,Router());seed(s);f=FeedbackService(s,Router(),now=lambda:NOW)
  assert (await f.observe(msg('دیگه خودت پیگیر کارهام نشو')))['feedback_type']=='opt_out'
  assert not FeedbackService(ZeroStore(path),Router()).is_enabled(-1,7)
  with s._conn() as c:r=dict(c.execute('select * from proactive_followups where id="c"').fetchone())
  assert PolicyEngine(s).decide(r,NOW,local_hour=12).reason=='user_opt_out'
 asyncio.run(run())

def test_adaptive_timing_and_retry_are_bounded(tmp_path):
 async def run():
  s=ZeroStore(str(tmp_path/'x.db'));ProactiveFollowups(s,Router());seed(s);f=FeedbackService(s,Router(),now=lambda:NOW)
  await f.observe(msg('این پیگیری آزاردهنده بود'));assert f.adjust_delay(-1,7,10)==15
  seed(s,'c2',topic='کار دوم');await f.observe(msg('مرسی مفید بود',21));assert 7<=f.adjust_delay(-1,7,10)<=15
 asyncio.run(run())

def test_ignored_feedback_sweep_and_restart(tmp_path):
 path=str(tmp_path/'x.db');s=ZeroStore(path);ProactiveFollowups(s,Router());seed(s,sent_at=NOW-73*3600);f=FeedbackService(s,Router(),now=lambda:NOW);assert f.sweep_ignored()==1
 with ZeroStore(path)._conn() as c:assert c.execute("select feedback_type from proactive_followup_feedback").fetchone()[0]=='ignored'
 assert FeedbackService(ZeroStore(path),Router()).adjust_delay(-1,7,10)==15

def test_unrelated_and_model_failure_are_unknown_without_learning(tmp_path):
 class Broken:
  async def complete(self,*a,**k):raise RuntimeError('x')
 async def run():
  s=ZeroStore(str(tmp_path/'x.db'));ProactiveFollowups(s,Router());seed(s);f=FeedbackService(s,Broken(),now=lambda:NOW)
  assert (await f.observe(msg('درباره ثبت گوشی یه خبر دارم')))['feedback_type']=='unknown'
  with s._conn() as c:assert c.execute('select positive_count+negative_count+ignored_count from proactive_feedback_preferences').fetchone()[0]==0
 asyncio.run(run())

def test_completed_outcome_is_feedback_and_opt_out_prevents_new_candidates(tmp_path,monkeypatch):
 class CountingRouter(Router):
  def __init__(self):super().__init__();self.calls=0
  async def complete(self,*a,**k):self.calls+=1;return await super().complete(*a,**k)
 async def run():
  monkeypatch.setenv('ZERO_PROACTIVE_FOLLOWUP_ENABLED','true');monkeypatch.setenv('ZERO_PROACTIVE_FOLLOWUP_CREATE_ENABLED','true')
  s=ZeroStore(str(tmp_path/'x.db'));r=CountingRouter();p=ProactiveFollowups(s,r);seed(s)
  assert (await p.feedback.observe(msg('ثبت گوشی انجام شد')))['feedback_type']=='resolved_before'
  seed(s,'c2',topic='کار دوم');await p.feedback.observe(msg('دیگه خودت پیگیر کارهام نشو',21))
  result=await p.consider(msg('می‌خوام کار تازه‌ای انجام بدم',22));assert result['reason']=='user_opt_out'
 asyncio.run(run())


def test_feedback_scope_is_chat_and_user(tmp_path):
 async def run():
  s=ZeroStore(str(tmp_path/'x.db'));ProactiveFollowups(s,Router());seed(s);f=FeedbackService(s,Router(),now=lambda:NOW)
  assert (await f.observe(msg('مرسی مفید بود',chat=-2)))['feedback_type']=='none'
  assert (await f.observe(msg('مرسی مفید بود',user=8)))['feedback_type']=='none'
 asyncio.run(run())
