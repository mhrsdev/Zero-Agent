"""Scoped deferred memories and one-shot reminders for group chat.

ponytail: one SQLite table plus the existing template-job runner; upgrade to a
separate queue only if reminder throughput or concurrent chats requires it.
"""
from __future__ import annotations

from .sqlite_tx import sqlite_txn
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .models import IncomingMessage
from .memory import is_sensitive_memory_text

logger = logging.getLogger('zero.deferred_memory')

TZ = ZoneInfo("Asia/Tehran")
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_WEEKDAYS = {"شنبه": 5, "یکشنبه": 6, "دوشنبه": 0, "سه‌شنبه": 1, "سه شنبه": 1, "چهارشنبه": 2, "پنجشنبه": 3, "جمعه": 4}
_EVENT_WORDS = ("امتحان", "آزمون", "جلسه", "قرار", "سفر", "پرواز", "ددلاین", "deadline", "تولد", "باید", "یادم باشه", "یادآوری")
_DEFERRED_DIALOG_PROMPT = """تو خودِ Zero هستی و باید دربارهٔ یک نکتهٔ آینده‌دارِ کاربر، طبیعی و کنجکاوانه گفتگو کنی. این یک گفتگوی واقعی است، نه فرم و نه cron setup. متن سؤال یا پاسخ را خودت به فارسی محاوره‌ای و کوتاه بنویس. هر چیزی که کاربر قبلاً گفته دوباره نپرس. هیچ‌وقت از cron، job، حافظه، زمان یادآوری یا «کی یادآوری کنم» حرف نزن. اگر پیام شامل برنامه یا کار شخصی آینده‌دار با تاریخ یا ساعت صریح است، حتی اگر ساده و روزمره باشد مثل «امشب ساعت ۸:۴۰ باید برم بیرون»، آن را ignore نکن: schedule کن. اگر زمان/تاریخ بخشی ناقص است، ask کن. فقط پیام دربارهٔ گذشته، سؤال معمولی، شوخی، شخص دیگر یا بدون هیچ نشانهٔ آینده‌دار را ignore کن. این فقط امتحان و تولد نیست؛ قرار، سفر، جلسه، کار، deadline و نکته‌های ریز آینده‌دار هم هستند.\n\nخروجی فقط JSON باشد:\n{\"action\":\"ignore|ask|schedule\",\"question\":\"متن سؤال فقط برای ask\",\"reply\":\"پاسخ کوتاه فقط برای schedule\",\"title\":\"عنوان کوتاه\",\"details\":\"جزئیات\",\"due_local\":\"YYYY-MM-DD HH:MM یا null\",\"confidence\":0..1}\n\nاگر تاریخ یا ساعت ناقص است action=ask و فقط همان بخش ناقص را با لحن طبیعی بپرس. اگر تاریخ نسبی مثل فردا/پس‌فردا/هفته بعد گفته شده، با توجه به زمان فعلی تبدیلش کن. اگر کاربر «فردا ساعت ۷ باید برم مدرسه» گفت، دوباره روز و ساعت را نپرس.\n\nزمان فعلی ایران: """

SCHEMA = """
CREATE TABLE IF NOT EXISTS deferred_memories (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 chat_id INTEGER NOT NULL, sender_id INTEGER NOT NULL,
 source_message_id INTEGER, source_trace_id TEXT NOT NULL DEFAULT '',
 kind TEXT NOT NULL DEFAULT 'reminder', status TEXT NOT NULL DEFAULT 'collecting',
 title TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '',
 due_at INTEGER, reminder_job_id TEXT, state_json TEXT NOT NULL DEFAULT '{}',
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deferred_scope ON deferred_memories(chat_id,sender_id,status,updated_at);
CREATE TABLE IF NOT EXISTS user_memory_notes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 chat_id INTEGER NOT NULL, sender_id INTEGER NOT NULL,
 section TEXT NOT NULL, content TEXT NOT NULL, token_estimate INTEGER NOT NULL,
 source_message_id INTEGER, created_at INTEGER NOT NULL, last_used_at INTEGER,
 status TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_user_notes_scope ON user_memory_notes(chat_id,sender_id,status,created_at);
"""


def _now() -> int:
    return int(time.time())


def _norm(text: str) -> str:
    return " ".join((text or "").translate(_DIGITS).replace("ي", "ی").replace("ك", "ک").split()).casefold()


def _time(text: str) -> tuple[int, int] | None:
    m = re.search(r"(?:ساعت\s*)?(\d{1,2})(?:\s*[:٫]\s*(\d{1,2}))?\s*(صبح|ظهر|عصر|شب)?", _norm(text))
    if not m:
        return None
    h, minute = int(m.group(1)), int(m.group(2) or 0)
    if h > 23 or minute > 59:
        return None
    part = m.group(3) or ""
    if part == "شب" and h < 12: h += 12
    if part in {"ظهر", "عصر"} and h < 12: h += 12
    return h, minute


