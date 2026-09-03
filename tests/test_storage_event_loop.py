"""The sqlite storage layer must not stop the event loop.

Every ``ZeroStore`` method used to run synchronous ``sqlite3`` work on the loop
thread, so a store call froze heartbeats, timers and Telegram reads for its
whole duration -- up to ``busy_timeout`` (5 s) whenever a background thread held
sqlite's write lock. These tests pin the properties that fix has to provide and
the ones it must not trade away.

RED before the fix (measured on 82174d1):
* ``test_event_loop_stays_responsive_during_a_burst_of_store_calls``: heartbeat
  gap 2448 ms, threshold 250 ms.
* ``test_event_loop_stays_responsive_while_a_thread_holds_the_write_lock``:
  heartbeat gap 5480 ms, threshold 400 ms.
* ``test_store_calls_reuse_one_connection``: 120 connections for 60 calls,
  threshold 3.
"""
from __future__ import annotations

import asyncio
import gc
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from zero.db_executor import AsyncMutex
from zero.storage import ZeroStore


class Heartbeat:
    """Periodic task that records how long the loop was unavailable to it."""

    def __init__(self, interval: float = 0.005):
        self.interval = interval
        self.gaps: list[float] = []
        self._stop = False
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        last = loop.time()
        while not self._stop:
            await asyncio.sleep(self.interval)
            now = loop.time()
            self.gaps.append(max(0.0, now - last - self.interval))
            last = now

    async def __aenter__(self) -> 'Heartbeat':
        self._task = asyncio.create_task(self._run())
        await asyncio.sleep(0.02)
        return self

    async def __aexit__(self, *exc_info) -> bool:
        self._stop = True
        if self._task is not None:
            await self._task
        return False

    @property
    def max_gap(self) -> float:
        return max(self.gaps) if self.gaps else 0.0


class TrackingConn(sqlite3.Connection):
    """Counts real connection opens and closes (the pattern used by
    tests/test_sqlite_tx_contract.py)."""

    opened = 0
    closed = 0

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        type(self).opened += 1

    def close(self):
        type(self).closed += 1
        super().close()

    @classmethod
    def reset(cls) -> None:
        cls.opened = 0
        cls.closed = 0


class TrackingStore(ZeroStore):
    """Same connection setup as production, with the opens counted."""

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, factory=TrackingConn)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=5000')
        return conn


def _store(tmp_path: Path, name: str = 'zero.db') -> ZeroStore:
    return ZeroStore(str(tmp_path / name))


# ------------------------------------------------------------ loop responsiveness

async def test_event_loop_stays_responsive_during_a_burst_of_store_calls(tmp_path: Path):
    store = _store(tmp_path)
    try:
        async with Heartbeat() as beat:
            for i in range(200):
                await store.set_setting(f'burst_{i % 10}', str(i))
                await store.get_setting(f'burst_{i % 10}')
        assert beat.max_gap < 0.25, f'loop blocked for {beat.max_gap * 1000:.0f} ms'
    finally:
        await store.aclose()


async def test_event_loop_stays_responsive_while_a_thread_holds_the_write_lock(tmp_path: Path):
    """The shape of proactive_scheduler.claim_due: a worker thread holds
    BEGIN IMMEDIATE while the message path writes. The loop must keep running."""
    store = _store(tmp_path)
    holding = threading.Event()
    hold_seconds = 1.0
    failure: list[str] = []

    def writer() -> None:
        conn = store._conn()
        try:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('writer','1')")
            holding.set()
            time.sleep(hold_seconds)
            conn.commit()
        except Exception as exc:  # pragma: no cover - surfaced by the assertion
            failure.append(f'{type(exc).__name__}: {exc}')
            holding.set()
        finally:
            conn.close()

    thread = threading.Thread(target=writer, name='test-write-lock-holder')
    try:
        async with Heartbeat() as beat:
            thread.start()
            await asyncio.to_thread(holding.wait, 10)
            await store.set_setting('loop_side', '1')
            await asyncio.sleep(0.02)
        thread.join(20)
        assert failure == []
        assert await store.get_setting('loop_side') == '1'
        assert beat.max_gap < 0.4, f'loop blocked for {beat.max_gap * 1000:.0f} ms'
    finally:
        thread.join(20)
        await store.aclose()


# ------------------------------------------------------------ connection lifecycle

async def test_store_calls_reuse_one_connection(tmp_path: Path):
    store = TrackingStore(str(tmp_path / 'reuse.db'))
    try:
        await store.set_setting('warm', '1')  # starts the worker
        TrackingConn.reset()
        for i in range(60):
            await store.set_setting(f'reuse_{i}', str(i))
        assert TrackingConn.opened <= 3, f'{TrackingConn.opened} connections for 60 calls'
    finally:
        await store.aclose()


async def test_closing_the_store_closes_every_connection_it_opened(tmp_path: Path):
    store = TrackingStore(str(tmp_path / 'leak.db'))
    TrackingConn.reset()
    for i in range(25):
        await store.set_setting(f'leak_{i}', str(i))
    await store.aclose()
    assert TrackingConn.opened == TrackingConn.closed, (
        f'{TrackingConn.opened - TrackingConn.closed} connection(s) leaked'
    )


def test_store_releases_thread_and_file_handle_when_collected(tmp_path: Path):
    """No explicit shutdown: dropping the store must stop its thread and free
    the database file (unlink is what proves it on Windows)."""
    db = tmp_path / 'collected.db'
    store = ZeroStore(str(db))
    asyncio.run(store.set_setting('k', 'v'))
    name = f'zero-store-{db.name}'
    assert any(t.name == name for t in threading.enumerate())
    del store
    gc.collect()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(t.name == name for t in threading.enumerate()):
        time.sleep(0.02)
    assert not any(t.name == name for t in threading.enumerate())
    db.unlink()


