"""Safe V1 template-job control plane.

This module deliberately contains no code interpreter, shell invocation, subprocess,
filesystem path supplied by users, package installation, or external runner.
Only handlers in TEMPLATE_REGISTRY can execute, using validated structured input.
"""
from __future__ import annotations

import asyncio
import calendar
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .config import ZeroConfig
from .storage import ZeroStore

logger = logging.getLogger(__name__)

OWNER_CAPABILITIES = frozenset({
    'cron.create', 'cron.delete', 'cron.pause', 'cron.resume', 'cron.logs',
    'cron.metrics', 'cron.templates', 'cron.network', 'cron.workflow',
    'cron.python', 'cron.approve', 'cron.role.grant', 'cron.role.revoke',
})
ADMIN_CAPABILITIES = frozenset({'cron.create', 'cron.delete', 'cron.pause', 'cron.resume', 'cron.logs', 'cron.metrics', 'cron.templates'})
SAFE_CAPABILITIES = frozenset({'cron.create', 'cron.pause', 'cron.resume', 'cron.logs', 'cron.metrics'})
FORBIDDEN_REQUEST = re.compile(r'\b(?:python|shell|bash|zsh|sh\b|script|subprocess|exec|eval|docker|podman|firecracker|runner|pip|npm|apt|curl|wget)\b|(?:کد|اسکریپت|شل|بش|پایتون|داکر|رانر|اجرا)', re.I)


@dataclass(frozen=True)
class Template:
    template_id: str
    version: str
    title: str
    risk: str
    network: bool
    fields: frozenset[str]
    enabled: bool = True
    reason: str = ''


TEMPLATE_REGISTRY: dict[str, Template] = {
    'reminder': Template('reminder', '1.0.0', 'Reminder', 'low', False, frozenset({'text'})),
    'ai_news': Template('ai_news', '1.1.0', 'AI News', 'medium', True, frozenset({'query', 'only_new'})),
    'daily_news': Template('daily_news', '1.1.0', 'Daily News', 'medium', True, frozenset({'query', 'only_new'})),
    'war_news': Template('war_news', '1.0.0', 'War News', 'medium', True, frozenset({'query', 'only_new'})),
    'telegram_digest': Template('telegram_digest', '1.0.0', 'Telegram Digest', 'medium', True, frozenset({'query'})),
    'search_digest': Template('search_digest', '1.0.0', 'Search Digest', 'medium', True, frozenset({'query'})),
    'crypto_price': Template('crypto_price', '1.0.0', 'Crypto Price', 'medium', True, frozenset({'asset'})),
    'weather': Template('weather', '1.0.0', 'Weather', 'medium', True, frozenset({'city'})),
    'group_summary': Template('group_summary', '1.0.0', 'Group Summary', 'low', False, frozenset()),
    'weekly_summary': Template('weekly_summary', '1.0.0', 'Weekly Summary', 'low', False, frozenset()),
    'health_check': Template('health_check', '1.0.0', 'Health Check', 'low', False, frozenset()),
    'heartbeat': Template('heartbeat', '1.0.0', 'Heartbeat', 'low', False, frozenset()),
    'reaction_stats': Template('reaction_stats', '1.0.0', 'Reaction Statistics', 'low', False, frozenset()),
    'social_stats': Template('social_stats', '1.0.0', 'Social Statistics', 'low', False, frozenset()),
    'memory_cleanup': Template('memory_cleanup', '1.0.0', 'Memory Cleanup', 'medium', False, frozenset()),
    'cache_cleanup': Template('cache_cleanup', '1.0.0', 'Cache Cleanup', 'medium', False, frozenset()),
    # Deliberately registered but disabled: physical log rotation is host administration, not a user job.
    'log_rotation': Template('log_rotation', '1.0.0', 'Log Rotation', 'critical', False, frozenset(), False, 'Host log rotation is unavailable in safe V1.'),
    'backup_reminder': Template('backup_reminder', '1.0.0', 'Backup Reminder', 'low', False, frozenset()),
    'nightly_knowledge_refresh.v1': Template('nightly_knowledge_refresh.v1', '1.0.0', 'Zero Nightly Knowledge Worker', 'medium', False, frozenset()),
}


class JobSecurityError(ValueError):
    pass


