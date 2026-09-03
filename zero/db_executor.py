"""Dedicated sqlite execution thread and the store mutex that guards it.

Why this exists
---------------
``ZeroStore`` exposes ~150 ``async def`` methods whose bodies are synchronous
``sqlite3`` work. Running them on the event loop thread stops the loop for the
duration of every call, and when a background thread holds sqlite's write lock
the loop-side call waits out ``busy_timeout`` (5 s) with heartbeats, Telegram
reads and timers all frozen. Both problems are removed by moving the sqlite
work onto one dedicated thread that owns one long-lived connection.

Design constraints this module has to satisfy
--------------------------------------------
* ``ZeroStore._conn()`` must keep returning a fresh, caller-owned connection:
  ~30 modules and ~90 test files call ``sqlite_txn(store._conn())``, and
  ``sqlite_txn`` closes what it is given. The long-lived connection is therefore
  private to the worker thread and never handed out.
* A sqlite3 connection may only be used by the thread that created it
  (``check_same_thread``), so the worker creates its own connection on its own
  thread and no other thread ever touches it.
* One worker thread, not a pool: ``store._lock`` is public API that
  ``knowledge.py``, ``template_jobs.py``, ``social_plus.py`` and
  ``stickers/library.py`` hold around their own sqlite work, so store
  operations are serialized regardless. A single thread reproduces the exact
  read-modify-write atomicity ``_lock`` gives today (``try_reserve_rate_event``,
  ``reserve_incoming_message``, ``consume_telegram_search_limit``, ...) instead
  of trading it for read parallelism the workload cannot use.
* Not ``asyncio.to_thread``: that shares the default executor with unrelated
  blocking work (~6 workers on a 2-core VPS) and gives no ordering guarantee.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import sqlite3
import threading
from collections import deque
from concurrent.futures import Future
from typing import Any, Callable

logger = logging.getLogger('zero.db_executor')

_SHUTDOWN = object()


class AsyncMutex:
    """FIFO async mutex that is not bound to one event loop and whose
    ``release`` is thread-safe.

    ``asyncio.Lock`` cannot be used here for two reasons that are properties of
    this codebase, not preferences: the store is constructed synchronously and
    reused across several ``asyncio.run`` calls in the test suite, and ownership
    is handed back from the sqlite worker thread once the operation it guards
    has actually finished (so a cancelled caller can never let a third party
    interleave with an in-flight statement).
    """

    __slots__ = ('_guard', '_held', '_waiters')

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._held = False
        self._waiters: deque[tuple[asyncio.AbstractEventLoop, asyncio.Future]] = deque()

    def locked(self) -> bool:
        return self._held

    async def acquire(self) -> bool:
        loop = asyncio.get_running_loop()
        with self._guard:
            if not self._held:
                self._held = True
                return True
            waiter = loop.create_future()
            entry = (loop, waiter)
            self._waiters.append(entry)
        try:
            await waiter
        except BaseException:
            with self._guard:
                if waiter.done() and not waiter.cancelled():
                    # Ownership arrived before the cancellation landed; hand it
                    # on instead of dropping the mutex forever.
                    self._grant_locked()
                else:
                    try:
                        self._waiters.remove(entry)
                    except ValueError:
                        pass
            raise
        return True

    def release(self) -> None:
        with self._guard:
            if not self._held:
                raise RuntimeError('store mutex is not held')
            self._grant_locked()

    def _grant_locked(self) -> None:
        """Transfer ownership directly to the next live waiter. Caller holds the guard."""
        while self._waiters:
            loop, waiter = self._waiters.popleft()
            if waiter.cancelled():
                continue
            try:
                loop.call_soon_threadsafe(self._handoff, waiter)
            except RuntimeError:
                # Waiter's loop is already closed; its task can never resume.
                continue
            return
        self._held = False

    def _handoff(self, waiter: asyncio.Future) -> None:
        if waiter.cancelled():
            with self._guard:
                self._grant_locked()
            return
        waiter.set_result(True)

    async def __aenter__(self) -> 'AsyncMutex':
        await self.acquire()
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        self.release()
        return False


class SqliteWorker:
    """One thread, one long-lived connection, FIFO queue of ``fn(conn)`` jobs.

    The thread starts on the first submitted job so a store that is only
    constructed (permission tests, migration-only use) holds no extra file
    handle, and it is stopped by ``close()`` or by the owner's finalizer.
    """

    def __init__(self, connect: Callable[[], sqlite3.Connection], *, name: str = 'zero-sqlite') -> None:
        self._connect = connect
        self._name = name
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._guard = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self.jobs_run = 0
        self.connections_opened = 0

    # ------------------------------------------------------------ submission

    def submit(self, fn: Callable[[sqlite3.Connection], Any]) -> Future:
        future: Future = Future()
        with self._guard:
            if self._closed:
                future.set_exception(RuntimeError('sqlite worker is closed'))
                return future
            if self._thread is None:
                self._thread = threading.Thread(target=self._serve, name=self._name, daemon=True)
                self._thread.start()
            self._queue.put((fn, future))
        return future

    def run(self, fn: Callable[[sqlite3.Connection], Any], timeout: float | None = None) -> Any:
        """Blocking submit, for synchronous callers on a non-worker thread."""
        if self._thread is threading.current_thread():
            raise RuntimeError('cannot re-enter the sqlite worker from itself')
        return self.submit(fn).result(timeout)

    def close(self, timeout: float = 15.0) -> None:
        with self._guard:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
            self._thread = None
        if thread is None:
            return
        self._queue.put(_SHUTDOWN)
        if thread is not threading.current_thread():
            thread.join(timeout)

    @property
    def started(self) -> bool:
        return self._thread is not None

    # ---------------------------------------------------------------- worker

    def _serve(self) -> None:
        conn: sqlite3.Connection | None = None
        try:
            while True:
                item = self._queue.get()
                if item is _SHUTDOWN:
                    return
                fn, future = item
                if not future.set_running_or_notify_cancel():
                    continue  # caller abandoned the call before it started
                try:
                    if conn is None:
                        conn = self._connect()
                        self.connections_opened += 1
                    # `with conn` is the exact commit-on-success /
                    # rollback-on-error scope every method body had via
                    # _session_ctx; the connection itself must survive.
                    with conn:
                        result = fn(conn)
                except BaseException as exc:  # noqa: BLE001 - relayed to the caller
                    conn = self._recycle(conn)
                    future.set_exception(exc)
                else:
                    future.set_result(result)
                finally:
                    self.jobs_run += 1
        finally:
            if conn is not None:
                _close_quietly(conn)

    def _recycle(self, conn: sqlite3.Connection | None) -> sqlite3.Connection | None:
        """Keep the connection only while it is still usable."""
        if conn is None:
            return None
        try:
            conn.execute('SELECT 1').fetchone()
        except Exception:
            _close_quietly(conn)
            return None
        return conn


def _close_quietly(conn: sqlite3.Connection) -> None:
    try:
        conn.close()
    except Exception as exc:
        logger.warning('STORE_WORKER_CLOSE_FAILED exception_type=%s', type(exc).__name__)


async def submit_awaitable(worker: SqliteWorker, mutex: AsyncMutex, fn: Callable[[sqlite3.Connection], Any]) -> Any:
    """Run *fn(conn)* on the worker while holding *mutex* until it completes.

    The mutex is released from the job's completion callback, not from the
    awaiting coroutine, so cancelling the caller cannot expose a half-finished
    statement to whoever holds ``store._lock`` next.
    """
    await mutex.acquire()
    try:
        future = worker.submit(fn)
    except BaseException:
        mutex.release()
        raise
    future.add_done_callback(lambda _f: mutex.release())
    return await asyncio.wrap_future(future)
