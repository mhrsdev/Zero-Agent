from zero.sqlite_tx import sqlite_txn
from datetime import datetime
from pathlib import Path

import pytest
from zoneinfo import ZoneInfo

from zero.deferred_memory import DeferredMemory
from zero.models import IncomingMessage
from zero.storage import ZeroStore


def msg(text, sender=42, sender_is_bot=False):
    return IncomingMessage(chat_id=-1001, chat_title='test', sender_id=sender, sender_label='@user', text=text, sender_is_bot=sender_is_bot, message_id=10, trace_id='t')


def test_unrelated_message_is_not_a_deferred_candidate(tmp_path: Path):
    memory = DeferredMemory(tmp_path / 'zero.db')
    assert memory.should_process(msg('دقیق‌تر بگو')) is False
    assert memory.should_process(msg('چک کنم')) is False


@pytest.mark.asyncio
async def test_bots_cannot_start_or_continue_deferred_reminders(tmp_path: Path):
    ZeroStore(str(tmp_path / 'zero.db'))
    memory = DeferredMemory(tmp_path / 'zero.db')
    answer, ready = await memory.process(msg('فردا امتحان دارم', sender=8252811591, sender_is_bot=True), FakeRouter('{}'))
    assert answer == '' and ready is None




class FakeRouter:
    def __init__(self, text): self.text = text
    async def complete(self, prompt):
        from types import SimpleNamespace
        return SimpleNamespace(text=self.text)


@pytest.mark.asyncio
async def test_model_schedules_future_time_without_reasking(tmp_path: Path):
    ZeroStore(str(tmp_path / 'zero.db'))
    memory = DeferredMemory(tmp_path / 'zero.db')
    future = (datetime.now(ZoneInfo('Asia/Tehran')) + __import__('datetime').timedelta(days=1)).strftime('%Y-%m-%d 08:40')
    raw = '{"action":"schedule","question":"","reply":"اوکی، حواسم هست.","title":"بیرون رفتن","details":"خروج از منزل","due_local":"' + future + '","confidence":0.99}'
    answer, ready = await memory.process(msg('فردا ساعت ۸:۴۰ باید برم بیرون'), FakeRouter(raw))
    assert ready and ready['ready']['due_at'] > __import__('time').time()
    assert answer == 'اوکی، حواسم هست.'



    ZeroStore(str(tmp_path / 'zero.db'))
    memory = DeferredMemory(tmp_path / 'zero.db')
    ask = await memory.process(msg('فردا باید برم مدرسه'), FakeRouter('{"action":"ask","question":"راستی مدرسه رو برای چه کاری میری؟","reply":"","title":"رفتن به مدرسه","details":"فردا باید برم مدرسه","due_local":null,"confidence":0.93}'))
    assert ask[0] == 'راستی مدرسه رو برای چه کاری میری؟'
    assert ask[1] is None


@pytest.mark.asyncio
async def test_explicit_reminder_is_not_claimed_by_proactive_scheduler(tmp_path: Path):
    import json
    from zero.models import RouteResult
    from zero.proactive_followups import ProactiveFollowups

    db = tmp_path / 'zero.db'
    store = ZeroStore(str(db))
    memory = DeferredMemory(db)
    future = (datetime.now(ZoneInfo('Asia/Tehran')) + __import__('datetime').timedelta(days=1)).strftime('%Y-%m-%d 08:00')
    _, wrapped = await memory.process(
        msg('فردا ساعت ۸ یادم بنداز'),
        FakeRouter('{"action":"schedule","question":"","reply":"باشه.","title":"یادآوری صریح","details":"یادآوری","due_local":"'+future+'","confidence":0.99}'),
    )
    job_id = memory.create_reminder_job(wrapped['ready'], owner_id=1)

    class Router:
        async def complete(self, *args, **kwargs):
            return RouteResult(json.dumps({'version':1,'action':'postpone','confidence':.9,'postpone_hours':2,'reason_code':'x'}),'x','x',1)

    assert await ProactiveFollowups(store, Router()).tick('test') == []
    with sqlite_txn(store._conn()) as conn:
        assert conn.execute('SELECT state FROM cron_jobs WHERE job_id=?',(job_id,)).fetchone()[0] == 'enabled'