def _now() -> int:
    return int(time.time())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _next_run(schedule: dict[str, Any], now: int | None = None) -> int:
    now = now or _now()
    zone = ZoneInfo(str(schedule.get('timezone', 'Asia/Tehran')))
    current = datetime.fromtimestamp(now, zone)
    kind = schedule.get('kind')
    if kind == 'interval':
        seconds = int(schedule.get('seconds', 0))
        if not 60 <= seconds <= 31_536_000:
            raise JobSecurityError('Interval باید بین ۶۰ ثانیه تا یک سال باشد.')
        return now + seconds
    hour, minute = int(schedule.get('hour', -1)), int(schedule.get('minute', 0))
    if kind in {'daily', 'weekly', 'monthly'} and (not 0 <= hour <= 23 or not 0 <= minute <= 59):
        raise JobSecurityError('ساعت schedule نامعتبر است.')
    if kind == 'daily':
        candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= current: candidate += timedelta(days=1)
        return int(candidate.timestamp())
    if kind == 'weekly':
        weekday = int(schedule.get('weekday', -1))
        if not 0 <= weekday <= 6: raise JobSecurityError('Weekly schedule نامعتبر است.')
        candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=(weekday-current.weekday()) % 7)
        if candidate <= current: candidate += timedelta(days=7)
        return int(candidate.timestamp())
    if kind == 'monthly':
        target = schedule.get('day', 1)
        def in_month(year: int, month: int) -> datetime:
            day = calendar.monthrange(year, month)[1] if target == 'last' else int(target)
            return current.replace(year=year, month=month, day=min(day, calendar.monthrange(year, month)[1]), hour=hour, minute=minute, second=0, microsecond=0)
        candidate = in_month(current.year, current.month)
        if candidate <= current:
            year, month = (current.year + 1, 1) if current.month == 12 else (current.year, current.month + 1)
            candidate = in_month(year, month)
        return int(candidate.timestamp())
    if kind == 'once':
        at = int(schedule.get('at', 0))
        if at <= 0: raise JobSecurityError('زمان یادآوری نامعتبر است.')
        return at
    raise JobSecurityError('فقط scheduleهای once، interval، daily، weekly و monthly در V1 مجازند.')


_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
_NUMBER_WORDS = {'یک': 1, 'دو': 2, 'سه': 3, 'چهار': 4, 'پنج': 5, 'شش': 6, 'هفت': 7, 'هشت': 8, 'نه': 9, 'ده': 10, 'دوازده': 12, 'سی': 30}

def _normalized(text: str) -> str:
    text = text.translate(_DIGITS).replace('ي', 'ی').replace('ك', 'ک').casefold()
    for word, value in _NUMBER_WORDS.items(): text = re.sub(rf'(?<!\w){word}(?!\w)', str(value), text)
    return ' '.join(text.split())

def _explicit_time(text: str) -> tuple[int, int] | None:
    match = re.search(r'(?:ساعت\s*)?(\d{1,2})(?:\s*[:٫]\s*(\d{1,2}))?\s*(?:صبح|شب|ظهر|غروب)?', text)
    if not match: return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59: raise JobSecurityError('ساعت نامعتبر است.')
    return hour, minute

def _needs_hour(text: str) -> None:
    if not _explicit_time(text): raise JobSecurityError('منظورت هر روز/شب چه ساعتی است؟')

def _schedule(text: str) -> dict[str, Any]:
    hour = _explicit_time(text)
    prefix = {'timezone': 'Asia/Tehran'}
    every = re.search(r'هر\s*(\d+)\s*(دقیقه|ساعت|روز)', text)
    if every:
        amount, unit = int(every.group(1)), every.group(2)
        seconds = amount * {'دقیقه': 60, 'ساعت': 3600, 'روز': 86400}[unit]
        return {**prefix, 'kind': 'interval', 'seconds': seconds, 'explanation': f'هر {amount} {unit}'}
    if 'هر هفته' in text:
        return {**prefix, 'kind': 'interval', 'seconds': 7 * 86400, 'explanation': 'هر هفته'}
    if 'هر ماه' in text or 'اول ماه' in text or 'آخر ماه' in text:
        _needs_hour(text); h, m = hour  # type: ignore[misc]
        day = 'last' if 'آخر ماه' in text else 1
        return {**prefix, 'kind': 'monthly', 'day': day, 'hour': h, 'minute': m, 'explanation': ('آخر ماه' if day == 'last' else 'اول هر ماه') + f' ساعت {h:02d}:{m:02d}'}
    weekdays = {'شنبه': 5, 'جمعه': 4}
    for label, weekday in weekdays.items():
        if f'هر {label}' in text:
            _needs_hour(text); h, m = hour  # type: ignore[misc]
            return {**prefix, 'kind': 'weekly', 'weekday': weekday, 'hour': h, 'minute': m, 'explanation': f'هر {label} ساعت {h:02d}:{m:02d}'}
    if any(value in text for value in ('هر روز', 'هر شب', 'هر صبح', 'هر ظهر', 'هر غروب')):
        _needs_hour(text); h, m = hour  # type: ignore[misc]
        return {**prefix, 'kind': 'daily', 'hour': h, 'minute': m, 'explanation': f'هر روز ساعت {h:02d}:{m:02d}'}
    raise JobSecurityError('Schedule روشن نیست؛ مثلاً «هر روز ساعت ۸» یا «هر ۶ ساعت» بگو.')


