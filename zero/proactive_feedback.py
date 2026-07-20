from __future__ import annotations

import json
import re
import time

_OPT_OUT = re.compile(r"دیگه.*(?:پیگیر|یادآوری)|(?:پیگیر|یادآوری).*(?:نکن|نمی.?خوام)|stop following|do not remind", re.I)
_ANNOYED = re.compile(r"آزاردهنده|مزاحم|رو مخ|اعصاب|annoying|stop bothering", re.I)
_HELPFUL = re.compile(r"مفید|کمک کرد|مرسی|ممنون|خوب بود|helpful|thanks", re.I)
_RESOLVED = re.compile(r"انجام شد|تموم شد|تمام شد|حل شد|درست شد|لغو شد|\b(?:done|completed|fixed|cancelled)\b", re.I)
_WORD = re.compile(r"[\w\u0600-\u06ff‌]{3,}")


class FeedbackService:
    TAXONOMY = {"helpful", "positive", "annoyed", "opt_out", "resolved_before", "ignored", "unknown"}

    def __init__(self, store, router, *, now=None):
        self.store, self.router, self._now = store, router, now or (lambda: int(time.time()))
        with store._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS proactive_followup_feedback(
                id INTEGER PRIMARY KEY,candidate_id TEXT NOT NULL,message_id INTEGER,
                feedback_type TEXT NOT NULL,confidence REAL NOT NULL,source TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,created_at INTEGER NOT NULL)"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS proactive_feedback_preferences(
                chat_id INTEGER NOT NULL,subject_user_id INTEGER NOT NULL,
                proactive_enabled INTEGER NOT NULL DEFAULT 1,timing_multiplier REAL NOT NULL DEFAULT 1.0,
                positive_count INTEGER NOT NULL DEFAULT 0,negative_count INTEGER NOT NULL DEFAULT 0,
                ignored_count INTEGER NOT NULL DEFAULT 0,opt_out_at INTEGER,updated_at INTEGER NOT NULL,
                PRIMARY KEY(chat_id,subject_user_id))"""
            )

    def is_enabled(self, chat_id: int, user_id: int) -> bool:
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT proactive_enabled FROM proactive_feedback_preferences WHERE chat_id=? AND subject_user_id=?",
                (chat_id, user_id),
            ).fetchone()
        return row is None or bool(row[0])

    def adjust_delay(self, chat_id: int, user_id: int, base_hours: int) -> int:
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT timing_multiplier FROM proactive_feedback_preferences WHERE chat_id=? AND subject_user_id=?",
                (chat_id, user_id),
            ).fetchone()
        multiplier = float(row[0]) if row else 1.0
        return max(1, min(720, round(base_hours * multiplier)))

    async def observe(self, message) -> dict:
        target = self._target(message.chat_id, message.sender_id)
        if not target:
            return {"feedback_type": "none", "recorded": False}
        text = str(message.text or "")
        mid = int(message.message_id or 0)
        key = f"{target['id']}:{mid}"
        with self.store._conn() as conn:
            existing = conn.execute(
                "SELECT feedback_type FROM proactive_followup_feedback WHERE idempotency_key=?", (key,)
            ).fetchone()
        if existing:
            return {"feedback_type": existing[0], "recorded": False}

        kind, confidence, source = "unknown", 0.0, "none"
        topic_words = {w.casefold() for w in _WORD.findall(str(target["topic_summary"] or ""))}
        words = {w.casefold() for w in _WORD.findall(text)}
        relevant = bool(topic_words.intersection(words))
        if _OPT_OUT.search(text): kind, confidence, source = "opt_out", 1.0, "explicit"
        elif _ANNOYED.search(text): kind, confidence, source = "annoyed", .95, "explicit"
        elif _HELPFUL.search(text): kind, confidence, source = "helpful", .9, "explicit"
        elif relevant and _RESOLVED.search(text): kind, confidence, source = "resolved_before", .95, "explicit"
        elif relevant:
            try:
                result = await self.router.complete(
                    'Return JSON only: {"version":1,"feedback_type":"positive|annoyed|resolved_before|unknown",'
                    '"confidence":0..1}. Use only this response; do not invent sentiment. topic='
                    + str(target["topic_summary"])[:120] + " response=" + text[:300], max_output_tokens=80,
                )
                data = json.loads(result.text); candidate = data["feedback_type"]; score = float(data["confidence"])
                if data.get("version") == 1 and candidate in self.TAXONOMY and 0 <= score <= 1 and score >= .8:
                    kind, confidence, source = candidate, score, "classifier"
            except Exception:
                pass
        self._record(target, mid, kind, confidence, source, key)
        return {"feedback_type": kind, "recorded": True}

    def sweep_ignored(self, *, threshold_hours: int = 72) -> int:
        now = int(self._now()); cutoff = now - threshold_hours * 3600
        with self.store._conn() as conn:
            rows = conn.execute(
                """SELECT p.*,max(e.created_at) sent_at FROM proactive_followups p
                JOIN proactive_policy_events e ON e.candidate_id=p.id AND e.event='sent'
                WHERE e.created_at<=? GROUP BY p.id HAVING NOT EXISTS(
                  SELECT 1 FROM proactive_followup_feedback f
                  WHERE f.candidate_id=p.id AND f.feedback_type!='unknown')""", (cutoff,)
            ).fetchall()
        count = 0
        for row in rows:
            key = f"{row['id']}:ignored"
            if self._record(dict(row), None, "ignored", .8, "timeout", key): count += 1
        return count

    def _target(self, chat_id, user_id):
        with self.store._conn() as conn:
            row = conn.execute(
                """SELECT p.*,max(e.created_at) sent_at FROM proactive_followups p
                JOIN proactive_policy_events e ON e.candidate_id=p.id AND e.event='sent'
                WHERE p.chat_id=? AND p.subject_user_id=?
                GROUP BY p.id ORDER BY sent_at DESC LIMIT 1""", (chat_id, user_id)
            ).fetchone()
        return dict(row) if row else None

    def _record(self, target, message_id, kind, confidence, source, key) -> bool:
        now = int(self._now())
        with self.store._conn() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO proactive_followup_feedback(
                candidate_id,message_id,feedback_type,confidence,source,idempotency_key,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (target["id"], message_id, kind, confidence, source, key, now),
            )
            if not cur.rowcount: return False
            conn.execute(
                """INSERT OR IGNORE INTO proactive_feedback_preferences(
                chat_id,subject_user_id,updated_at) VALUES(?,?,?)""",
                (target["chat_id"], target["subject_user_id"], now),
            )
            if kind in {"helpful", "positive", "resolved_before"}:
                conn.execute("""UPDATE proactive_feedback_preferences SET positive_count=positive_count+1,
                timing_multiplier=max(.75,timing_multiplier*.9),updated_at=? WHERE chat_id=? AND subject_user_id=?""",
                (now,target["chat_id"],target["subject_user_id"]))
            elif kind in {"annoyed", "ignored"}:
                conn.execute("""UPDATE proactive_feedback_preferences SET negative_count=negative_count+1,
                ignored_count=ignored_count+?,timing_multiplier=min(3.0,timing_multiplier*1.5),updated_at=?
                WHERE chat_id=? AND subject_user_id=?""",
                (1 if kind=="ignored" else 0,now,target["chat_id"],target["subject_user_id"]))
            elif kind == "opt_out":
                conn.execute("""UPDATE proactive_feedback_preferences SET proactive_enabled=0,negative_count=negative_count+1,
                timing_multiplier=3.0,opt_out_at=?,updated_at=? WHERE chat_id=? AND subject_user_id=?""",
                (now,now,target["chat_id"],target["subject_user_id"]))
        return True
