import asyncio, time
from concurrent.futures import ThreadPoolExecutor
from zero.proactive_scheduler import SchedulerIntelligence
from zero.proactive_followups import ProactiveFollowups
from zero.storage import ZeroStore

NOW=2_000_000_000

def add(s,id,chat,user,priority='normal',created=None,due=None,deadline=None,status='pending',postpones=0,retries=0,max_retries=3,parent=None):
 with s._conn() as c:c.execute("""insert into proactive_followups(id,chat_id,subject_user_id,created_at,due_at,deadline_at,follow_up_type,topic_summary,goal,priority,confidence,status,dedup_key,postpone_count,retry_count,max_retries,parent_candidate_id) values(?,?,?,?,?,?,'task_outcome',?,'g',?,.9,?,?,?,?,?,?)""",(id,chat,user,created or NOW-100,due if due is not None else NOW-1,deadline,id,priority,status,id,postpones,retries,max_retries,parent))

def setup(tmp_path):
 s=ZeroStore(str(tmp_path/'x.db'))
 class R:pass
 p=ProactiveFollowups(s,R());return s,p.scheduler

def test_priority_and_stable_order(tmp_path):
 s,q=setup(tmp_path);add(s,'normal',-1,1);add(s,'critical',-2,2,'critical');add(s,'a',-3,3,'high',created=NOW-50);add(s,'b',-4,4,'high',created=NOW-50)
 rows=q.claim_due('w',10,NOW);assert [r['id'] for r in rows]==['critical','a','b','normal']

def test_fairness_prevents_one_scope_monopoly(tmp_path):
 s,q=setup(tmp_path)
 for i in range(4):add(s,'a'+str(i),-1,1,'high')
 for i in range(2):add(s,'b'+str(i),-2,2,'normal')
 rows=q.claim_due('w',4,NOW);scopes=[(r['chat_id'],r['subject_user_id']) for r in rows];assert scopes.count((-1,1))==2 and scopes.count((-2,2))==2

def test_aging_and_postpone_boosts_are_bounded(tmp_path):
 s,q=setup(tmp_path);young={'priority':'high','created_at':NOW,'postpone_count':0,'deadline_at':None};old={'priority':'low','created_at':NOW-400*86400,'postpone_count':100,'deadline_at':None}
 assert q.score(old,NOW)>q.score({'priority':'low','created_at':NOW,'postpone_count':0,'deadline_at':None},NOW)
 assert q.score(old,NOW)==q.score({'priority':'low','created_at':NOW-800*86400,'postpone_count':200,'deadline_at':None},NOW)
 assert q.score(old,NOW)>q.score(young,NOW)-250

def test_deadline_expiry_near_deadline_and_exact_boundary(tmp_path):
 s,q=setup(tmp_path);add(s,'expired',-1,1,deadline=NOW);add(s,'near',-2,2,'normal',deadline=NOW+60);add(s,'plain',-3,3,'normal')
 rows=q.claim_due('w',5,NOW);assert [r['id'] for r in rows][0]=='near' and 'expired' not in [r['id'] for r in rows]
 with s._conn() as c:assert c.execute("select status from proactive_followups where id='expired'").fetchone()[0]=='expired'

def test_policy_postpone_never_crosses_deadline_or_increments_retry(tmp_path):
 s,q=setup(tmp_path);add(s,'c',-1,1,deadline=NOW+1800)
 q.policy_postpone('c',NOW+7200,'quiet_hours',NOW)
 with s._conn() as c:r=c.execute("select due_at,retry_count,postpone_count from proactive_followups where id='c'").fetchone()
 assert NOW<r[0]<NOW+1800 and r[1]==0 and r[2]==1

def test_retry_backoff_jitter_cap_budget_and_restart(tmp_path):
 path=str(tmp_path/'x.db');s=ZeroStore(path)
 class R:pass
 p=ProactiveFollowups(s,R());q=p.scheduler;add(s,'c',-1,1,max_retries=3)
 delays=[]
 for n in range(3):
  q.technical_failure('c','provider_transient',NOW+n)
  with s._conn() as c:r=c.execute("select retry_count,next_retry_at,status from proactive_followups where id='c'").fetchone()
  if r[1] is not None:delays.append(r[1]-(NOW+n))
  if n==0:q=ProactiveFollowups(ZeroStore(path),R()).scheduler
 assert 900<=delays[0]<=990 and 3600<=delays[1]<=3960 and r[2]=='permanent_failed'

def test_cancellation_propagates_and_stops_outbox(tmp_path):
 s,q=setup(tmp_path);add(s,'parent',-1,1);add(s,'child',-1,1,parent='parent',status='retryable_failed');q.outbox.reserve('child','w',NOW);q.cancel('parent','user_opt_out',NOW)
 with s._conn() as c:
  assert {r[0] for r in c.execute("select status from proactive_followups where id in ('parent','child')")}=={'cancelled'}
  assert c.execute("select send_state from proactive_followup_outbox where candidate_id='child'").fetchone()[0]=='permanent_failed'

def test_valid_and_expired_leases_and_two_workers(tmp_path):
 s,q=setup(tmp_path);add(s,'c',-1,1);first=q.claim_due('a',1,NOW);assert first and not q.claim_due('b',1,NOW+10);recovered=q.claim_due('b',1,NOW+901);assert recovered[0]['id']=='c'

def test_concurrent_claim_is_single_and_restart_safe(tmp_path):
 path=str(tmp_path/'x.db');s=ZeroStore(path)
 class R:pass
 q=ProactiveFollowups(s,R()).scheduler;add(s,'c',-1,1)
 with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda w:q.claim_due(w,1,NOW),('a','b')))
 assert sum(len(x) for x in results)==1
 q2=ProactiveFollowups(ZeroStore(path),R()).scheduler;assert q2.claim_due('c',1,NOW+10)==[]

def test_optout_and_terminal_candidates_are_not_claimed(tmp_path):
 s,q=setup(tmp_path);add(s,'c',-1,1)
 with s._conn() as c:c.execute("insert into proactive_feedback_preferences(chat_id,subject_user_id,proactive_enabled,updated_at) values(-1,1,0,?)",(NOW,))
 q.propagate_disabled(NOW);assert q.claim_due('w',5,NOW)==[]
 with s._conn() as c:assert c.execute("select status from proactive_followups where id='c'").fetchone()[0]=='cancelled'

def test_schema_migration_is_idempotent_and_timezone_is_preserved(tmp_path):
 path=str(tmp_path/'x.db');s=ZeroStore(path)
 class R:pass
 ProactiveFollowups(s,R());ProactiveFollowups(ZeroStore(path),R())
 with s._conn() as c:cols={r[1] for r in c.execute('pragma table_info(proactive_followups)')}
 assert {'deadline_at','timezone','retry_count','max_retries','next_retry_at','last_attempt_at','postpone_count','parent_candidate_id'}<=cols
