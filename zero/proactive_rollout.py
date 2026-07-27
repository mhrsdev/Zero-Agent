from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass

CANARY_LEVELS={0,1,5,10,25,50,100}
COUNTERS=('allowlist_rejects','canary_accepts','real_sends','mock_sends')

@dataclass(frozen=True)
class RolloutDecision:
    allowed: bool
    reason: str
    canary_bucket: int | None = None

class RolloutController:
    """Fail-closed production-send gate; no candidate lifecycle authority."""
    MIGRATION_VERSION='5'
    def __init__(self,store):
        self.store=store;self._load();self._schema()
    def _load(self):
        self.send_enabled=os.getenv('ZERO_PROACTIVE_FOLLOWUP_SEND_ENABLED','false').lower()=='true'
        self.admin_disabled=os.getenv('ZERO_PROACTIVE_ADMIN_DISABLED','false').lower()=='true'
        self.wildcard_enabled=os.getenv('ZERO_PROACTIVE_ALLOW_WILDCARD','false').lower()=='true'
        self.config_sane=True
        def ids(name):
            raw=os.getenv(name,'').strip()
            if not raw:return set(),False
            values=set();wild=False
            for part in raw.split(','):
                item=part.strip()
                if item=='*':wild=True;continue
                try:values.add(int(item))
                except ValueError:self.config_sane=False
            if wild and not self.wildcard_enabled:self.config_sane=False
            return frozenset(values),bool(wild and self.wildcard_enabled)
        self.chat_ids,self.chat_wildcard=ids('ZERO_PROACTIVE_ALLOWED_CHAT_IDS')
        self.user_ids,self.user_wildcard=ids('ZERO_PROACTIVE_ALLOWED_USER_IDS')
        try:self.canary_percent=int(os.getenv('ZERO_PROACTIVE_CANARY_PERCENT','0'))
        except ValueError:self.canary_percent=0;self.config_sane=False
        if self.canary_percent not in CANARY_LEVELS:self.canary_percent=0;self.config_sane=False
    def _schema(self):
        now=int(time.time())
        with self.store._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS proactive_rollout_state(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at INTEGER NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS proactive_rollout_counters(name TEXT PRIMARY KEY,value INTEGER NOT NULL DEFAULT 0,updated_at INTEGER NOT NULL)")
            c.execute("INSERT INTO proactive_rollout_state VALUES('migration_version',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(self.MIGRATION_VERSION,now))
            for name in COUNTERS:c.execute("INSERT OR IGNORE INTO proactive_rollout_counters VALUES(?,0,?)",(name,now))
    def _count(self,name,amount=1):
        if name not in COUNTERS:return
        with self.store._conn() as c:c.execute("UPDATE proactive_rollout_counters SET value=value+?,updated_at=? WHERE name=?",(int(amount),int(time.time()),name))
    @staticmethod
    def bucket(user_id):return int(hashlib.sha256(('zero-proactive-canary-v1:'+str(int(user_id))).encode()).hexdigest()[:8],16)%10000
    def decide(self,chat_id,user_id):
        if self.admin_disabled:return RolloutDecision(False,'admin_disabled')
        if not self.send_enabled:return RolloutDecision(False,'send_disabled')
        if not self.config_sane:self._count('allowlist_rejects');return RolloutDecision(False,'invalid_configuration')
        if not (self.chat_ids or self.chat_wildcard or self.user_ids or self.user_wildcard):self._count('allowlist_rejects');return RolloutDecision(False,'allowlist_empty')
        if (self.chat_ids or self.chat_wildcard) and not (self.chat_wildcard or int(chat_id) in self.chat_ids):self._count('allowlist_rejects');return RolloutDecision(False,'chat_not_allowed')
        if (self.user_ids or self.user_wildcard) and not (self.user_wildcard or int(user_id) in self.user_ids):self._count('allowlist_rejects');return RolloutDecision(False,'user_not_allowed')
        bucket=self.bucket(user_id)
        if bucket>=self.canary_percent*100:return RolloutDecision(False,'canary_reject',bucket)
        self._count('canary_accepts');return RolloutDecision(True,'allowed',bucket)
    def heartbeat(self):
        with self.store._conn() as c:c.execute("INSERT INTO proactive_rollout_state VALUES('scheduler_heartbeat','alive',?) ON CONFLICT(key) DO UPDATE SET value='alive',updated_at=excluded.updated_at",(int(time.time()),))
    def record_send(self,real):self._count('real_sends' if real else 'mock_sends')
    def metrics(self):
        with self.store._conn() as c:return {r['name']:int(r['value']) for r in c.execute('select name,value from proactive_rollout_counters')}

class ProactiveProductionHealth:
    def __init__(self,store,rollout,transport):self.store,self.rollout,self.transport=store,rollout,transport
    def check(self,now=None):
        now=int(now if now is not None else time.time());checks={}
        with self.store._conn() as c:
            heartbeat=c.execute("select updated_at from proactive_rollout_state where key='scheduler_heartbeat'").fetchone();checks['scheduler_alive']=bool(heartbeat and now-int(heartbeat[0])<=3660)
            checks['lease_cleanup']=c.execute("select count(*) from proactive_followups where status='evaluating' and lease_until<?",(now,)).fetchone()[0]==0
            checks['outbox_consistency']=c.execute("select count(*) from proactive_followup_outbox where send_state not in ('reserved','sending','sent','retryable_failed','permanent_failed','ambiguous') or (send_state='sent' and receipt is null)").fetchone()[0]==0
            checks['retry_queue_consistency']=c.execute("select count(*) from proactive_followups where status='retryable_failed' and next_retry_at is null").fetchone()[0]==0
            checks['policy_storage']=bool(c.execute("select 1 from sqlite_master where type='table' and name='proactive_policy_events'").fetchone())
            checks['sqlite_integrity']=c.execute('pragma integrity_check').fetchone()[0]=='ok' and not c.execute('pragma foreign_key_check').fetchall()
            version=c.execute("select value from proactive_rollout_state where key='migration_version'").fetchone();checks['migration_version']=bool(version and version[0]==RolloutController.MIGRATION_VERSION)
            checks['configuration_sanity']=self.rollout.config_sane
            real=self.transport.__class__.__name__=='TelegramProactiveTransport';checks['transport_selection']=real if self.rollout.send_enabled else not real
            outbox_depth=c.execute("select count(*) from proactive_followup_outbox where send_state in ('reserved','sending','retryable_failed')").fetchone()[0]
            retry_queue=c.execute("select count(*) from proactive_followups where status='retryable_failed'").fetchone()[0]
            blocks=c.execute("select count(*) from proactive_policy_events where action='block'").fetchone()[0]
            postpones=c.execute("select count(*) from proactive_policy_events where action='postpone'").fetchone()[0]
            ambiguous=c.execute("select count(*) from proactive_followup_outbox where send_state='ambiguous'").fetchone()[0]
            exhausted=c.execute("select count(*) from proactive_followups where terminal_reason='retry_budget_exhausted'").fetchone()[0]
            expired=c.execute("select count(*) from proactive_followups where terminal_reason='deadline_expired'").fetchone()[0]
        counters=self.rollout.metrics();metrics={'scheduler_health':int(checks['scheduler_alive']),'transport_health':int(checks['transport_selection']),'outbox_depth':outbox_depth,'retry_queue':retry_queue,'policy_blocks':blocks,'policy_postpones':postpones,'ambiguous_reservations':ambiguous,'retry_exhausted':exhausted,'deadline_expired':expired,**counters}
        return {'status':'healthy' if all(checks.values()) else 'degraded','checks':checks,'metrics':metrics,'migration_version':RolloutController.MIGRATION_VERSION}