def parse_natural_job(text: str) -> tuple[str, dict[str, Any], dict[str, Any], str]:
    """Deterministic Persian/English intent parser; no LLM result is executable."""
    raw = ' '.join(text.strip().split())
    if FORBIDDEN_REQUEST.search(raw): raise JobSecurityError('اجرای کد، اسکریپت و Runner امن در V1 فعال نیست.')
    lower = _normalized(raw)
    only_new = bool(re.search(r'تکراری|خبر جدید نبود|چیزی نگه|فقط جدید', lower))
    if any(x in lower for x in ('جنگ', 'اوکراین', 'غزه', 'درگیری', 'iran امریکا', 'ایران آمریکا')):
        template, inputs = 'war_news', {'query': 'latest world war news Ukraine Gaza Iran US conflicts', 'only_new': only_new}
    elif any(x in lower for x in ('اخبار ai', 'هوش مصنوعی', 'gemini', 'openai', 'claude')) or re.search(r'\bai\b', lower):
        template, inputs = 'ai_news', {'query': 'latest AI news', 'only_new': only_new}
    elif 'اتریوم' in lower or re.search(r'\beth\b|ethereum', lower):
        template, inputs = 'crypto_price', {'asset': 'ETH'}
    elif any(x in lower for x in ('بیت کوین', 'بیت‌کوین', 'bitcoin', 'btc')):
        template, inputs = 'crypto_price', {'asset': 'BTC'}
    elif ('گروه' in lower and any(x in lower for x in ('آمار', 'statistics', 'stats'))):
        template, inputs = 'social_stats', {}
    elif 'گروه' in lower and any(x in lower for x in ('خلاصه', 'جمع بندی', 'جمع‌بندی', 'اتفاقات')):
        template, inputs = 'group_summary', {}
    elif any(x in lower for x in ('خلاصه هفته', 'جمع بندی هفته', 'جمع‌بندی هفته')):
        template, inputs = 'weekly_summary', {}
    elif any(x in lower for x in ('آب', 'یادآور', 'یادم بنداز', 'reminder')):
        template, inputs = 'reminder', {'text': raw}
    elif any(x in lower for x in ('وضعیت زیرو', 'سلامت زیرو', 'health', 'سلامت')):
        template, inputs = 'health_check', {}
    elif 'تلگرام' in lower and any(x in lower for x in ('خلاصه', 'دیجست')):
        template, inputs = 'telegram_digest', {'query': 'latest'}
    elif any(x in lower for x in ('اخبار', 'خبرهای مهم', 'اخبار روز', 'news')):
        template, inputs = 'daily_news', {'query': 'latest important news', 'only_new': only_new}
    else: raise JobSecurityError('Template امن متناسب پیدا نشد؛ موضوع و schedule روشن بگو.')
    return template, inputs, _schedule(lower), raw