def _date(text: str, now: datetime) -> datetime | None:
    t = _norm(text)
    if "پس فردا" in t: return now + timedelta(days=2)
    if "فردا" in t: return now + timedelta(days=1)
    for name, weekday in _WEEKDAYS.items():
        if name in t:
            days = (weekday - now.weekday()) % 7 or 7
            return now + timedelta(days=days)
    m = re.search(r"(\d{1,2})\s*[/\-]\s*(\d{1,2})(?:\s*[/\-]\s*(\d{4}))?", t)
    if m:
        year = int(m.group(3) or now.year)
        try: return now.replace(year=year, month=int(m.group(2)), day=int(m.group(1)))
        except ValueError: return None
    return None


def _title(text: str) -> str:
    t = " ".join((text or "").split())
    m = re.search(r"(?:امتحان|آزمون|جلسه|قرار|سفر|پرواز|ددلاین|deadline|تولد)\s+(?:دارم|داریم|مهمه|هست)?\s*([^،؛.!؟?]*)", t, re.I)
    if m and m.group(1).strip():
        return (t[:m.end(0)]).strip()[:160]
    for word in _EVENT_WORDS:
        match = re.search(re.escape(word) + r'\s+([^،؛.!؟?]+)', t, re.I)
        if match:
            tail = match.group(1).strip()
            if tail and tail not in {'دارم', 'داریم', 'هست', 'مهمه'}:
                return f'{word} {tail}'[:160]
    return ''


