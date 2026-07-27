from __future__ import annotations
import asyncio,json,os,re,time,uuid
from .proactive_feedback import FeedbackService
from .proactive_outcome import OutcomeDetector
from .proactive_policy import PolicyDecision, PolicyEngine
from .proactive_rollout import ProactiveProductionHealth, RolloutController
from .proactive_scheduler import SchedulerIntelligence
from .proactive_transport import Outbox, TransportResult, select_transport
TYPES={'task_outcome','event_check_in','celebration','travel_return','exam_or_deadline','repair_or_purchase','health_or_wellbeing','project_progress','promise_or_commitment'}
SENSITIVE={'health_or_wellbeing'}
class ProactiveFollowups:
 def __init__(self,store,router,transport=None,client=None):
  self.store,self.router=store,router;self._schema();self.feedback=FeedbackService(store,router);self.policy=PolicyEngine(store);self.outcomes=OutcomeDetector(store,router);self.rollout=RolloutController(store);self.rollout_enforced=transport is None;self.transport=transport or select_transport(client);self.outbox=Outbox(store);self.scheduler=SchedulerIntelligence(store,self.outbox);self.production_health=ProactiveProductionHealth(store,self.rollout,self.transport)
 def _schema(self):
  with self.store._conn() as c:
   c.execute("CREATE TABLE IF NOT EXISTS proactive_followups(id TEXT PRIMARY KEY,chat_id INTEGER NOT NULL,subject_user_id INTEGER NOT NULL,source_message_id INTEGER,created_at INTEGER,due_at INTEGER,follow_up_type TEXT,topic_summary TEXT,goal TEXT,priority TEXT,sensitivity TEXT,confidence REAL,status TEXT,last_evaluated_at INTEGER,evaluation_count INTEGER DEFAULT 0,sent_at INTEGER,resolved_at INTEGER,cancel_reason TEXT,dedup_key TEXT UNIQUE,version INTEGER DEFAULT 1,claim_at INTEGER,lease_until INTEGER,worker_id TEXT)")
   cols={r[1] for r in c.execute('pragma table_info(proactive_followups)')}
   for name in ('claim_at','lease_until','worker_id','send_reserved_at','send_lease_until','send_worker_id','send_attempt_count','send_state','final_message_hash','last_send_error_code'):
    if name not in cols:c.execute(f"alter table proactive_followups add column {name} {'INTEGER' if name in ('claim_at','lease_until','send_reserved_at','send_lease_until','send_attempt_count') else 'TEXT'}")
 async def consider(self,message):
  if os.getenv('ZERO_PROACTIVE_FOLLOWUP_ENABLED','false').lower()!='true' or os.getenv('ZERO_PROACTIVE_FOLLOWUP_CREATE_ENABLED','false').lower()!='true' or message.sender_is_bot:return {'created':False,'reason':'disabled'}
  if not self.feedback.is_enabled(message.chat_id,message.sender_id):return {'created':False,'reason':'user_opt_out'}
  prompt='Return JSON only: {"version":1,"should_schedule":bool,"confidence":0..1,"follow_up_type":"task_outcome|event_check_in|celebration|travel_return|exam_or_deadline|repair_or_purchase|health_or_wellbeing|project_progress|promise_or_commitment","topic":"short","goal":"short","delay_hours":6..2160,"deadline_hours":null|1..2160,"sensitivity":"normal|sensitive","intrusiveness":"low|medium|high"}. Never choose priority, write future message, IDs, SQL, paths, or another chat. Reject vague or intrusive follow-ups. User message: '+(message.text or '')[:900]
  t=time.monotonic();r=await self.router.complete(prompt,max_output_tokens=160);lat=int((time.monotonic()-t)*1000)
  try:d=json.loads(r.text);typ=d['follow_up_type'];conf=float(d['confidence']);delay=int(d['delay_hours']);topic=re.sub(r'\s+',' ',str(d['topic']))[:120];goal=re.sub(r'\s+',' ',str(d['goal']))[:160]
  except Exception:return {'created':False,'reason':'invalid_plan','latency_ms':lat}
  if not d.get('should_schedule') or typ not in TYPES or not .6<=conf<=1 or not 6<=delay<=2160 or not topic or not goal or d.get('intrusiveness')=='high' or (d.get('sensitivity')=='sensitive' and typ in SENSITIVE):return {'created':False,'reason':'rejected','latency_ms':lat}
  delay=self.feedback.adjust_delay(message.chat_id,message.sender_id,delay)
  deadline_hours=d.get('deadline_hours');deadline_hours=int(deadline_hours) if deadline_hours is not None and str(deadline_hours).isdigit() and 1<=int(deadline_hours)<=2160 else None
  priority='high' if typ=='exam_or_deadline' else ('low' if typ in {'task_outcome','repair_or_purchase'} else 'normal')
  if re.search(r'فوری|خیلی مهم|urgent|critical',message.text or '',re.I):priority='high'
  if deadline_hours is not None and deadline_hours<=6:priority='critical'
  now=int(time.time());deadline_at=now+deadline_hours*3600 if deadline_hours is not None else None;key=f'{message.chat_id}:{message.sender_id}:{typ}:{topic.casefold()}'
  def put():
   with self.store._conn() as c:
    if c.execute("select 1 from proactive_followups where dedup_key=? and status in ('pending','postponed','evaluating')",(key,)).fetchone():return False
    c.execute("insert into proactive_followups(id,chat_id,subject_user_id,source_message_id,created_at,due_at,deadline_at,timezone,follow_up_type,topic_summary,goal,priority,sensitivity,confidence,status,dedup_key) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),message.chat_id,message.sender_id,message.message_id,now,now+delay*3600,deadline_at,'UTC',typ,topic,goal,priority,d.get('sensitivity','normal'),conf,'pending',key));return True
  return {'created':await asyncio.to_thread(put),'type':typ,'latency_ms':lat}
 async def claim_due(self,worker,limit=8):
  return await asyncio.to_thread(self.scheduler.claim_due,worker,limit)
 async def tick(self,worker='listener',limit=8):
  started=time.monotonic();await asyncio.to_thread(self.outbox.recover);await asyncio.to_thread(self.feedback.sweep_ignored);await asyncio.to_thread(self.rollout.heartbeat)
  await asyncio.to_thread(self.scheduler.propagate_disabled)
  rows=await self.claim_due(worker,limit);out=[]
  for row in rows:
   try:
    recent=await self.store.get_recent(row['chat_id'],limit=12);ctx=' '.join(str(x.get('text',''))[:120] for x in recent)
    prompt='Return JSON only: {"version":1,"action":"send|postpone|cancel|expire","confidence":0..1,"postpone_hours":0..168,"reason_code":"short"}. Decide only from candidate and fresh context; no IDs, SQL, or new scope. candidate='+json.dumps({'type':row['follow_up_type'],'topic':row['topic_summary'],'goal':row['goal']})+' context='+ctx[:1400]
    try:
     response=await self.router.complete(prompt,max_output_tokens=120)
    except Exception:
     retry_at=await asyncio.to_thread(self.scheduler.technical_failure,row['id'],'provider_transient');out.append({'action':'retry' if retry_at else 'failed','would_send':False,'id':row['id'],'outcome_status':'unknown','reason':'provider_transient'});continue
    try:d=json.loads(response.text);action=d['action'];conf=float(d['confidence']);delay=int(d.get('postpone_hours',24));assert action in {'send','postpone','cancel','expire'} and 0<=conf<=1 and 0<=delay<=168
    except Exception:d={'reason_code':'invalid_plan'};action='postpone';delay=6
    outcome=await self.outcomes.detect(row,recent)
    if outcome.status=='resolved':
     await asyncio.to_thread(self.scheduler.cancel,row['id'],'outcome:'+outcome.reason,None,status='cancelled');out.append({'action':'cancel','would_send':False,'id':row['id'],'outcome_status':'resolved','reason':'outcome_resolved'});continue
    try:
     policy=self.policy.decide(row)
     if not isinstance(policy,PolicyDecision) or policy.action not in {'allow','postpone','block','cancel'} or not policy.reason:raise ValueError('invalid policy')
    except Exception:policy=PolicyDecision('postpone','policy_error',int(time.time())+3600)
    try:self.policy.record_decision(row,policy)
    except Exception:pass
    now=int(time.time())
    if action=='send' and policy.action!='allow':action='postpone' if policy.action=='postpone' else 'block';d={'reason_code':policy.reason};delay=max(1,((policy.retry_at or now+3600)-now+3599)//3600)
    if action=='postpone':
     delay=self.feedback.adjust_delay(row['chat_id'],row['subject_user_id'],max(1,delay or 24));await asyncio.to_thread(self.scheduler.policy_postpone,row['id'],now+delay*3600,d.get('reason_code','postponed'),now);out.append({'action':'postpone','would_send':False,'id':row['id'],'outcome_status':outcome.status,'reason':d.get('reason_code','postponed')});continue
    if action in {'cancel','expire','block'}:
     target='expired' if action=='expire' else ('blocked' if action=='block' else 'cancelled');await asyncio.to_thread(self.scheduler.cancel,row['id'],d.get('reason_code',action),now,status=target);out.append({'action':action,'would_send':False,'id':row['id'],'outcome_status':outcome.status,'reason':d.get('reason_code',action)});continue
    if self.rollout_enforced:
     rollout_decision=await asyncio.to_thread(self.rollout.decide,row['chat_id'],row['subject_user_id'])
     if not rollout_decision.allowed and rollout_decision.reason!='send_disabled':
      await asyncio.to_thread(self.scheduler.policy_postpone,row['id'],now+3600,'rollout_gate',now);out.append({'action':'postpone','would_send':False,'id':row['id'],'outcome_status':outcome.status,'reason':rollout_decision.reason});continue
    key=await asyncio.to_thread(self.outbox.reserve,row['id'],worker,now,installation_id=str(row.get('installation_id','') or 'inst'),group_id=str(row.get('group_id','') or 'group'))
    if not key:
     with self.store._conn() as conn:state=conn.execute("SELECT send_state FROM proactive_followup_outbox WHERE candidate_id=?",(row['id'],)).fetchone()
     if state and state[0]=='ambiguous':await asyncio.to_thread(self.scheduler.cancel,row['id'],'outbox_ambiguous',now,status='blocked')
     out.append({'action':'blocked','would_send':False,'id':row['id'],'outcome_status':outcome.status,'reason':'outbox_unavailable'});continue
    try:generated=await self.router.complete('Write one short casual follow-up; no claim beyond goal. goal='+row['goal'],max_output_tokens=80)
    except Exception:
     await asyncio.to_thread(self.outbox.complete,key,TransportResult(False,retryable=True,error_code='generation_transient'))
     retry_at=await asyncio.to_thread(self.scheduler.technical_failure,row['id'],'generation_transient',now);out.append({'action':'retry' if retry_at else 'failed','would_send':False,'id':row['id'],'outcome_status':outcome.status,'reason':'generation_transient'});continue
    result=await self.transport.send(row['chat_id'],generated.text[:500],key);await asyncio.to_thread(self.outbox.complete,key,result)
    if result.success:
     await asyncio.to_thread(self.rollout.record_send,not str(result.receipt or '').startswith('mock:'))
     self.policy.record(row,'sent',now)
     if str(result.receipt or '').startswith('mock:'):await asyncio.to_thread(self.scheduler.policy_postpone,row['id'],now+24*3600,'mock_would_send',now)
     else:
      with self.store._conn() as conn:conn.execute("UPDATE proactive_followups SET status='sent',sent_at=?,lease_until=NULL,worker_id=NULL,next_retry_at=NULL WHERE id=? AND worker_id=?",(now,row['id'],worker))
     out.append({'action':'send','would_send':True,'id':row['id'],'outcome_status':outcome.status,'reason':'sent'})
    elif result.retryable:
     retry_at=await asyncio.to_thread(self.scheduler.technical_failure,row['id'],result.error_code or 'transport_transient',now);out.append({'action':'retry' if retry_at else 'failed','would_send':True,'id':row['id'],'outcome_status':outcome.status,'reason':'transport_retry'})
    else:
     await asyncio.to_thread(self.scheduler.cancel,row['id'],result.error_code or 'transport_permanent',now,status='permanent_failed');out.append({'action':'failed','would_send':True,'id':row['id'],'outcome_status':outcome.status,'reason':'transport_permanent'})
   except Exception:
    self.scheduler._metric('scheduler_tick_failure')
    try:await asyncio.to_thread(self.scheduler.technical_failure,row['id'],'scheduler_item_failure')
    except Exception:pass
    out.append({'action':'retry','would_send':False,'id':row['id'],'outcome_status':'unknown','reason':'scheduler_item_failure'})
  self.scheduler.last_metrics['scheduler_tick_duration_ms']=int((time.monotonic()-started)*1000);return out
