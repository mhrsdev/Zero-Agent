import asyncio
from pathlib import Path

import pytest

from zero.config import ZeroConfig
from zero.brain import ZeroBrain
from zero.models import RouteResult
from zero.storage import ZeroStore
from zero.template_jobs import JobSecurityError, TemplateJobService, _next_run, parse_natural_job


def service(tmp_path: Path):
    config = ZeroConfig.load('/root/zero/config/zero.example.yaml')
    config = config.model_copy(update={'owner_user_id': 1, 'memory': config.memory.model_copy(update={'db_path': str(tmp_path / 'jobs.db')})})
    return TemplateJobService(ZeroStore(config.memory.db_path), config)


def test_next_run_honors_persisted_asia_tehran_timezone_not_host_timezone():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = int(datetime(2026, 7, 10, 6, 0, tzinfo=ZoneInfo('UTC')).timestamp())
    next_run = _next_run({'kind': 'daily', 'hour': 8, 'minute': 0, 'timezone': 'Asia/Tehran'}, now)
    assert datetime.fromtimestamp(next_run, ZoneInfo('Asia/Tehran')).strftime('%H:%M') == '08:00'


    cases = [
        ('هر روز ساعت ۸ اخبار AI را بگو', 'ai_news', {'kind': 'daily', 'hour': 8}),
        ('هر شب ساعت ۲۲ خلاصه گروه را بفرست', 'group_summary', {'kind': 'daily', 'hour': 22}),
        ('هر ۶ ساعت اخبار مهم جنگ‌های جهان اوکراین غزه را بگو', 'war_news', {'kind': 'interval', 'seconds': 21600}),
        ('هر ۱۲ ساعت ترندهای گیت‌هاب را بررسی کن', 'github_trending_digest', {'kind': 'interval', 'seconds': 43200}),
        ('هر جمعه ساعت ۹ خلاصه هفته را بفرست', 'weekly_summary', {'kind': 'weekly', 'weekday': 4}),
        ('هر روز ساعت ۹ قیمت اتریوم را بررسی کن', 'crypto_price', {'kind': 'daily', 'hour': 9}),
        ('هر ماه اول ماه ساعت ۹ خلاصه آمار گروه را بده', 'social_stats', {'kind': 'monthly', 'day': 1}),
        ('هر دو ساعت آب خوردن را یادآوری کن', 'reminder', {'kind': 'interval', 'seconds': 7200}),
    ]
    for request, template, expected in cases:
        actual, _, schedule, _ = parse_natural_job(request)
        assert actual == template
        assert all(schedule[key] == value for key, value in expected.items())
        assert schedule['timezone'] == 'Asia/Tehran'


def test_ambiguous_time_period_asks_a_question_instead_of_defaulting():
    with pytest.raises(JobSecurityError, match='چه ساعتی'):
        parse_natural_job('هر شب خلاصه گروه را بفرست')


    template, inputs, schedule, _ = parse_natural_job('هر ۶ ساعت قیمت بیت کوین را بررسی کن')
    assert template == 'crypto_price'
    assert inputs == {'asset': 'BTC'}
    assert schedule['seconds'] == 21600
    with pytest.raises(JobSecurityError, match='Runner'):
        parse_natural_job('هر روز یک Python script اجرا کن')


def test_group_summary_uses_semantic_builder_for_last_24_hours(monkeypatch, tmp_path):
    async def scenario():
        captured = {}

        async def semantic_builder(chat_id: int, *, since_ts: int):
            captured.update(chat_id=chat_id, since_ts=since_ts)
            return 'بحث اصلی درباره ساخت یک شکل ASCII بود و در پایان نتیجه تأیید شد.'

        monkeypatch.setattr('zero.template_jobs._now', lambda: 200_000)
        jobs = TemplateJobService(
            ZeroStore(str(tmp_path / 'summary.db')),
            ZeroConfig.load('/root/zero/config/zero.example.yaml'),
            summary_builder=semantic_builder,
        )
        out = await jobs._execute_template({
            'template_id': 'group_summary',
            'input_json': '{}',
            'chat_id': -100,
        })

        assert captured == {'chat_id': -100, 'since_ts': 200_000 - 86_400}
        assert 'بحث اصلی درباره ساخت یک شکل ASCII بود' in out
        assert '/O.O\\' not in out
        assert ' | ' not in out

    asyncio.run(scenario())


