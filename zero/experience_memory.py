from __future__ import annotations

from .sqlite_tx import sqlite_txn
import hashlib
import json
import logging
import re
import sqlite3
import time
import uuid
from pathlib import Path

logger = logging.getLogger('zero.experience_memory')
VERIFY_THRESHOLD = 0.6
AUDIT_EVENTS = {
    'EXPERIENCE_VERIFICATION_REQUESTED', 'EXPERIENCE_VERIFICATION_REJECTED',
    'EXPERIENCE_VERIFIED', 'EXPERIENCE_INVALIDATED',
}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS experience_memory_candidates (
 id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, root_cause TEXT NOT NULL,
 fix TEXT NOT NULL, evidence_json TEXT NOT NULL, source_text_hash TEXT NOT NULL,
 confidence REAL NOT NULL, created_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', reviewed_at INTEGER
);
CREATE TABLE IF NOT EXISTS experience_memory (
 id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, root_cause TEXT NOT NULL,
 fix TEXT NOT NULL, evidence_json TEXT NOT NULL, source_text_hash TEXT NOT NULL,
 confidence REAL NOT NULL, first_seen_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'active', version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(topic,root_cause,version)
);
CREATE INDEX IF NOT EXISTS idx_experience_topic ON experience_memory(topic,status);
CREATE TABLE IF NOT EXISTS experience_memory_audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, trace_id TEXT NOT NULL,
 experience_id INTEGER NOT NULL, previous_status TEXT NOT NULL, new_status TEXT NOT NULL,
 actor_id INTEGER, reason TEXT NOT NULL DEFAULT '', timestamp INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experience_audit_id ON experience_memory_audit(experience_id,timestamp);
