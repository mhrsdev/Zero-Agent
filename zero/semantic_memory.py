from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
import uuid
import weakref
from pathlib import Path
from typing import Any, Callable

from .db_executor import SqliteWorker

logger = logging.getLogger('zero.semantic_memory')

CATEGORIES = {'identity', 'preference', 'interest', 'project', 'skill', 'goal', 'relationship', 'communication_style'}
SENSITIVE = re.compile(r'\b(?:password|token|api[_ -]?key|secret|private key)\b|رمز عبور|توکن|کلید خصوصی', re.I)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS semantic_user_memory_candidates (
 id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, sender_id INTEGER NOT NULL,
 category TEXT NOT NULL, key TEXT NOT NULL, value_json TEXT NOT NULL, confidence REAL NOT NULL,
 evidence_message_ids_json TEXT NOT NULL DEFAULT '[]', source_text_hash TEXT NOT NULL,
 created_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', reviewed_at INTEGER,
 reviewed_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_semantic_candidates_identity ON semantic_user_memory_candidates(chat_id,sender_id,status,created_at);
CREATE TABLE IF NOT EXISTS semantic_user_memory (
 id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, sender_id INTEGER NOT NULL,
 category TEXT NOT NULL, key TEXT NOT NULL, value_json TEXT NOT NULL, confidence REAL NOT NULL,
 evidence_message_ids_json TEXT NOT NULL DEFAULT '[]', source_text_hash TEXT NOT NULL,
 first_seen_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL, last_verified_at INTEGER,
 expires_at INTEGER, status TEXT NOT NULL DEFAULT 'active', version INTEGER NOT NULL DEFAULT 1,
 created_by_backend TEXT NOT NULL DEFAULT 'semantic_memory', UNIQUE(chat_id,sender_id,category,key,version)
);
CREATE INDEX IF NOT EXISTS idx_semantic_active_identity ON semantic_user_memory(chat_id,sender_id,status,expires_at);
CREATE TABLE IF NOT EXISTS semantic_memory_audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, trace_id TEXT NOT NULL,
 item_id INTEGER, chat_id INTEGER NOT NULL, sender_id INTEGER NOT NULL, actor_id INTEGER NOT NULL,
 action TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', timestamp INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_semantic_audit_item ON semantic_memory_audit(item_id,timestamp);
'''


def migrate_semantic_user_memory(db_path: str | Path) -> None:
    con = sqlite3.connect(str(db_path), timeout=5)
    try:
        con.execute('PRAGMA busy_timeout=5000'); con.execute('PRAGMA journal_mode=WAL'); con.execute('PRAGMA foreign_keys=ON')
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


class SemanticUserMemory:
    def __init__(self, db_path: str | Path, backend: str = 'semantic_memory'):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.backend = backend
        migrate_semantic_user_memory(self.db_path)
        # Reopening a connection and re-issuing its three PRAGMAs costs ~1.3 ms
        # against ~6 us of query on a kept connection, so 99% of every call here
        # was setup. `retrieve` runs twice per composed context, which made this
        # module 3.5 ms of a 7.4 ms composition, on the event loop thread. One
        # worker thread owns one connection instead; `_conn` still hands out a
        # fresh caller-owned connection, because callers pass it to `sqlite_txn`
        # or to `with ... as con`, both of which close what they are given.
        self._worker = SqliteWorker(self._conn, name=f'zero-semantic-{self.db_path.name}')
        # Instances are routinely created without an explicit shutdown (brain,
        # panel, ~10 test files); the finalizer is what stops the thread and its
        # file handle from outliving the object.
        self._finalizer = weakref.finalize(self, self._worker.close)

    def _conn(self):
        con = sqlite3.connect(self.db_path, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA busy_timeout=5000')
        con.execute('PRAGMA foreign_keys=ON')
        return con

    def _run(self, operation: Callable[[sqlite3.Connection], Any]) -> Any:
        """Run *operation(con)* on this module's sqlite thread, one at a time.

        The worker wraps every job in the same commit-on-success /
        rollback-on-error scope `sqlite_txn` provided, and being single-threaded
        it preserves the read-modify-write atomicity `BEGIN IMMEDIATE` is there
        for even when several threads call in at once.
        """
        return self._worker.run(operation)

    def close(self) -> None:
        self._worker.close()

    def _sync_rag_memory(self, con: sqlite3.Connection, memory_id: int) -> None:
        """Keep an optional ZeroStore RAG index consistent in the same transaction."""
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_rag_documents'").fetchone():
            return
        doc_id = f'semantic:{int(memory_id)}'
        con.execute('DELETE FROM memory_rag_fts WHERE doc_id=?', (doc_id,))
        con.execute('DELETE FROM memory_rag_documents WHERE doc_id=?', (doc_id,))
        row = con.execute("SELECT * FROM semantic_user_memory WHERE id=? AND status='active'", (memory_id,)).fetchone()
        if not row:
            return
        now = int(time.time())
        con.execute('INSERT INTO memory_rag_documents(doc_id,chat_id,subject_user_id,scope,layer,category,content,source_telegram_ids_json,source_trace_ids_json,confidence,created_at,updated_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (doc_id,row['chat_id'],row['sender_id'],'personal','semantic',f"{row['category']}.{row['key']}",row['value_json'],row['evidence_message_ids_json'],'[]',row['confidence'],now,now,row['expires_at']))
        con.execute('INSERT INTO memory_rag_fts(doc_id,chat_id,category,layer,content) VALUES (?,?,?,?,?)', (doc_id,str(row['chat_id']),f"{row['category']}.{row['key']}",'semantic',row['value_json']))

    def extract_explicit(self, text: str) -> list[dict]:
        out = []
        raw = text or ''
        patterns = [
            ('identity', 'preferred_name', r'(?:اسمم|اسم\s+من)\s+(?!(?:چی|چیه|چه|کی|رو|را|و)\b)([آ-ی]{2,20}(?:\s+[آ-ی]{2,20})?)'),
            ('identity', 'preferred_name', r'من\s+(?!(?:چی|چیه|چه|کی|کیم|اینجا|هم|که|این)\b)([آ-ی]{2,20})\s+هستم'),
            ('identity', 'nickname', r'(?:منو|من\s+را|من\s+رو)\s+([\wآ-ی‌]{2,20})\s+صدا\s+کن'),
            ('identity', 'nickname', r'لقب\s+من(?:و)?\s+(?:بذار|کن)\s+([\wآ-ی‌]{2,20})'),
            ('interest', 'interest', r'(?:برنامه[‌ ]?نویسی|کدنویسی|فوتبال|هوش مصنوعی)\s+(?:دوست دارم|علاقه دارم)'),
        ]
        for category, key, pattern in patterns:
            m = re.search(pattern, raw, re.I)
            if m:
                value = m.group(1) if m.lastindex else m.group(0)
                out.append({'category': category, 'key': key, 'value': value.strip(), 'confidence': .9})
        return out

    def candidate(self, *, chat_id: int, sender_id: int, category: str, key: str, value, confidence: float, evidence_message_ids=(), source_text: str = '') -> int:
        if category not in CATEGORIES or SENSITIVE.search(json.dumps(value, ensure_ascii=False)):
            raise ValueError('invalid_or_sensitive_candidate')
        if not 0 <= confidence <= 1:
            raise ValueError('invalid_confidence')
        now = int(time.time())
        value_json = json.dumps(value, ensure_ascii=False)
        source_hash = hashlib.sha256(source_text.encode()).hexdigest()
        def _op(con):
            con.execute('BEGIN IMMEDIATE')
            existing = con.execute("SELECT id,confidence,evidence_message_ids_json FROM semantic_user_memory_candidates WHERE chat_id=? AND sender_id=? AND category=? AND key=? AND value_json=? AND status='pending' ORDER BY id DESC LIMIT 1", (chat_id, sender_id, category, key, value_json)).fetchone()
            if existing:
                evidence = list(dict.fromkeys(json.loads(existing['evidence_message_ids_json'] or '[]') + list(evidence_message_ids)))
                con.execute('UPDATE semantic_user_memory_candidates SET confidence=?,evidence_message_ids_json=? WHERE id=?', (max(float(existing['confidence']), confidence), json.dumps(evidence), existing['id']))
                con.commit()
                return int(existing['id'])
            cur = con.execute('INSERT INTO semantic_user_memory_candidates(chat_id,sender_id,category,key,value_json,confidence,evidence_message_ids_json,source_text_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (chat_id, sender_id, category, key, value_json, confidence, json.dumps(list(evidence_message_ids)), source_hash, now))
            con.commit()
            logger.info('SEMANTIC_CANDIDATE_CREATED chat_id=%s sender_id=%s candidate_id=%s confidence=%.2f', chat_id, sender_id, cur.lastrowid, confidence)
            return int(cur.lastrowid)
        return self._run(_op)

    def approve(self, candidate_id: int, reviewer_id: int, ttl_seconds: int | None = None) -> int:
        now = int(time.time())
        def _op(con):
            con.execute('BEGIN IMMEDIATE')
            row = con.execute("SELECT * FROM semantic_user_memory_candidates WHERE id=? AND status='pending'", (candidate_id,)).fetchone()
            if not row or row['confidence'] < 0.6:
                raise ValueError('candidate_not_approvable')
            con.execute("UPDATE semantic_user_memory_candidates SET status='approved',reviewed_at=?,reviewed_by=? WHERE id=?", (now, reviewer_id, candidate_id))
            active = con.execute("SELECT * FROM semantic_user_memory WHERE chat_id=? AND sender_id=? AND category=? AND key=? AND status='active' ORDER BY version DESC LIMIT 1", (row['chat_id'],row['sender_id'],row['category'],row['key'])).fetchone()
            if active and active['value_json'] == row['value_json']:
                con.execute("UPDATE semantic_user_memory SET confidence=?,last_seen_at=?,last_verified_at=? WHERE id=?", (max(float(active['confidence']), float(row['confidence'])), now, now, active['id']))
                self._sync_rag_memory(con, int(active['id']))
                con.commit()
                logger.info('SEMANTIC_MEMORY_DEDUPED candidate_id=%s memory_id=%s', candidate_id, active['id'])
                return int(active['id'])
            prior = con.execute("SELECT MAX(version) AS v FROM semantic_user_memory WHERE chat_id=? AND sender_id=? AND category=? AND key=?", (row['chat_id'],row['sender_id'],row['category'],row['key'])).fetchone()['v'] or 0
            con.execute("UPDATE semantic_user_memory SET status='superseded',last_seen_at=? WHERE chat_id=? AND sender_id=? AND category=? AND key=? AND status='active'", (now,row['chat_id'],row['sender_id'],row['category'],row['key']))
            expires = now + ttl_seconds if ttl_seconds else None
            cur = con.execute('INSERT INTO semantic_user_memory(chat_id,sender_id,category,key,value_json,confidence,evidence_message_ids_json,source_text_hash,first_seen_at,last_seen_at,last_verified_at,expires_at,status,version,created_by_backend) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (row['chat_id'],row['sender_id'],row['category'],row['key'],row['value_json'],row['confidence'],row['evidence_message_ids_json'],row['source_text_hash'],now,now,now,expires,'active',prior+1,self.backend))
            if active:
                self._sync_rag_memory(con, int(active['id']))
            self._sync_rag_memory(con, int(cur.lastrowid))
            con.commit()
            logger.info('SEMANTIC_MEMORY_CONFLICT_RESOLVED candidate_id=%s memory_id=%s', candidate_id, cur.lastrowid)
            return int(cur.lastrowid)
        return self._run(_op)

    def audit_event(self, event_type: str, *, item_id: int | None, chat_id: int, sender_id: int, actor_id: int, action: str, reason: str = '', trace_id: str | None = None) -> str:
        trace_id = trace_id or uuid.uuid4().hex
        def _op(con):
            con.execute('INSERT INTO semantic_memory_audit(event_type,trace_id,item_id,chat_id,sender_id,actor_id,action,reason,timestamp) VALUES(?,?,?,?,?,?,?,?,?)', (event_type, trace_id, item_id, chat_id, sender_id, actor_id, action, reason[:500], int(time.time())))
            con.commit()
        self._run(_op)
        return trace_id

    def inspect_for_actor(self, memory_id: int, *, chat_id: int, sender_id: int, actor_id: int, owner_id: int | None = None, trace_id: str | None = None) -> dict:
        row = self._run(lambda con: con.execute('SELECT * FROM semantic_user_memory WHERE id=?', (memory_id,)).fetchone())
        if not row: raise ValueError('memory_not_found')
        override = owner_id is not None and int(actor_id) == int(owner_id)
        if not override and (int(row['chat_id']) != int(chat_id) or int(row['sender_id']) != int(sender_id)):
            self.audit_event('SEMANTIC_MEMORY_ACCESS_DENIED', item_id=memory_id, chat_id=chat_id, sender_id=sender_id, actor_id=actor_id, action='inspect', reason='identity_scope_mismatch', trace_id=trace_id)
            raise PermissionError('access_denied')
        self.audit_event('SEMANTIC_MEMORY_OWNER_OVERRIDE' if override else 'SEMANTIC_MEMORY_INSPECTED', item_id=memory_id, chat_id=row['chat_id'], sender_id=row['sender_id'], actor_id=actor_id, action='inspect', reason='owner_override' if override else '', trace_id=trace_id)
        return dict(row) | {'value': json.loads(row['value_json']), 'evidence_message_ids': json.loads(row['evidence_message_ids_json'])}

    def correct_for_actor(self, memory_id: int, value, *, chat_id: int, sender_id: int, actor_id: int, owner_id: int | None = None) -> int:
        row = self.inspect_for_actor(memory_id, chat_id=chat_id, sender_id=sender_id, actor_id=actor_id, owner_id=owner_id)
        new_id = self.correct(memory_id, value, actor_id)
        override = owner_id is not None and int(actor_id) == int(owner_id) and (int(row['chat_id']) != int(chat_id) or int(row['sender_id']) != int(sender_id))
        self.audit_event('SEMANTIC_MEMORY_OWNER_OVERRIDE' if override else 'SEMANTIC_MEMORY_CORRECTED', item_id=new_id, chat_id=row['chat_id'], sender_id=row['sender_id'], actor_id=actor_id, action='correct', reason='owner_override' if override else '')
        return new_id

    def forget_for_actor(self, memory_id: int, *, chat_id: int, sender_id: int, actor_id: int, owner_id: int | None = None) -> int:
        row = self.inspect_for_actor(memory_id, chat_id=chat_id, sender_id=sender_id, actor_id=actor_id, owner_id=owner_id)
        count = self.forget(row['chat_id'], row['sender_id'], row['key'])
        override = owner_id is not None and int(actor_id) == int(owner_id) and (int(row['chat_id']) != int(chat_id) or int(row['sender_id']) != int(sender_id))
        self.audit_event('SEMANTIC_MEMORY_OWNER_OVERRIDE' if override else 'SEMANTIC_MEMORY_FORGOTTEN', item_id=memory_id, chat_id=row['chat_id'], sender_id=row['sender_id'], actor_id=actor_id, action='forget', reason='owner_override' if override else '')
        return count

    def retrieve(self, chat_id: int, sender_id: int, limit: int = 20) -> list[dict]:
        now = int(time.time())
        def _op(con):
            rows = con.execute("SELECT * FROM semantic_user_memory WHERE chat_id=? AND sender_id=? AND status='active' AND (expires_at IS NULL OR expires_at>=?) ORDER BY last_verified_at DESC,id DESC LIMIT ?", (chat_id,sender_id,now,limit)).fetchall()
            return [dict(r) | {'value': json.loads(r['value_json']), 'evidence_message_ids': json.loads(r['evidence_message_ids_json'])} for r in rows]
        return self._run(_op)

    def correct(self, memory_id: int, value, reviewer_id: int) -> int:
        value_json = json.dumps(value, ensure_ascii=False)
        if SENSITIVE.search(value_json):
            raise ValueError('invalid_or_sensitive_candidate')
        now = int(time.time())
        def _op(con):
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT * FROM semantic_user_memory WHERE id=? AND status="active"',(memory_id,)).fetchone()
            if not row: raise ValueError('memory_not_found')
            prior = con.execute('SELECT COALESCE(MAX(version),0) FROM semantic_user_memory WHERE chat_id=? AND sender_id=? AND category=? AND key=?', (row['chat_id'],row['sender_id'],row['category'],row['key'])).fetchone()[0]
            source_hash = hashlib.sha256(b'correction').hexdigest()
            candidate = con.execute('INSERT INTO semantic_user_memory_candidates(chat_id,sender_id,category,key,value_json,confidence,evidence_message_ids_json,source_text_hash,created_at,status,reviewed_at,reviewed_by) VALUES(?,?,?,?,?,?,?,?,?,"approved",?,?)', (row['chat_id'],row['sender_id'],row['category'],row['key'],value_json,1.0,row['evidence_message_ids_json'],source_hash,now,now,reviewer_id))
            con.execute('UPDATE semantic_user_memory SET status="superseded",last_seen_at=? WHERE id=?',(now,memory_id))
            created = con.execute('INSERT INTO semantic_user_memory(chat_id,sender_id,category,key,value_json,confidence,evidence_message_ids_json,source_text_hash,first_seen_at,last_seen_at,last_verified_at,expires_at,status,version,created_by_backend) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (row['chat_id'],row['sender_id'],row['category'],row['key'],value_json,1.0,row['evidence_message_ids_json'],source_hash,now,now,now,row['expires_at'],'active',int(prior)+1,self.backend))
            con.execute('UPDATE semantic_user_memory_candidates SET reviewed_by=? WHERE id=?', (reviewer_id, candidate.lastrowid))
            self._sync_rag_memory(con, int(memory_id))
            self._sync_rag_memory(con, int(created.lastrowid))
            return int(created.lastrowid)
        return self._run(_op)

    def forget(self, chat_id: int, sender_id: int, key: str | None = None) -> int:
        def _op(con):
            rows = con.execute('SELECT id FROM semantic_user_memory WHERE chat_id=? AND sender_id=? AND status="active"' + (' AND key=?' if key is not None else ''), ([chat_id,sender_id,key] if key is not None else [chat_id,sender_id])).fetchall()
            q='UPDATE semantic_user_memory SET status="deleted",last_seen_at=? WHERE chat_id=? AND sender_id=? AND status="active"'; args=[int(time.time()),chat_id,sender_id]
            if key is not None: q += ' AND key=?'; args.append(key)
            cur=con.execute(q,args)
            for row in rows: self._sync_rag_memory(con, int(row['id']))
            con.commit(); return cur.rowcount
        return self._run(_op)
