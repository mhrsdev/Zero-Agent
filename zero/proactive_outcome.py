from __future__ import annotations

from .sqlite_tx import sqlite_txn
import json
import re
import time
from dataclasses import dataclass

_DONE = re.compile(r"انجام شد|تموم شد|تمام شد|حل شد|درست شد|ثبت شد|خریدم|لغو شد|\b(?:done|completed|fixed|cancelled)\b", re.I)
_PENDING = re.compile(r"هنوز|نشد|بعداً|\b(?:not yet|still pending|later)\b", re.I)
_WORD = re.compile(r"[\w\u0600-\u06ff‌]{3,}")


@dataclass(frozen=True)
class OutcomeResult:
    status: str
    reason: str
    evidence: int | None
    confidence: float


class OutcomeDetector:
    """Current-chat/current-subject outcome classifier with persistent evidence refs."""

    def __init__(self, store, router):
        self.store, self.router = store, router
        with sqlite_txn(store._conn()) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS proactive_followup_outcomes(
                candidate_id TEXT PRIMARY KEY,status TEXT NOT NULL,reason TEXT NOT NULL,
                evidence_message_id INTEGER,confidence REAL NOT NULL,updated_at INTEGER NOT NULL)"""
            )

    async def detect(self, candidate, messages) -> OutcomeResult:
        if candidate.get("status") in {"resolved", "cancelled", "expired"}:
            return self._save(candidate, OutcomeResult("resolved", "candidate_terminal", None, 1.0))
        topic = " ".join((str(candidate.get("topic_summary") or ""), str(candidate.get("goal") or "")))
        topic_words = {w.casefold() for w in _WORD.findall(topic)}
        relevant = []
        for message in messages:
            if int(message.get("chat_id", candidate.get("chat_id", 0))) != int(candidate.get("chat_id", 0)):
                continue
            if int(message.get("sender_id") or 0) != int(candidate.get("subject_user_id") or 0):
                continue
            if int(message.get("created_at") or 0) <= int(candidate.get("created_at") or 0):
                continue
            text = str(message.get("text") or "")
            words = {w.casefold() for w in _WORD.findall(text)}
            reply = int(message.get("reply_to_message_id") or 0) == int(candidate.get("source_message_id") or -1)
            if reply or topic_words.intersection(words):
                relevant.append((message, text))
        for message, text in reversed(relevant):
            evidence = int(message.get("telegram_message_id") or 0) or None
            if _PENDING.search(text):
                return self._save(candidate, OutcomeResult("pending", "explicit_still_open", evidence, .95))
            if _DONE.search(text):
                return self._save(candidate, OutcomeResult("resolved", "explicit_outcome", evidence, .95))
        if not relevant:
            return self._save(candidate, OutcomeResult("unknown", "no_relevant_evidence", None, 0.0))
        excerpts = [text[:240] for _, text in relevant[-4:]]
        prompt = (
            'Return JSON only: {"version":1,"status":"pending|resolved|unknown",'
            '"reason":"short","confidence":0..1}. Use only the supplied current-chat subject evidence; '
            'do not invent completion. topic=' + topic[:240] + " evidence=" + json.dumps(excerpts, ensure_ascii=False)
        )
        try:
            raw = await self.router.complete(prompt, max_output_tokens=100)
            data = json.loads(raw.text)
            status = data["status"]; confidence = float(data["confidence"]); reason = str(data.get("reason") or "model")[:80]
            if data.get("version") != 1 or status not in {"pending", "resolved", "unknown"} or not 0 <= confidence <= 1:
                raise ValueError("invalid outcome")
            if status == "resolved" and confidence < .8:
                status, reason = "unknown", "low_confidence"
            evidence = int(relevant[-1][0].get("telegram_message_id") or 0) or None
            return self._save(candidate, OutcomeResult(status, reason, evidence, confidence))
        except Exception:
            return self._save(candidate, OutcomeResult("unknown", "classifier_failure", None, 0.0))

    def _save(self, candidate, result: OutcomeResult) -> OutcomeResult:
        with sqlite_txn(self.store._conn()) as conn:
            conn.execute(
                """INSERT INTO proactive_followup_outcomes(candidate_id,status,reason,evidence_message_id,confidence,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET status=excluded.status,
                reason=excluded.reason,evidence_message_id=excluded.evidence_message_id,
                confidence=excluded.confidence,updated_at=excluded.updated_at""",
                (candidate["id"], result.status, result.reason, result.evidence, result.confidence, int(time.time())),
            )
        return result