class TemplateJobService:
    def __init__(
        self, store: ZeroStore, config: ZeroConfig, web: Any | None = None,
        knowledge: Any | None = None, summary_builder: Any | None = None,
    ):
        self.store, self.config, self.web, self.knowledge = store, config, web, knowledge
        self.summary_builder = summary_builder

    async def _audit(self, actor: int, action: str, obj_type: str, obj_id: str, details: dict[str, Any], trace_id: str = '') -> None:
        role = await self.role_for(actor)
        now = _now()
        async with self.store._lock:
            with self.store._conn() as conn:
                prior = conn.execute('SELECT event_hash FROM cron_audit ORDER BY id DESC LIMIT 1').fetchone()
                previous = prior['event_hash'] if prior else '0' * 64
                payload = _json({'trace_id': trace_id, 'actor': actor, 'role': role, 'action': action, 'object': [obj_type, obj_id], 'details': details, 'previous': previous, 'at': now})
                event_hash = hashlib.sha256(payload.encode()).hexdigest()
                conn.execute('INSERT INTO cron_audit(trace_id,actor_user_id,actor_role,action,object_type,object_id,details_json,previous_hash,event_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)', (trace_id or uuid.uuid4().hex[:12], actor, role, action, obj_type, obj_id, _json(details), previous, event_hash, now))
                conn.commit()

    async def role_for(self, user_id: int) -> str:
        if int(user_id) == int(self.config.owner_user_id):
            return 'owner'
        async with self.store._lock:
            with self.store._conn() as conn:
                row = conn.execute('SELECT role FROM cron_permissions WHERE user_id=?', (user_id,)).fetchone()
                return str(row['role']) if row else 'normal_user'

    async def capabilities_for(self, user_id: int) -> frozenset[str]:
        role = await self.role_for(user_id)
        if role == 'owner': return OWNER_CAPABILITIES
        if role == 'cron_admin': return ADMIN_CAPABILITIES
        async with self.store._lock:
            with self.store._conn() as conn:
                row = conn.execute('SELECT capabilities_json FROM cron_permissions WHERE user_id=?', (user_id,)).fetchone()
                return frozenset(json.loads(row['capabilities_json'])) if row else frozenset()

    async def require(self, user_id: int, capability: str) -> None:
        if capability not in await self.capabilities_for(user_id):
            raise JobSecurityError('دسترسی لازم برای این عملیات را نداری.')

    async def grant_cron_admin(self, actor: int, target: int, enabled: bool) -> None:
        if int(actor) != int(self.config.owner_user_id):
            raise JobSecurityError('فقط Owner می‌تواند Cron Admin تعیین یا حذف کند.')
        if int(target) == int(self.config.owner_user_id):
            raise JobSecurityError('Owner از config شناخته می‌شود و قابل تغییر نیست.')
        now = _now(); role = 'cron_admin' if enabled else 'normal_user'
        async with self.store._lock:
            with self.store._conn() as conn:
                conn.execute('INSERT INTO cron_permissions(user_id,role,capabilities_json,granted_by,version,created_at,updated_at) VALUES (?,?,?,?,1,?,?) ON CONFLICT(user_id) DO UPDATE SET role=excluded.role,capabilities_json=excluded.capabilities_json,granted_by=excluded.granted_by,version=cron_permissions.version+1,updated_at=excluded.updated_at', (target, role, _json(sorted(ADMIN_CAPABILITIES if enabled else SAFE_CAPABILITIES)), actor, now, now)); conn.commit()
        await self._audit(actor, 'ROLE_GRANTED' if enabled else 'ROLE_REVOKED', 'permission', str(target), {'role': role})

    async def template_list(self) -> list[dict[str, Any]]:
        return [dict(template_id=t.template_id, version=t.version, title=t.title, risk=t.risk, network=t.network, enabled=t.enabled, reason=t.reason) for t in TEMPLATE_REGISTRY.values()]

    def _validate(self, template_id: str, inputs: dict[str, Any], schedule: dict[str, Any]) -> Template:
        template = TEMPLATE_REGISTRY.get(template_id)
        if not template or not template.enabled:
            raise JobSecurityError(template.reason or 'این Template در V1 مجاز نیست.')
        if set(inputs) - template.fields:
            raise JobSecurityError('Input غیرمجاز برای Template.')
        if any((not isinstance(v, (str, bool))) or (isinstance(v, str) and len(v) > 240) for v in inputs.values()):
            raise JobSecurityError('Input Template نامعتبر یا بیش از حد بلند است.')
        _next_run(schedule)
        return template

    async def simulate(self, actor: int, template_id: str, inputs: dict[str, Any], schedule: dict[str, Any]) -> dict[str, Any]:
        await self.require(actor, 'cron.create')
        template = self._validate(template_id, inputs, schedule)
        approval = 'owner' if template.risk == 'high' else ('cron_admin_or_owner' if template.risk == 'medium' else 'creator_confirmation')
        return {'template': template.title, 'template_id': template_id, 'template_version': template.version, 'schedule': schedule.get('explanation', schedule['kind']), 'next_run_at': _next_run(schedule), 'risk': template.risk, 'approval_required': approval, 'network': 'allowlisted internal service only' if template.network else 'off', 'resources': {'python': 'none', 'shell': 'none', 'runner': 'none', 'timeout_seconds': 30, 'output_limit': '1MB'}, 'host_access': 'none'}

    async def create_draft(self, actor: int, chat_id: int, title: str, template_id: str, inputs: dict[str, Any], schedule: dict[str, Any]) -> dict[str, Any]:
        simulation = await self.simulate(actor, template_id, inputs, schedule)
        template = TEMPLATE_REGISTRY[template_id]; now = _now(); job_id = 'job_' + uuid.uuid4().hex[:16]
        async with self.store._lock:
            with self.store._conn() as conn:
                conn.execute('INSERT INTO cron_jobs(job_id,version,template_id,template_version,owner_user_id,created_by_user_id,chat_id,title,input_json,schedule_json,risk_level,approval_state,state,next_run_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (job_id,1,template_id,template.version,self.config.owner_user_id,actor,chat_id,title[:120],_json(inputs),_json(schedule),template.risk,'pending','draft',None,now,now)); conn.commit()
        await self._audit(actor, 'JOB_DRAFTED', 'job', job_id, {'template': template_id, 'risk': template.risk})
        return {'job_id': job_id, **simulation}

    async def approve(self, actor: int, job_id: str) -> dict[str, Any]:
        async with self.store._lock:
            with self.store._conn() as conn:
                row = conn.execute('SELECT * FROM cron_jobs WHERE job_id=?', (job_id,)).fetchone()
                if not row: raise JobSecurityError('Job پیدا نشد.')
                job = dict(row)
        role = await self.role_for(actor)
        if job['risk_level'] in {'high', 'critical'} and int(actor) != int(self.config.owner_user_id): raise JobSecurityError('Approval این risk فقط برای Owner است.')
        if job['risk_level'] == 'medium' and role not in {'owner', 'cron_admin'}: raise JobSecurityError('Approval Medium فقط برای Cron Admin یا Owner است.')
        if job['risk_level'] == 'low' and int(actor) not in {int(job['created_by_user_id']), int(self.config.owner_user_id)}: raise JobSecurityError('فقط سازنده یا Owner می‌تواند این draft را تأیید کند.')
        schedule = json.loads(job['schedule_json']); nxt = _next_run(schedule)
        async with self.store._lock:
            with self.store._conn() as conn:
                conn.execute("UPDATE cron_jobs SET approval_state='approved',state='enabled',next_run_at=?,updated_at=?,version=version+1 WHERE job_id=?", (nxt,_now(),job_id)); conn.commit()
        await self._audit(actor, 'JOB_APPROVED', 'job', job_id, {'next_run_at': nxt})
        return await self.status(job_id)

    async def status(self, job_id: str, actor: int | None = None) -> dict[str, Any]:
        if actor is not None:
            await self.require(actor, 'cron.logs')
        async with self.store._lock:
            with self.store._conn() as conn:
                row = conn.execute('SELECT * FROM cron_jobs WHERE job_id=?', (job_id,)).fetchone()
                if not row: raise JobSecurityError('Job پیدا نشد.')
                data = dict(row); metric = conn.execute('SELECT * FROM cron_metrics WHERE job_id=?',(job_id,)).fetchone(); data['metrics'] = dict(metric) if metric else {}
                return data

    async def list_jobs(self, actor: int | None = None) -> list[dict[str, Any]]:
        if actor is not None:
            await self.require(actor, 'cron.logs')
        async with self.store._lock:
            with self.store._conn() as conn:
                return [dict(row) for row in conn.execute('SELECT * FROM cron_jobs ORDER BY created_at DESC').fetchall()]

    async def set_state(self, actor: int, job_id: str, state: str) -> None:
        action = {'paused':'JOB_PAUSED','enabled':'JOB_ENABLED','disabled':'JOB_DISABLED'}.get(state)
        if not action: raise JobSecurityError('State نامعتبر.')
        await self.require(actor, 'cron.pause' if state == 'paused' else 'cron.resume')
        async with self.store._lock:
            with self.store._conn() as conn:
                row = conn.execute('SELECT job_id FROM cron_jobs WHERE job_id=?',(job_id,)).fetchone()
                if not row: raise JobSecurityError('Job پیدا نشد.')
                conn.execute('UPDATE cron_jobs SET state=?,updated_at=?,version=version+1 WHERE job_id=?',(state,_now(),job_id)); conn.commit()
        await self._audit(actor, action, 'job', job_id, {})

    async def delete(self, actor: int, job_id: str) -> None:
        await self.require(actor, 'cron.delete')
        async with self.store._lock:
            with self.store._conn() as conn:
                conn.execute("UPDATE cron_jobs SET state='deleted',updated_at=?,version=version+1 WHERE job_id=?",(_now(),job_id)); conn.commit()
        await self._audit(actor, 'JOB_DELETED', 'job', job_id, {})

    async def _execute_template(self, job: dict[str, Any]) -> str:
        template = job['template_id']; inputs = json.loads(job['input_json']); chat = int(job['chat_id'])
        if template == 'reminder': return '⏰ یادآوری: ' + inputs['text'][:800]
        if template == 'group_summary':
            unavailable = '📋 خلاصه گروه: خلاصه‌ساز فعلاً در دسترس نیست؛ پیام‌های خام ارسال نشدند.'
            if self.summary_builder is None:
                return unavailable
            try:
                summary = await self.summary_builder(chat, since_ts=_now() - 86_400)
            except Exception as exc:
                logger.warning('GROUP_SUMMARY_FAILED chat_id=%s exception_type=%s', chat, type(exc).__name__)
                return unavailable
            return '📋 خلاصه گروه:\n' + (summary or 'در ۲۴ ساعت اخیر گفت‌وگوی معنادار کافی ثبت نشده است.')
        if template == 'weekly_summary':
            recent = await self.store.get_recent(chat, limit=12)
            text = ' | '.join(str(r.get('text',''))[:80] for r in recent[-6:])
            return ('📋 خلاصه گروه: ' + (text or 'پیام کافی برای خلاصه وجود ندارد.'))[:1000]
        if template == 'reaction_stats':
            return f"📊 Reaction stats: sent last hour={await self.store.count_rate_events(0, 'reaction_sent', 3600)}"
        if template == 'social_stats':
            state = await self.store.get_social_group_state(chat)
            return f"📊 Social stats: reputation={state['social_reputation']} acceptance={state['reply_acceptance_count']}"
        if template == 'health_check': return '💚 Health Check: Template Job control plane پاسخ‌گو است؛ runner/code execution غیرفعال است.'
        if template == 'heartbeat': return '💓 Heartbeat: Zero Template Job Scheduler فعال است.'
        if template == 'backup_reminder': return '🛡️ یادآوری Backup: وضعیت backup را بررسی کن.'
        if template in {'memory_cleanup','cache_cleanup'}: return '🧹 Safe cleanup: هیچ فایل Host یا script اجرا نشد؛ فقط عملیات داخلی محدود مجاز است.'
        if template == 'nightly_knowledge_refresh.v1':
            if self.knowledge is None: return 'KNOWLEDGE_RUN_FAILED: worker_not_configured'
            result = await self.knowledge.run_nightly(dry_run=False)
            return 'KNOWLEDGE_RUN_COMPLETED ' + _json(result)
        if template in {'ai_news', 'daily_news', 'war_news', 'search_digest'}:
            if self.web is None:
                return f"📰 {TEMPLATE_REGISTRY[template].title}: provider داخلی برای این اجرا پیکربندی نشده است."
            hits = await asyncio.to_thread(self.web.search, inputs.get('query', 'latest news'))
            if not hits:
                return '' if inputs.get('only_new') else f"📰 {TEMPLATE_REGISTRY[template].title}: خبر معتبری پیدا نشد."
            summary = '\n'.join(f'• {hit.title[:170]}' for hit in hits[:5])
            result = f"📰 {TEMPLATE_REGISTRY[template].title}\n{summary}"[:1000]
            if inputs.get('only_new'):
                async with self.store._lock:
                    with self.store._conn() as conn:
                        prior = conn.execute("SELECT result_text FROM cron_runs WHERE job_id=? AND state='succeeded' AND result_text<>'' ORDER BY created_at DESC LIMIT 1", (job['job_id'],)).fetchone()
                if prior and prior['result_text'] == result:
                    return ''
            return result
        # Network templates remain allowlisted, internal-provider-only contracts.
        return f"📰 {TEMPLATE_REGISTRY[template].title}: Template آماده است، اما provider داخلی برای این اجرا پیکربندی نشده است."

    async def run_due(self, now: int | None = None, deliver=None) -> list[dict[str, Any]]:
        now = _now() if now is None else now; delivered: list[dict[str, Any]] = []
        async with self.store._lock:
            with self.store._conn() as conn:
                due = [dict(r) for r in conn.execute("SELECT * FROM cron_jobs WHERE state='enabled' AND approval_state='approved' AND next_run_at<=?",(now,)).fetchall()]
        for job in due:
            run_id, trace = 'run_' + uuid.uuid4().hex[:16], uuid.uuid4().hex[:12]; started = _now()
            try:
                async with self.store._lock:
                    with self.store._conn() as conn:
                        row = conn.execute('SELECT run_id,state FROM cron_runs WHERE job_id=? AND scheduled_for=? ORDER BY created_at DESC LIMIT 1', (job['job_id'], job['next_run_at'])).fetchone()
                        if row and row['state'] == 'succeeded': continue
                        if row:
                            run_id = row['run_id']
                            conn.execute('UPDATE cron_runs SET state=\'running\',started_at=?,finished_at=NULL,exception_type=NULL,exit_code=NULL WHERE run_id=?', (started, run_id))
                        else:
                            conn.execute('INSERT INTO cron_runs(run_id,job_id,job_version,scheduled_for,state,started_at,trace_id,created_at) VALUES (?,?,?,?,?,?,?,?)',(run_id,job['job_id'],job['version'],job['next_run_at'],'running',started,trace,started))
                        conn.commit()
                result = await self._execute_template(job); finished = _now(); duration = max(0,(finished-started)*1000)
                if result and deliver is not None:
                    await deliver(job, result)
                schedule = json.loads(job['schedule_json']); nxt = _next_run(schedule, max(now, job['next_run_at']))
                async with self.store._lock:
                    with self.store._conn() as conn:
                        if schedule.get('kind') == 'once':
                            conn.execute("UPDATE cron_jobs SET state='completed',last_run_at=?,next_run_at=NULL,updated_at=? WHERE job_id=?", (finished,finished,job['job_id']))
                        else:
                            nxt = _next_run(schedule, max(now, job['next_run_at']))
                            conn.execute('UPDATE cron_jobs SET last_run_at=?,next_run_at=?,updated_at=? WHERE job_id=?',(finished,nxt,finished,job['job_id']))
                        conn.execute("UPDATE cron_runs SET state='succeeded',finished_at=?,duration_ms=?,exit_code=0,result_text=? WHERE run_id=?",(finished,duration,result,run_id))
                        conn.execute('INSERT INTO cron_metrics(job_id,run_count,success_count,failure_count,total_duration_ms,last_duration_ms,updated_at) VALUES (?,1,1,0,?,?,?) ON CONFLICT(job_id) DO UPDATE SET run_count=run_count+1,success_count=success_count+1,total_duration_ms=total_duration_ms+excluded.last_duration_ms,last_duration_ms=excluded.last_duration_ms,updated_at=excluded.updated_at',(job['job_id'],duration,duration,finished)); conn.commit()
                await self._audit(self.config.owner_user_id, 'JOB_RUN_SUCCEEDED', 'run', run_id, {'job_id':job['job_id'],'duration_ms':duration}, trace)
                if result:
                    delivered.append({'chat_id':job['chat_id'], 'text':result, 'run_id':run_id})
            except Exception as exc:
                finished = _now()
                async with self.store._lock:
                    with self.store._conn() as conn:
                        conn.execute("UPDATE cron_runs SET state='failed',finished_at=?,exit_code=1,exception_type=? WHERE run_id=?",(finished,type(exc).__name__,run_id)); conn.commit()
                await self._audit(self.config.owner_user_id, 'JOB_RUN_FAILED', 'run', run_id, {'exception':type(exc).__name__}, trace)
        return delivered

    async def logs(self, job_id: str, limit: int = 10, actor: int | None = None) -> list[dict[str, Any]]:
        if actor is not None:
            await self.require(actor, 'cron.logs')
        async with self.store._lock:
            with self.store._conn() as conn:
                return [dict(r) for r in conn.execute('SELECT * FROM cron_runs WHERE job_id=? ORDER BY created_at DESC LIMIT ?',(job_id,limit)).fetchall()]