# ------------------------------------------------------- preserved sqlite semantics

async def test_reused_connection_keeps_wal_foreign_keys_and_busy_timeout(tmp_path: Path):
    """A long-lived connection must still carry the per-connection settings the
    old per-call connection set: they are not defaults."""
    store = _store(tmp_path, 'pragmas.db')

    def probe(conn):
        return (
            str(conn.execute('PRAGMA journal_mode').fetchone()[0]).lower(),
            int(conn.execute('PRAGMA foreign_keys').fetchone()[0]),
            int(conn.execute('PRAGMA busy_timeout').fetchone()[0]),
        )

    try:
        await store.set_setting('warm', '1')
        assert await store._exec(probe) == ('wal', 1, 5000)
        assert await store._exec(probe) == ('wal', 1, 5000)  # still true on reuse
    finally:
        await store.aclose()


async def test_failed_operation_rolls_back_and_leaves_the_connection_usable(tmp_path: Path):
    store = _store(tmp_path, 'rollback.db')

    def boom(conn):
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('doomed','1')")
        raise RuntimeError('boom')

    try:
        await store.set_setting('kept', '1')
        with pytest.raises(RuntimeError, match='boom'):
            await store._exec(boom)
        assert await store.get_setting('doomed') is None
        assert await store.get_setting('kept') == '1'
    finally:
        await store.aclose()


async def test_explicit_begin_immediate_still_works_on_the_reused_connection(tmp_path: Path):
    """commit_group_context issues its own BEGIN IMMEDIATE/COMMIT; a connection
    left mid-transaction by an earlier call would break it."""
    store = _store(tmp_path, 'immediate.db')
    rows = [{'telegram_message_id': 11, 'created_at': 1000}]
    try:
        assert await store.commit_group_context(-500, rows, {'topic': 'a'}, 0) is True
        assert await store.commit_group_context(-500, [{'telegram_message_id': 12, 'created_at': 1001}], None, 1) is True
        assert await store.commit_group_context(-500, [{'telegram_message_id': 13, 'created_at': 1002}], None, 0) is False
    finally:
        await store.aclose()


# ------------------------------------------------------------ serialization guards

async def test_concurrent_rate_reservations_never_exceed_the_limit(tmp_path: Path):
    """Read-then-write atomicity: `_lock` is what makes this exact, sqlite alone
    does not. Moving the work to a thread must not turn it into a pool."""
    store = _store(tmp_path, 'reserve.db')
    try:
        results = await asyncio.gather(*[
            store.try_reserve_rate_event(7, 'message', 60, 3, chat_id=-42) for _ in range(12)
        ])
        assert sum(1 for granted, _ in results if granted) == 3
        assert await store.count_rate_events(7, 'message', 60, -42) == 3
    finally:
        await store.aclose()


async def test_external_lock_holder_excludes_store_operations(tmp_path: Path):
    """knowledge.py, template_jobs.py, social_plus.py and stickers/library.py all
    hold `store._lock` around their own sqlite work."""
    store = _store(tmp_path, 'external.db')
    order: list[str] = []

    async def holder():
        async with store._lock:
            order.append('holder-enter')
            await asyncio.sleep(0.05)
            order.append('holder-exit')

    async def operation():
        await asyncio.sleep(0.01)
        await store.set_setting('x', '1')
        order.append('store-op')

    try:
        await asyncio.gather(holder(), operation())
        assert order == ['holder-enter', 'holder-exit', 'store-op']
    finally:
        await store.aclose()


def test_store_is_usable_from_more_than_one_event_loop(tmp_path: Path):
    """The store is built synchronously and several tests drive one instance from
    successive asyncio.run calls, so the mutex must not bind to a single loop."""
    store = ZeroStore(str(tmp_path / 'loops.db'))
    try:
        async def contended():
            await asyncio.gather(*[store.set_setting(f'loop_{i}', str(i)) for i in range(8)])
        asyncio.run(contended())

        async def read_back():
            return await store.get_setting('loop_0')
        assert asyncio.run(read_back()) == '0'
    finally:
        store.close()


# ---------------------------------------------------------------------- AsyncMutex

async def test_mutex_grants_in_arrival_order():
    mutex = AsyncMutex()
    order: list[int] = []

    async def worker(n: int):
        async with mutex:
            order.append(n)
            await asyncio.sleep(0.005)

    await mutex.acquire()
    tasks = [asyncio.create_task(worker(n)) for n in range(5)]
    await asyncio.sleep(0.02)
    mutex.release()
    await asyncio.gather(*tasks)
    assert order == [0, 1, 2, 3, 4]
    assert not mutex.locked()


async def test_mutex_can_be_released_from_another_thread():
    """The sqlite worker hands ownership back from its own thread."""
    mutex = AsyncMutex()
    await mutex.acquire()
    handed = asyncio.Event()
    loop = asyncio.get_running_loop()

    async def waiter():
        async with mutex:
            loop.call_soon_threadsafe(handed.set)

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.02)
    threading.Thread(target=mutex.release, name='test-mutex-releaser').start()
    await asyncio.wait_for(handed.wait(), 5)
    await task
    assert not mutex.locked()


async def test_mutex_cancelled_waiter_does_not_strand_ownership():
    mutex = AsyncMutex()
    await mutex.acquire()
    first = asyncio.create_task(mutex.acquire())
    second = asyncio.create_task(mutex.acquire())
    await asyncio.sleep(0.02)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    mutex.release()
    await asyncio.wait_for(second, 5)
    assert mutex.locked()
    mutex.release()
    assert not mutex.locked()
