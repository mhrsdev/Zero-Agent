from __future__ import annotations

import hashlib
import sqlite3
import time
from collections import defaultdict

PRIORITY = {"low": 100, "normal": 200, "high": 300, "critical": 400}
TERMINAL = {"sent", "resolved", "cancelled", "expired", "permanent_failed", "blocked"}
ACTIVE = {"pending", "postponed", "evaluating", "retryable_failed"}


class SchedulerIntelligence:
    """Persistent priority/fair scheduler and retry state owner."""

    def __init__(self, store, outbox):
        self.store, self.outbox = store, outbox
        self.last_metrics = {}
        self._migrate()

    def _migrate(self):
        columns = {
            "deadline_at": "INTEGER", "timezone": "TEXT", "postpone_count": "INTEGER NOT NULL DEFAULT 0",
            "retry_count": "INTEGER NOT NULL DEFAULT 0", "max_retries": "INTEGER NOT NULL DEFAULT 3",
            "last_failure_reason": "TEXT", "next_retry_at": "INTEGER", "last_attempt_at": "INTEGER",
            "parent_candidate_id": "TEXT", "terminal_reason": "TEXT",
        }
        with self.store._conn() as conn:
            existing = {row[1] for row in conn.execute("PRAGMA table_info(proactive_followups)")}
            for name, kind in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE proactive_followups ADD COLUMN {name} {kind}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_proactive_due ON proactive_followups(status,due_at,next_retry_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_proactive_scope ON proactive_followups(chat_id,subject_user_id,status)")
            conn.execute("UPDATE proactive_followups SET timezone='UTC' WHERE deadline_at IS NOT NULL AND timezone IS NULL")

    @staticmethod
    def normalize_priority(value) -> str:
        value = str(value or "normal").lower()
        return value if value in PRIORITY else "normal"

    def score(self, row, now: int) -> int:
        base = PRIORITY[self.normalize_priority(row.get("priority"))]
        age_days = max(0, (now - int(row.get("created_at") or now)) // 86400)
        age_boost = min(180, age_days * 6)
        postpone_boost = min(60, max(0, int(row.get("postpone_count") or 0)) * 6)
        deadline = row.get("deadline_at"); deadline_boost = 0
        if deadline is not None:
            remaining = int(deadline) - now
            if 0 < remaining <= 24 * 3600:
                deadline_boost = max(20, 90 - remaining * 70 // (24 * 3600))
        return base + age_boost + postpone_boost + deadline_boost

    def _fair_select(self, rows, limit, now):
        queues = defaultdict(list)
        for row in rows:
            queues[(row["chat_id"], row["subject_user_id"])].append(row)
        for queue in queues.values():
            queue.sort(key=lambda r: (-self.score(r, now), int(r.get("due_at") or 0), int(r.get("created_at") or 0), r["id"]))
        selected = []
        while queues and len(selected) < limit:
            scopes = sorted(queues, key=lambda scope: (-self.score(queues[scope][0], now), int(queues[scope][0].get('due_at') or 0), int(queues[scope][0].get('created_at') or 0), queues[scope][0]['id'], scope[0], scope[1]))
            for scope in scopes:
                if len(selected) >= limit: break
                selected.append(queues[scope].pop(0))
                if not queues[scope]: del queues[scope]
        return selected

    def claim_due(self, worker: str, limit: int = 8, now: int | None = None):
        now = int(now if now is not None else time.time()); metrics = defaultdict(int)
        for name in ('scheduler_batch_size','candidate_selected','candidate_claimed','candidate_skipped_fairness','candidate_age_boosted','deadline_expired','deadline_near','retry_scheduled','retry_budget_exhausted','policy_postponed','lease_recovered','candidate_terminal_failed','scheduler_tick_failure'):metrics[name]=0
        with self.store._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            expired = conn.execute(
                """SELECT id FROM proactive_followups WHERE status IN ('pending','postponed','evaluating','retryable_failed')
                AND deadline_at IS NOT NULL AND deadline_at<=?""", (now,)
            ).fetchall()
            for item in expired:
                conn.execute("UPDATE proactive_followups SET status='expired',terminal_reason='deadline_expired',lease_until=NULL,worker_id=NULL,next_retry_at=NULL WHERE id=?", (item[0],))
                self._stop_outbox(conn, item[0], "deadline_expired"); metrics["deadline_expired"] += 1
            recovered = conn.execute("SELECT count(*) FROM proactive_followups WHERE status='evaluating' AND lease_until<?", (now,)).fetchone()[0]
            rows = [dict(r) for r in conn.execute(
                """SELECT * FROM proactive_followups WHERE status IN ('pending','postponed','evaluating','retryable_failed')
                AND due_at<=? AND (next_retry_at IS NULL OR next_retry_at<=?)
                AND (lease_until IS NULL OR lease_until<?) ORDER BY due_at,created_at,id LIMIT 256""", (now, now, now)
            ).fetchall()]
            chosen = self._fair_select(rows, max(1, min(int(limit), 64)), now)
            claimed = []
            for row in chosen:
                cur = conn.execute(
                    """UPDATE proactive_followups SET status='evaluating',claim_at=?,lease_until=?,worker_id=?,
                    last_attempt_at=?,version=version+1 WHERE id=? AND status IN ('pending','postponed','evaluating','retryable_failed')
                    AND (lease_until IS NULL OR lease_until<?)""", (now, now + 900, worker, now, row["id"], now)
                )
                if cur.rowcount:
                    row.update(status="evaluating", claim_at=now, lease_until=now+900, worker_id=worker, last_attempt_at=now)
                    claimed.append(row)
            conn.commit()
        metrics["lease_recovered"] = recovered; metrics["candidate_selected"] = len(chosen);metrics["candidate_claimed"] = len(claimed)
        metrics["scheduler_batch_size"] = len(claimed);metrics["candidate_skipped_fairness"] = max(0, len(rows)-len(chosen))
        metrics["candidate_age_boosted"] = sum(self.score(r,now)>PRIORITY[self.normalize_priority(r.get('priority'))] for r in claimed)
        metrics["deadline_near"] = sum(bool(r.get('deadline_at')) and 0<int(r['deadline_at'])-now<=86400 for r in claimed)
        self.last_metrics = dict(metrics);return claimed

    def _metric(self, name, amount=1):
        self.last_metrics[name] = int(self.last_metrics.get(name, 0)) + int(amount)

    def policy_postpone(self, candidate_id, requested_at, reason, now=None):
        now=int(now if now is not None else time.time())
        with self.store._conn() as conn:
            row=conn.execute("SELECT deadline_at FROM proactive_followups WHERE id=?",(candidate_id,)).fetchone()
            if not row:return False
            deadline=row[0];due=int(requested_at)
            if deadline is not None:
                if int(deadline)<=now:
                    conn.execute("UPDATE proactive_followups SET status='expired',terminal_reason='deadline_expired',cancel_reason='deadline_expired',resolved_at=?,lease_until=NULL,worker_id=NULL,next_retry_at=NULL WHERE id=? AND status!='sent'",(now,candidate_id));self._stop_outbox(conn,candidate_id,'deadline_expired');return False
                due=min(due,int(deadline)-1)
            cur=conn.execute("""UPDATE proactive_followups SET status='postponed',due_at=?,postpone_count=postpone_count+1,
            terminal_reason=NULL,cancel_reason=?,lease_until=NULL,worker_id=NULL WHERE id=? AND status NOT IN ('sent','resolved','cancelled','expired','permanent_failed')""",(max(now+1,due),str(reason)[:80],candidate_id))
        if cur.rowcount:self._metric('policy_postponed')
        return bool(cur.rowcount)

    def technical_failure(self,candidate_id,reason,now=None):
        allowed={'provider_transient','generation_transient','transport_transient','database_transient','scheduler_item_failure'}
        now=int(now if now is not None else time.time());reason=str(reason) if str(reason) in allowed else 'transport_transient'
        with self.store._conn() as conn:
            conn.execute("BEGIN IMMEDIATE");row=conn.execute("SELECT retry_count,max_retries,status FROM proactive_followups WHERE id=?",(candidate_id,)).fetchone()
            if not row or row[2] in TERMINAL:conn.rollback();return None
            count=int(row[0] or 0)+1;budget=max(1,int(row[1] or 3))
            if count>=budget:
                conn.execute("UPDATE proactive_followups SET status='permanent_failed',retry_count=?,last_failure_reason=?,last_attempt_at=?,next_retry_at=NULL,lease_until=NULL,worker_id=NULL,terminal_reason='retry_budget_exhausted' WHERE id=?",(count,reason,now,candidate_id));self._stop_outbox(conn,candidate_id,"retry_budget_exhausted");conn.commit();self._metric('retry_budget_exhausted');self._metric('candidate_terminal_failed');return None
            bases=(900,3600,21600);base=bases[min(count-1,len(bases)-1)];jitter=int(hashlib.sha256(f"{candidate_id}:{count}".encode()).hexdigest()[:4],16)%(base//10+1);retry_at=now+min(21600,base+jitter)
            conn.execute("UPDATE proactive_followups SET status='retryable_failed',retry_count=?,last_failure_reason=?,last_attempt_at=?,next_retry_at=?,due_at=?,lease_until=NULL,worker_id=NULL WHERE id=?",(count,reason,now,retry_at,retry_at,candidate_id));conn.commit();self._metric('retry_scheduled');return retry_at

    def cancel(self,candidate_id,reason,now=None,*,status="cancelled"):
        now=int(now if now is not None else time.time());reason=str(reason)[:80]
        with self.store._conn() as conn:
            ids=[r[0] for r in conn.execute("WITH RECURSIVE tree(id) AS (SELECT ? UNION ALL SELECT p.id FROM proactive_followups p JOIN tree t ON p.parent_candidate_id=t.id) SELECT id FROM tree",(candidate_id,)).fetchall()]
            for item in ids:
                conn.execute("UPDATE proactive_followups SET status=?,terminal_reason=?,cancel_reason=?,resolved_at=?,lease_until=NULL,worker_id=NULL,next_retry_at=NULL WHERE id=? AND status!='sent'",(status,reason,reason,now,item));self._stop_outbox(conn,item,reason)
        return len(ids)

    def cancel_all_active(self, reason='administrative_disable', now=None):
        now=int(now if now is not None else time.time())
        with self.store._conn() as conn:ids=[r[0] for r in conn.execute("SELECT id FROM proactive_followups WHERE status IN ('pending','postponed','evaluating','retryable_failed')").fetchall()]
        for item in ids:self.cancel(item,reason,now)
        return len(ids)

    def propagate_disabled(self,now=None):
        now=int(now if now is not None else time.time())
        with self.store._conn() as conn:
            ids=[r[0] for r in conn.execute("""SELECT p.id FROM proactive_followups p JOIN proactive_feedback_preferences f
            ON f.chat_id=p.chat_id AND f.subject_user_id=p.subject_user_id WHERE f.proactive_enabled=0
            AND p.status IN ('pending','postponed','evaluating','retryable_failed')""").fetchall()]
        for item in ids:self.cancel(item,"user_opt_out",now)
        return len(ids)

    @staticmethod
    def _stop_outbox(conn,candidate_id,reason):
        conn.execute("""UPDATE proactive_followup_outbox SET send_state='permanent_failed',last_error=?,lease_until=NULL,worker_id=NULL,updated_at=?
        WHERE candidate_id=? AND send_state NOT IN ('sent','ambiguous')""",(str(reason)[:80],int(time.time()),candidate_id))
