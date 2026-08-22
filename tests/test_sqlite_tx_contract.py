"""Contract tests for zero.sqlite_tx.sqlite_txn (v2, savepoint nesting).

Adversarial by design: several tests FAIL on the pre-fix behaviour
(``with conn:`` never closes; v1 nested scopes closed the shared connection;
v1 inner commits survived outer rollback). They pin the required semantics:

* outermost: commit on success, rollback on error, original exception
  preserved even when close fails, connection unusable after the block
* nested: SAVEPOINT -- inner failure rolls back only the inner writes;
  outer failure after inner success rolls back the inner data too (atomic)
* thread-local registry: concurrent transactions cannot interfere
* no state leak: the depth registry is empty after every stress run
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from zero import sqlite_tx
from zero.sqlite_tx import active_depth, sqlite_txn


def _db(tmp_path: Path) -> Path:
    p = tmp_path / "t.db"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE t(x INTEGER)")
    conn.commit()
    conn.close()
    return p


def _count(p: Path) -> int:
    with sqlite_txn(sqlite3.connect(p)) as c:
        return c.execute("SELECT count(*) FROM t").fetchone()[0]


class TrackingConn(sqlite3.Connection):
    close_calls = 0
    open_count = 0

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        TrackingConn.open_count += 1

    def close(self):
        TrackingConn.close_calls += 1
        super().close()


def _track(p: Path) -> sqlite3.Connection:
    return sqlite3.connect(p, factory=TrackingConn)


# ---------------------------------------------------------- outermost scope

def test_commit_on_success(tmp_path: Path):
    p = _db(tmp_path)
    with sqlite_txn(sqlite3.connect(p)) as c:
        c.execute("INSERT INTO t VALUES (1)")
    assert _count(p) == 1


def test_rollback_on_error_and_original_exception_preserved(tmp_path: Path):
    p = _db(tmp_path)
    conn = sqlite3.connect(p)
    with pytest.raises(ValueError, match="boom"):
        with sqlite_txn(conn) as c:
            c.execute("INSERT INTO t VALUES (1)")
            raise ValueError("boom")
    assert _count(p) == 0


def test_connection_closed_exactly_once(tmp_path: Path):
    p = _db(tmp_path)
    before = TrackingConn.close_calls
    with sqlite_txn(_track(p)) as c:
        c.execute("INSERT INTO t VALUES (1)")
    assert TrackingConn.close_calls - before == 1


def test_close_failure_does_not_mask_original_error(tmp_path: Path):
    class BrokenCloseConn(sqlite3.Connection):
        def close(self):
            raise RuntimeError("close failed")

    p = _db(tmp_path)
    conn = sqlite3.connect(p, factory=BrokenCloseConn)
    with pytest.raises(ValueError, match="boom"):
        with sqlite_txn(conn) as c:
            c.execute("INSERT INTO t VALUES (1)")
            raise ValueError("boom")


def test_close_failure_on_success_surfaces(tmp_path: Path):
    class BrokenCloseConn(sqlite3.Connection):
        def close(self):
            raise RuntimeError("close failed")

    p = _db(tmp_path)
    conn = sqlite3.connect(p, factory=BrokenCloseConn)
    with pytest.raises(RuntimeError, match="close failed"):
        with sqlite_txn(conn) as c:
            c.execute("INSERT INTO t VALUES (1)")


def test_no_use_after_close_fails_on_leaking_version(tmp_path: Path):
    """On the pre-fix version the connection stayed open and this passed silently;
    with sqlite_txn the post-block use must raise ProgrammingError."""
    p = _db(tmp_path)
    conn = sqlite3.connect(p)
    with sqlite_txn(conn) as c:
        c.execute("INSERT INTO t VALUES (1)")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("INSERT INTO t VALUES (2)")


def test_commit_failure_preserved_and_closed(tmp_path: Path):
    """Force a real COMMIT-time failure via deferred foreign keys (the C-level
    ``with conn`` exit bypasses Python-level commit overrides)."""
    p = tmp_path / "t.db"
    setup = sqlite3.connect(p)
    setup.execute("PRAGMA foreign_keys=ON")
    setup.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
    setup.execute(
        "CREATE TABLE child(id INTEGER PRIMARY KEY, pid INTEGER REFERENCES parent(id))"
    )
    setup.commit()
    setup.close()

    conn = sqlite3.connect(p)
    conn.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite_txn(conn) as c:
            c.execute("PRAGMA defer_foreign_keys=ON")
            c.execute("INSERT INTO child VALUES (1, 999)")  # violates FK at COMMIT
    # Connection was still closed despite the commit failure.
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


# ---------------------------------------------------------- nested / savepoint

def test_nested_atomic_outer_error_rolls_back_inner_writes(tmp_path: Path):
    """THE critical scenario: outer opens, inner writes and succeeds, outer
    fails -> the inner data MUST be rolled back too (atomic semantics)."""
    p = _db(tmp_path)
    conn = sqlite3.connect(p)
    with pytest.raises(ValueError, match="outer"):
        with sqlite_txn(conn):
            conn.execute("INSERT INTO t VALUES (1)")
            with sqlite_txn(conn) as inner:
                inner.execute("INSERT INTO t VALUES (2)")
            raise ValueError("outer")
    assert _count(p) == 0


def test_nested_inner_error_rolls_back_only_inner(tmp_path: Path):
    p = _db(tmp_path)
    conn = sqlite3.connect(p)
    with sqlite_txn(conn) as outer:
        outer.execute("INSERT INTO t VALUES (1)")
        with pytest.raises(ValueError, match="inner"):
            with sqlite_txn(conn) as inner:
                inner.execute("INSERT INTO t VALUES (2)")
                raise ValueError("inner")
        outer.execute("INSERT INTO t VALUES (3)")
    assert _count(p) == 2  # rows 1 and 3 committed; row 2 rolled back


def test_three_level_nesting_atomic(tmp_path: Path):
    p = _db(tmp_path)
    conn = sqlite3.connect(p)
    with pytest.raises(ValueError, match="top"):
        with sqlite_txn(conn):
            conn.execute("INSERT INTO t VALUES (1)")
            with sqlite_txn(conn):
                conn.execute("INSERT INTO t VALUES (2)")
                with sqlite_txn(conn) as third:
                    third.execute("INSERT INTO t VALUES (3)")
                raise ValueError("top")
    assert _count(p) == 0


def test_nested_success_commits_with_outer(tmp_path: Path):
    p = _db(tmp_path)
    conn = sqlite3.connect(p)
    with sqlite_txn(conn):
        conn.execute("INSERT INTO t VALUES (1)")
        with sqlite_txn(conn) as inner:
            inner.execute("INSERT INTO t VALUES (2)")
    assert _count(p) == 2


def test_nested_depth_tracking_restored(tmp_path: Path):
    p = _db(tmp_path)
    conn = sqlite3.connect(p)
    with sqlite_txn(conn):
        assert active_depth(conn) == 1
        with sqlite_txn(conn):
            assert active_depth(conn) == 2
        assert active_depth(conn) == 1
    assert active_depth(conn) == 0


# ---------------------------------------------------------- threading & stress

def test_concurrent_writers_both_succeed(tmp_path: Path):
    p = _db(tmp_path)
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            conn = sqlite3.connect(p, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            with sqlite_txn(conn) as c:
                c.execute("INSERT INTO t VALUES (?)", (n,))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert _count(p) == 8


def test_thread_local_registry_isolation(tmp_path: Path):
    """A depth entry on one thread must not leak into another thread."""
    p = _db(tmp_path)
    conn = sqlite3.connect(p)
    seen_in_thread: list[int] = []

    def probe():
        seen_in_thread.append(active_depth(conn))

    with sqlite_txn(conn):
        t = threading.Thread(target=probe)
        t.start()
        t.join()
    assert seen_in_thread == [0]


def test_stress_thousands_of_transactions_no_state_leak(tmp_path: Path):
    p = _db(tmp_path)
    TrackingConn.open_count = 0
    TrackingConn.close_calls = 0
    for i in range(3000):
        with sqlite_txn(_track(p)) as c:
            c.execute("INSERT INTO t VALUES (?)", (i,))
    assert TrackingConn.open_count == 3000
    assert TrackingConn.close_calls == 3000
    assert TrackingConn.open_count - TrackingConn.close_calls == 0
    # No leaked registry entries on this thread.
    assert sqlite_tx._depths() == {}


def test_stress_nested_mixed_threads(tmp_path: Path):
    p = _db(tmp_path)
    errors: list[Exception] = []

    def worker(base: int) -> None:
        for i in range(150):
            conn = sqlite3.connect(p, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            try:
                with sqlite_txn(conn):
                    conn.execute("INSERT INTO t VALUES (?)", (base * 1000 + i,))
                    if i % 2 == 0:
                        with sqlite_txn(conn) as inner:
                            inner.execute("INSERT INTO t VALUES (?)", (base * 1000 + i,))
                        raise ValueError("planned outer failure")
            except ValueError:
                pass  # planned rollback; keep iterating
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(b,)) for b in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    # Only odd iterations committed (75 per worker).
    assert _count(p) == 4 * 75
    assert sqlite_tx._depths() == {}