def test_group_summary_does_not_cut_semantic_output_at_1000_chars(tmp_path):
    semantic = 'خلاصه معنایی: ' + ('الف' * 1200)

    async def builder(chat_id: int, *, since_ts: int):
        return semantic

    async def scenario():
        jobs = TemplateJobService(
            ZeroStore(str(tmp_path / 'long-summary.db')),
            ZeroConfig.load('/root/zero/config/zero.example.yaml'),
            summary_builder=builder,
        )
        out = await jobs._execute_template({'template_id': 'group_summary', 'input_json': '{}', 'chat_id': -100})

        assert out.endswith('الف' * 20)
        assert len(out) > 1000

    asyncio.run(scenario())


def test_group_summary_provider_failure_never_dumps_raw_messages(tmp_path):
    async def failing_builder(chat_id: int, *, since_ts: int):
        raise RuntimeError('provider unavailable')

    async def scenario():
        jobs = TemplateJobService(
            ZeroStore(str(tmp_path / 'failed-summary.db')),
            ZeroConfig.load('/root/zero/config/zero.example.yaml'),
            summary_builder=failing_builder,
        )
        out = await jobs._execute_template({
            'template_id': 'group_summary',
            'input_json': '{}',
            'chat_id': -100,
        })

        assert out == '📋 خلاصه گروه: خلاصه‌ساز فعلاً در دسترس نیست؛ پیام‌های خام ارسال نشدند.'
        assert 'provider unavailable' not in out

    asyncio.run(scenario())


def test_recent_since_keeps_human_and_bot_messages_inside_window(tmp_path):
    async def scenario():
        store = ZeroStore(str(tmp_path / 'window.db'), recent_messages_limit=20)
        async with store._lock:
            with store._conn() as conn:
                conn.executemany(
                    'INSERT INTO recent_messages(chat_id,sender_id,sender_label,role,text,created_at) VALUES(?,?,?,?,?,?)',
                    [
                        (-100, 1, '@old', 'user', 'پیام قدیمی', 100),
                        (-100, 2, '@human', 'user', 'پیام انسان', 200),
                        (-100, 3, '@SomeBot', 'user', 'پیام کامل بات', 201),
                    ],
                )
                conn.commit()

        rows = await store.get_recent_since(-100, since_ts=150, limit=20)

        assert [row['sender_label'] for row in rows] == ['@human', '@SomeBot']
        assert [row['text'] for row in rows] == ['پیام انسان', 'پیام کامل بات']

    asyncio.run(scenario())