'''


def migrate_experience_memory(db_path: str | Path):
    # sqlite_txn closes the handle; a bare `with sqlite3.connect(...)` only
    # commits or rolls back, leaving the schema-init connection open until GC.
    with sqlite_txn(sqlite3.connect(db_path, timeout=5)) as c:
        c.execute('PRAGMA busy_timeout=5000'); c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA foreign_keys=ON')
        c.executescript(SCHEMA)
        candidate_cols = {r[1] for r in c.execute('PRAGMA table_info(experience_memory_candidates)')}
        if 'regression_test_json' not in candidate_cols:
            c.execute("ALTER TABLE experience_memory_candidates ADD COLUMN regression_test_json TEXT NOT NULL DEFAULT '[]'")
        cols = {r[1] for r in c.execute('PRAGMA table_info(experience_memory)')}
        for name, definition in (
            ('outcome', "TEXT NOT NULL DEFAULT ''"),
            ('regression_test_json', "TEXT NOT NULL DEFAULT '[]'"),
            ('verified_by', 'INTEGER'),
            ('verified_at', 'INTEGER'),
            ('invalidated_reason', "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in cols:
                c.execute(f'ALTER TABLE experience_memory ADD COLUMN {name} {definition}')
        c.commit()


def _nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_evidence_item(item) -> bool:
    if isinstance(item, dict):
        if item.get('test_name') and str(item.get('status', '')).lower() == 'passed': return True
        if item.get('trace_id') and item.get('log_event'): return True
        return any(item.get(k) for k in ('run_id', 'message_id', 'regression_test')) or bool(item.get('file_path') and (item.get('line') or item.get('reference'))) or bool(item.get('backup_reference') or item.get('checksum_reference'))
    if isinstance(item, str):
        # Backward-compatible structured shorthand; arbitrary prose is rejected.
        return bool(re.match(r'^(trace|test|pytest|run|message|file|backup|checksum|regression_test):[^\s].*$', item.strip(), re.I))
    return False


def validate_evidence(evidence) -> bool:
    return isinstance(evidence, (list, tuple)) and any(_valid_evidence_item(x) for x in evidence)


class ExperienceMemory:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        migrate_experience_memory(self.db_path)

    def _c(self):
        c = sqlite3.connect(self.db_path, timeout=5); c.row_factory = sqlite3.Row
        c.execute('PRAGMA busy_timeout=5000'); c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA foreign_keys=ON')
        return c

    def _audit(self, event_type, experience_id, previous_status, new_status, actor_id, reason='', trace_id=None):
        if event_type not in AUDIT_EVENTS: raise ValueError('invalid_audit_event')
        with sqlite_txn(self._c()) as c:
            c.execute('INSERT INTO experience_memory_audit(event_type,trace_id,experience_id,previous_status,new_status,actor_id,reason,timestamp) VALUES(?,?,?,?,?,?,?,?)', (event_type, trace_id or uuid.uuid4().hex, experience_id, previous_status or '', new_status or '', actor_id, str(reason)[:500], int(time.time())))
            c.commit()

    def candidate(self, topic: str, root_cause: str, fix: str, evidence, source_text: str, confidence: float = .8, *, outcome: str = '', regression_tests=()) -> int:
        if not evidence or not _nonempty(root_cause) or not _nonempty(fix) or not 0 <= confidence <= 1: raise ValueError('evidence_required')
        if not validate_evidence(evidence): raise ValueError('invalid_evidence')
        with sqlite_txn(self._c()) as c:
            cur = c.execute('INSERT INTO experience_memory_candidates(topic,root_cause,fix,evidence_json,source_text_hash,confidence,created_at,regression_test_json) VALUES(?,?,?,?,?,?,?,?)', (topic, root_cause, fix, json.dumps(list(evidence), ensure_ascii=False), hashlib.sha256(source_text.encode()).hexdigest(), confidence, int(time.time()), json.dumps(list(regression_tests), ensure_ascii=False)))
            c.commit(); return cur.lastrowid

    def approve(self, candidate_id: int, reviewer_id: int) -> int:
        now = int(time.time())
        with sqlite_txn(self._c()) as c:
            row = c.execute("SELECT * FROM experience_memory_candidates WHERE id=? AND status='pending'", (candidate_id,)).fetchone()
            if not row or row['confidence'] < VERIFY_THRESHOLD: raise ValueError('candidate_not_approvable')
            prior = c.execute('SELECT COALESCE(MAX(version),0) v FROM experience_memory WHERE topic=? AND root_cause=?', (row['topic'], row['root_cause'])).fetchone()['v']
            c.execute("UPDATE experience_memory SET status='superseded',last_seen_at=? WHERE topic=? AND root_cause=? AND status IN ('active','verified')", (now, row['topic'], row['root_cause']))
            regression_json = row['regression_test_json'] if 'regression_test_json' in row.keys() else '[]'
            cur = c.execute('INSERT INTO experience_memory(topic,root_cause,fix,evidence_json,source_text_hash,confidence,first_seen_at,last_seen_at,outcome,regression_test_json,version) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (row['topic'],row['root_cause'],row['fix'],row['evidence_json'],row['source_text_hash'],row['confidence'],now,now,row['fix'],regression_json,prior+1))
            c.execute("UPDATE experience_memory_candidates SET status='approved',reviewed_at=? WHERE id=?", (now, candidate_id)); c.commit(); return cur.lastrowid

    def verify(self, experience_id: int, actor_id: int, *, trace_id: str | None = None) -> dict:
        trace_id = trace_id or uuid.uuid4().hex
        with sqlite_txn(self._c()) as c: row = c.execute('SELECT * FROM experience_memory WHERE id=?', (experience_id,)).fetchone()
        previous = row['status'] if row else ''
        self._audit('EXPERIENCE_VERIFICATION_REQUESTED', experience_id, previous, previous, actor_id, '', trace_id)
        if not row: reason = 'invalid_status'
        elif row['status'] not in {'active', 'verified'}: reason = 'invalid_status'
        elif not _nonempty(row['root_cause']): reason = 'missing_root_cause'
        elif not _nonempty(row['fix']): reason = 'missing_final_fix'
        elif not _nonempty(row['outcome']): reason = 'missing_outcome'
        elif not validate_evidence(json.loads(row['evidence_json'] or '[]')): reason = 'missing_evidence'
        elif not validate_evidence(json.loads(row['regression_test_json'] or '[]')): reason = 'missing_regression_test'
        elif row['confidence'] <= VERIFY_THRESHOLD: reason = 'confidence_too_low'
        else: reason = ''
        if reason:
            self._audit('EXPERIENCE_VERIFICATION_REJECTED', experience_id, previous, previous, actor_id, reason, trace_id)
            return {'verified': False, 'reason': reason, 'trace_id': trace_id}
        now = int(time.time())
        with sqlite_txn(self._c()) as c:
            c.execute("UPDATE experience_memory SET status='verified',verified_by=?,verified_at=? WHERE id=?", (actor_id, now, experience_id)); c.commit()
        self._audit('EXPERIENCE_VERIFIED', experience_id, previous, 'verified', actor_id, 'evidence_validated', trace_id)
        return {'verified': True, 'id': experience_id, 'status': 'verified', 'verified_by': actor_id, 'verified_at': now, 'trace_id': trace_id}

    def invalidate(self, experience_id: int, actor_id: int, reason: str = '', *, trace_id: str | None = None) -> dict:
        trace_id = trace_id or uuid.uuid4().hex
        with sqlite_txn(self._c()) as c:
            row = c.execute('SELECT status FROM experience_memory WHERE id=?', (experience_id,)).fetchone()
            if not row: return {'invalidated': False, 'reason': 'not_found', 'trace_id': trace_id}
            c.execute("UPDATE experience_memory SET status='invalidated',invalidated_reason=?,last_seen_at=? WHERE id=?", (reason[:500], int(time.time()), experience_id)); c.commit()
        self._audit('EXPERIENCE_INVALIDATED', experience_id, row['status'], 'invalidated', actor_id, reason, trace_id)
        return {'invalidated': True, 'id': experience_id, 'status': 'invalidated', 'trace_id': trace_id}

    def retrieve(self, query: str, debug: bool = False, limit: int = 3) -> list[dict]:
        if not debug: return []
        q=set(re.findall(r'[a-z0-9_]{3,}|[آ-ی]{3,}', (query or '').casefold()))
        with sqlite_txn(self._c()) as c: rows=c.execute("SELECT * FROM experience_memory WHERE status IN ('active','verified') ORDER BY last_seen_at DESC,confidence DESC").fetchall()
        ranked=[]
        for r in rows:
            hay=' '.join((r['topic'],r['root_cause'],r['fix'],r['outcome'],r['evidence_json'])).casefold(); terms=set(re.findall(r'[a-z0-9_]{3,}|[آ-ی]{3,}',hay)); overlap=len(q & terms)
            if overlap: ranked.append((overlap*3 + float(r['confidence']), r))
        ranked.sort(key=lambda x:x[0], reverse=True)
        return [dict(r)|{'evidence':json.loads(r['evidence_json']), 'regression_tests':json.loads(r['regression_test_json'] or '[]')} for _,r in ranked[:limit]]