class DeferredMemory:
    # ponytail: one bounded continuation window; move to per-thread state only
    # if the deployment enables forum topics or concurrent reminder dialogs.
    CONTINUATION_TTL = 10 * 60
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        with sqlite3.connect(self.db_path, timeout=5) as con:
            con.execute('PRAGMA busy_timeout=5000'); con.execute('PRAGMA journal_mode=WAL'); con.execute('PRAGMA foreign_keys=ON')
            con.executescript(SCHEMA)

    def _conn(self):
        con = sqlite3.connect(self.db_path, timeout=5)
        con.execute('PRAGMA busy_timeout=5000')
        return con

    def _pending(self, chat_id: int, sender_id: int):
        cutoff = _now() - self.CONTINUATION_TTL
        with sqlite_txn(self._conn()) as con:
            row = con.execute("SELECT * FROM deferred_memories WHERE chat_id=? AND sender_id=? AND status='collecting' AND updated_at>=? ORDER BY updated_at DESC LIMIT 1", (chat_id, sender_id, cutoff)).fetchone()
            con.execute("UPDATE deferred_memories SET status='cancelled',updated_at=? WHERE chat_id=? AND sender_id=? AND status='collecting' AND updated_at<?", (_now(), chat_id, sender_id, cutoff))
            con.commit()
            return row

    @staticmethod
    def _row(row) -> dict[str, Any] | None:
        if not row: return None
        keys = ["id","chat_id","sender_id","source_message_id","source_trace_id","kind","status","title","details","due_at","reminder_job_id","state_json","created_at","updated_at"]
        return dict(zip(keys, row))

    def _save(self, data: dict[str, Any]) -> None:
        now = _now()
        with sqlite_txn(self._conn()) as con:
            con.execute("""INSERT INTO deferred_memories(chat_id,sender_id,source_message_id,source_trace_id,kind,status,title,details,due_at,reminder_job_id,state_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (data['chat_id'],data['sender_id'],data.get('source_message_id'),data.get('source_trace_id',''),data.get('kind','reminder'),data.get('status','collecting'),data.get('title',''),data.get('details',''),data.get('due_at'),data.get('reminder_job_id'),json.dumps(data.get('state',{}),ensure_ascii=False),now,now))

    def _update(self, item_id: int, data: dict[str, Any]) -> None:
        with sqlite_txn(self._conn()) as con:
            con.execute("UPDATE deferred_memories SET status=?,title=?,details=?,due_at=?,state_json=?,updated_at=? WHERE id=?", (data.get('status','collecting'),data.get('title',''),data.get('details',''),data.get('due_at'),json.dumps(data.get('state',{}),ensure_ascii=False),_now(),item_id))

    def _is_continuation(self, message: IncomingMessage, row: dict[str, Any]) -> bool:
        state = json.loads(row.get('state_json') or '{}') if isinstance(row.get('state_json'), str) else row.get('state', {})
        if message.reply_to_message_id and int(message.reply_to_message_id) == int(state.get('last_bot_message_id') or 0):
            return True
        current = set(re.findall(r'[\w\u0600-\u06ff]{3,}', _norm(message.text)))
        prior = set(re.findall(r'[\w\u0600-\u06ff]{3,}', _norm(f"{row.get('title','')} {row.get('details','')}")))
        # A single shared word (for example a name or "باید") is not enough
        # to attach a new group message to an old reminder.  Reply metadata is
        # authoritative; lexical continuation needs at least two stable terms.
        return bool(current and prior and len(current & prior) >= 2)

    def _has_future_signal(self, message: IncomingMessage) -> bool:
        text = _norm(message.text)
        if _date(text, datetime.now(TZ)) or _time(text):
            return True
        # Do not treat every occurrence of «باید/قرار/امتحان» as a reminder.
        # Require an explicit future/reminder construction instead.
        return bool(re.search(
            r'(?:یادت\s*باشه|یادآوری\s*کن|یاد[م]?\s*بنداز|قرار\s+دارم|قرار\s+داریم|'
            r'باید\s+(?:فردا|پس\s*فردا|امشب|امروز|هفته\s+بعد|\d{1,2})|'
            r'(?:امتحان|آزمون|جلسه|سفر|پرواز|ددلاین|تولد)\s+(?:دارم|داریم|هست|است|فردا|پس\s*فردا))',
            text,
            re.I,
        ))

    def should_process(self, message: IncomingMessage) -> bool:
        if message.sender_is_bot or not (message.text or '').strip():
            return False
        row = self._row(self._pending(message.chat_id, message.sender_id))
        # An active reminder changes the gate: only a verified continuation may
        # touch it.  A new future event must not hijack an unrelated collecting
        # dialog from the same sender.
        return bool(self._is_continuation(message, row) if row else self._has_future_signal(message))

    def record_bot_reply(self, chat_id: int, sender_id: int, message_id: int) -> None:
        row = self._row(self._pending(chat_id, sender_id))
        if not row:
            return
        state = json.loads(row.get('state_json') or '{}')
        state['last_bot_message_id'] = int(message_id)
        self._update(row['id'], {**row, 'state': state})

    def capture_note(self, message: IncomingMessage) -> None:
        if message.sender_is_bot:
            return
        text = (message.text or '').strip()
        if not text or is_sensitive_memory_text(text) or len(text) < 8 or not re.search(r"یادت باشه|نکته\s*:|دوست دارم|دوست ندارم|شخصیتم", text, re.I): return
        section = 'likes' if 'دوست دارم' in text else 'dislikes' if 'دوست ندارم' in text else 'notes'
        estimate = max(1, len(text) // 4)
        with sqlite_txn(self._conn()) as con:
            total = con.execute("SELECT COALESCE(SUM(token_estimate),0) FROM user_memory_notes WHERE chat_id=? AND sender_id=? AND status='active'", (message.chat_id,message.sender_id)).fetchone()[0]
            if total + estimate > 10000: return
            con.execute("INSERT INTO user_memory_notes(chat_id,sender_id,section,content,token_estimate,source_message_id,created_at) VALUES(?,?,?,?,?,?,?)", (message.chat_id,message.sender_id,section,text[:1200],estimate,message.message_id,_now()))

    async def process(self, message: IncomingMessage, router) -> tuple[str, dict[str, Any] | None]:
        """Model-led conversation; Python only validates ownership and due time."""
        if message.sender_is_bot or not (message.text or '').strip():
            return '', None
        row = self._row(self._pending(message.chat_id, message.sender_id))
        if row:
            eligible = self._is_continuation(message, row)
        else:
            eligible = self._has_future_signal(message)
        if not eligible:
            return '', None
        now = datetime.now(TZ)
        prior = {}
        if row:
            prior = json.loads(row.get('state_json') or '{}')
        context = {
            'title': row.get('title', '') if row else '',
            'details': row.get('details', '') if row else '',
            'due_local': datetime.fromtimestamp(row['due_at'], TZ).strftime('%Y-%m-%d %H:%M') if row and row.get('due_at') else None,
            'stage': prior.get('stage', '') if row else '',
        }
        prompt = (_DEFERRED_DIALOG_PROMPT + now.strftime('%Y-%m-%d %H:%M') + '\n\nKNOWN STATE:\n' + json.dumps(context, ensure_ascii=False) + '\n\nCURRENT MESSAGE:\n' + message.text[:2000])
        try:
            result = await router.complete(prompt)
            raw = (getattr(result, 'text', '') or '').strip()
            match = re.search(r'\{.*\}', raw, re.S)
            plan = json.loads(match.group(0)) if match else {}
        except Exception:
            return '', None
        action = plan.get('action')
        confidence = float(plan.get('confidence', 0.0) or 0.0)
        logger.info('DEFERRED_PLAN chat_id=%s sender_id=%s action=%s confidence=%.2f has_question=%s has_reply=%s has_due=%s', message.chat_id, message.sender_id, action, confidence, bool(plan.get('question')), bool(plan.get('reply')), bool(plan.get('due_local')))
        if confidence < 0.75 or action not in {'ignore', 'ask', 'schedule'}:
            return '', None
        if action == 'ignore':
            return '', None
        title = str(plan.get('title') or context.get('title') or '')[:160]
        details = str(plan.get('details') or context.get('details') or message.text)[:500]
        state = {'human_verified': True, 'stage': 'dialog', 'model_plan': plan}
        if action == 'ask':
            question = str(plan.get('question') or '').strip()[:500]
            if not question:
                return '', None
            if row:
                self._update(row['id'], {**row, 'title': title, 'details': details, 'state': state})
            else:
                self._save({'chat_id': message.chat_id, 'sender_id': message.sender_id, 'source_message_id': message.message_id, 'source_trace_id': message.trace_id or '', 'title': title, 'details': details, 'state': state})
            return question, None
        due_local = str(plan.get('due_local') or '').strip()
        try:
            due = datetime.strptime(due_local, '%Y-%m-%d %H:%M').replace(tzinfo=TZ)
        except ValueError:
            return '', None
        if due.timestamp() <= time.time() or not title:
            return '', None
        data = {'status': 'ready', 'title': title, 'details': details, 'due_at': int(due.timestamp()), 'state': state}
        if row:
            self._update(row['id'], {**row, **data})
            ready = self._row(self._pending(message.chat_id, message.sender_id))
            if not ready:
                with sqlite_txn(self._conn()) as con:
                    ready = self._row(con.execute("SELECT * FROM deferred_memories WHERE id=?", (row['id'],)).fetchone())
        else:
            self._save({'chat_id': message.chat_id, 'sender_id': message.sender_id, 'source_message_id': message.message_id, 'source_trace_id': message.trace_id or '', **data})
            with sqlite_txn(self._conn()) as con:
                ready = self._row(con.execute("SELECT * FROM deferred_memories WHERE chat_id=? AND sender_id=? ORDER BY id DESC LIMIT 1", (message.chat_id, message.sender_id)).fetchone())
        return str(plan.get('reply') or '').strip()[:500], {'ready': ready} if ready else None

    def create_reminder_job(self, row: dict[str, Any], owner_id: int, sender_label: str = '') -> str:
        state = json.loads(row.get('state_json') or '{}') if isinstance(row.get('state_json'), str) else row.get('state', {})
        if not state.get('human_verified') or int(row.get('sender_id') or 0) <= 0:
            raise ValueError('reminder ownership is not human-verified')
        job_id = 'job_' + __import__('uuid').uuid4().hex[:16]
        due_at = int(row['due_at'])
        text = f"{sender_label} یادت نره: {row.get('title') or row.get('details','')[:240]}".strip()
        now = _now()
        schedule = {'kind': 'once', 'at': due_at, 'timezone': 'Asia/Tehran', 'explanation': 'one-shot reminder'}
        with sqlite_txn(self._conn()) as con:
            con.execute("INSERT INTO cron_jobs(job_id,version,template_id,template_version,owner_user_id,created_by_user_id,chat_id,title,input_json,schedule_json,risk_level,approval_state,state,next_run_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (job_id,1,'reminder','1.0.0',owner_id,row['sender_id'],row['chat_id'], 'deferred reminder', json.dumps({'text': text},ensure_ascii=False), json.dumps(schedule,ensure_ascii=False), 'low','approved','enabled',due_at,now,now))
            con.execute("UPDATE deferred_memories SET status='scheduled',reminder_job_id=?,updated_at=? WHERE id=?", (job_id,now,row['id']))
        return job_id

    def notes_context(self, message: IncomingMessage, limit: int = 6) -> str:
        words = {w for w in re.findall(r"[\w\u0600-\u06ff]{3,}", _norm(message.text))}
        with sqlite_txn(self._conn()) as con:
            rows = con.execute("SELECT id,section,content FROM user_memory_notes WHERE chat_id=? AND sender_id=? AND status='active' ORDER BY created_at DESC LIMIT 100", (message.chat_id,message.sender_id)).fetchall()
            selected = [row for row in rows if not words or words & set(re.findall(r"[\w\u0600-\u06ff]{3,}", _norm(row[2])))] [:limit]
            if selected:
                con.executemany('UPDATE user_memory_notes SET last_used_at=? WHERE id=?', [(_now(), row[0]) for row in selected])
                con.commit()
        return '\n'.join(f"[{section}] {content}" for _,section,content in selected)
