from __future__ import annotations
import asyncio,os,time
from dataclasses import dataclass
@dataclass(frozen=True)
class TransportResult: success:bool; receipt:str|None=None; retryable:bool=False; error_code:str|None=None
class MockProactiveTransport:
 async def send(self,chat_id:int,text:str,outbound_key:str)->TransportResult:return TransportResult(True,'mock:'+outbound_key)
class TelegramProactiveTransport:
 def __init__(self,client):self.client=client
 async def send(self,chat_id:int,text:str,outbound_key:str)->TransportResult:
  try:
   r=await self.client.send_message(chat_id,text);return TransportResult(True,str(getattr(r,'id','')))
  except Exception as e:return TransportResult(False,retryable=type(e).__name__ in {'TimeoutError','FloodWaitError'},error_code=type(e).__name__)
def select_transport(client=None):
 return TelegramProactiveTransport(client) if os.getenv('ZERO_PROACTIVE_FOLLOWUP_SEND_ENABLED','false').lower()=='true' and client else MockProactiveTransport()
class Outbox:
 def __init__(self,store):
  self.store=store
  with store._conn() as c:c.execute("CREATE TABLE IF NOT EXISTS proactive_followup_outbox(outbound_key TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,installation_id TEXT NOT NULL DEFAULT '',group_id TEXT NOT NULL DEFAULT '',send_state TEXT NOT NULL,attempt_count INTEGER NOT NULL DEFAULT 0,lease_until INTEGER,worker_id TEXT,receipt TEXT,last_error TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)")
  # P0-2: migrate existing databases
  cols={r[1] for r in c.execute('pragma table_info(proactive_followup_outbox)')}
  for col in ('installation_id','group_id'):
   if col not in cols:c.execute(f"ALTER TABLE proactive_followup_outbox ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
 def reserve(self,candidate_id,worker,now=None,*,installation_id='',group_id=''):
  from zero.tenancy.models import FORBIDDEN_IDS,_CANDIDATE_PREFIX
  for _f,_v in (('installation_id',installation_id),('group_id',group_id)):
   if not str(_v).strip():raise ValueError(f"{_f} must not be empty")
   if str(_v).casefold() in FORBIDDEN_IDS:raise ValueError(f"{_f} must not use a forbidden fallback: {_v!r}")
   if str(_v).startswith(_CANDIDATE_PREFIX):raise ValueError(f"{_f} must not be a candidate placeholder: {_v!r}")
  now=now or int(time.time());key='pf:'+candidate_id
  with self.store._conn() as c:
   c.execute('begin immediate');r=c.execute('select send_state,lease_until from proactive_followup_outbox where outbound_key=?',(key,)).fetchone()
   if r and (r['send_state'] in ('sent','ambiguous') or (r['lease_until'] or 0)>now):c.rollback();return None
   c.execute("insert into proactive_followup_outbox(outbound_key,candidate_id,installation_id,group_id,send_state,attempt_count,lease_until,worker_id,created_at,updated_at) values(?,?,?,?,'reserved',1,?,?,?,?) on conflict(outbound_key) do update set send_state='reserved',attempt_count=attempt_count+1,lease_until=excluded.lease_until,worker_id=excluded.worker_id,updated_at=excluded.updated_at",(key,candidate_id,installation_id,group_id,now+900,worker,now,now));c.commit();return key
 def complete(self,key,result):
  now=int(time.time());state='sent' if result.success else ('retryable_failed' if result.retryable else 'permanent_failed')
  with self.store._conn() as c:c.execute('update proactive_followup_outbox set send_state=?,receipt=?,last_error=?,lease_until=null,updated_at=? where outbound_key=?',(state,result.receipt,result.error_code,now,key))
 def recover(self,now=None):
  now=now or int(time.time())
  with self.store._conn() as c:c.execute("update proactive_followup_outbox set send_state='ambiguous',lease_until=null,updated_at=? where send_state in ('reserved','sending') and lease_until<?",(now,now))