def test_daily_summary_prompt_uses_window_and_includes_bot_messages(tmp_path):
    class SummaryRouter:
        keys = []

        def __init__(self):
            self.prompt = ''

        async def complete(self, prompt: str, **kwargs):
            self.prompt = prompt
            return RouteResult(text='خلاصه معنایی ساخته شد.', provider='test', model='test', attempts=1)

    async def scenario():
        config = ZeroConfig.load('/root/zero/config/zero.example.yaml')
        config = config.model_copy(update={'memory': config.memory.model_copy(update={'db_path': str(tmp_path / 'brain-summary.db')})})
        store = ZeroStore(config.memory.db_path, recent_messages_limit=20)
        async with store._lock:
            with store._conn() as conn:
                conn.executemany(
                    'INSERT INTO recent_messages(chat_id,sender_id,sender_label,role,text,created_at) VALUES(?,?,?,?,?,?)',
                    [
                        (-100, 1, '@old', 'user', 'پیام قدیمی نباید وارد شود', 100),
                        (-100, 2, '@human', 'user', 'بحث انسانی مهم', 200),
                        (-100, 3, '@MyNovaChatBot', 'user', 'بحث کامل بات', 201),
                    ],
                )
                conn.commit()
        router = SummaryRouter()
        brain = ZeroBrain(config, store, router)

        result = await brain.build_daily_summary(-100, since_ts=150)

        assert result == 'خلاصه معنایی ساخته شد.'
        assert 'بحث انسانی مهم' in router.prompt
        assert 'بحث کامل بات' in router.prompt
        assert 'پیام قدیمی نباید وارد شود' not in router.prompt
        assert 'پیام‌ها را پشت سر هم کپی نکن' in router.prompt
        assert 'ASCII' in router.prompt

    asyncio.run(scenario())


def test_simulation_then_owner_approval_and_real_safe_reminder_run(tmp_path: Path):
    async def scenario():
        jobs = service(tmp_path)
        draft = await jobs.create_draft(1, -100, 'یادآور تست', 'reminder', {'text': 'آب بخور'}, {'kind': 'interval', 'seconds': 60, 'explanation': 'هر دقیقه'})
        assert draft['risk'] == 'low' and draft['host_access'] == 'none'
        assert (await jobs.status(draft['job_id']))['state'] == 'draft'
        await jobs.approve(1, draft['job_id'])
        job = await jobs.status(draft['job_id'])
        # Force only the persisted trusted template due; no shell/code is involved.
        async with jobs.store._lock:
            with jobs.store._conn() as conn:
                conn.execute('UPDATE cron_jobs SET next_run_at=? WHERE job_id=?', (1, draft['job_id']))
                conn.commit()
        delivered = await jobs.run_due(now=2)
        assert delivered and delivered[0]['text'] == '⏰ یادآوری: آب بخور'
        state = await jobs.status(draft['job_id'])
        assert state['metrics']['success_count'] == 1
        assert (await jobs.logs(draft['job_id']))[0]['state'] == 'succeeded'
    asyncio.run(scenario())


def test_owner_identity_and_cron_admin_boundary(tmp_path: Path):
    async def scenario():
        jobs = service(tmp_path)
        await jobs.grant_cron_admin(1, 22, True)
        assert await jobs.role_for(22) == 'cron_admin'
        with pytest.raises(JobSecurityError):
            await jobs.grant_cron_admin(22, 33, True)
        with pytest.raises(JobSecurityError):
            await jobs.grant_cron_admin(22, 1, True)
        assert await jobs.role_for(1) == 'owner'
    asyncio.run(scenario())


def test_medium_risk_cannot_be_approved_by_normal_user_and_persists(tmp_path: Path):
    async def scenario():
        jobs = service(tmp_path)
        await jobs.grant_cron_admin(1, 22, True)
        draft = await jobs.create_draft(22, -100, 'هوا', 'weather', {'city': 'Tehran'}, {'kind': 'daily', 'hour': 8, 'minute': 0, 'explanation': 'روزانه'})
        with pytest.raises(JobSecurityError):
            await jobs.approve(33, draft['job_id'])
        await jobs.approve(22, draft['job_id'])
        assert (await jobs.status(draft['job_id']))['state'] == 'enabled'
        assert len(await jobs.list_jobs()) == 1
        with pytest.raises(JobSecurityError):
            await jobs.list_jobs(33)
    asyncio.run(scenario())


def test_disabled_critical_template_cannot_be_created(tmp_path: Path):
    async def scenario():
        jobs = service(tmp_path)
        with pytest.raises(JobSecurityError, match='Host log rotation'):
            await jobs.create_draft(1, -100, 'log', 'log_rotation', {}, {'kind': 'interval', 'seconds': 60})
    asyncio.run(scenario())
