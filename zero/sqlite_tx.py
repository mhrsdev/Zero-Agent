"""Shared SQLite transaction scope: commit/rollback AND deterministic close.

Contract (v2, savepoint-based nesting)
--------------------------------------
``sqlite3.Connection`` used directly as a context manager only commits or
rolls back -- it never closes. Every ``with store._conn() as conn:`` site in
this project therefore leaked a file handle until garbage collection.
``sqlite_txn`` is the required scope instead.

* **Outermost scope** (connection not already inside a ``sqlite_txn``):
  runs the block as one transaction -- commit on success, rollback on error,
  original exception preserved even if ``close`` fails -- then closes the
  connection exactly once. A close failure on the success path is real and
  surfaces; on the error path it is logged and never masks the original.
* **Nested scope** (same connection inside an active outermost scope):
  opens a SAVEPOINT. Inner success releases the savepoint (still uncommitted);
  inner failure rolls back ONLY the inner writes and re-raises. Because the
  inner work stays uncommitted until the outermost scope commits, an outer
  failure rolls back the inner data too: **nesting is atomic with the outer
  transaction**, which is the semantics every caller in this project needs
  (no production call site relies on inner-commit-persists).
* **Thread safety**: the depth registry is ``threading.local``, so concurrent
  transactions on different connections (the only supported pattern --
  sqlite3 connections are not shareable across threads by default) cannot
  interfere. An ``id(conn)`` entry exists only while its connection is alive
  on that thread's stack, so id reuse cannot collide.
* **No state leaks**: the depth entry is removed in a ``finally`` on every
  path; verified by stress tests asserting the registry is empty afterwards.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_local = threading.local()


def _depths() -> dict[int, int]:
    """Per-thread {id(conn): nesting depth}."""
    mapping = getattr(_local, "depths", None)
    if mapping is None:
        mapping = {}
        _local.depths = mapping
    return mapping


def active_depth(conn: sqlite3.Connection) -> int:
    """Nesting depth for *conn* on the current thread (0 = not managed)."""
    return _depths().get(id(conn), 0)


def _safe_close(conn: sqlite3.Connection) -> None:
    """Close without ever masking an in-flight exception."""
    try:
        conn.close()
    except Exception as exc:
        logger.warning("SQLITE_TXN_CLOSE_FAILED exception_type=%s", type(exc).__name__)


@contextmanager
def sqlite_txn(conn: sqlite3.Connection):
    depths = _depths()
    key = id(conn)
    depth = depths.get(key, 0)

    if depth == 0:
        # ---- outermost: own the connection lifecycle --------------------
        depths[key] = 1
        ok = False
        try:
            with conn:
                yield conn
            ok = True
        finally:
            depths.pop(key, None)
            if ok:
                # Success path: a close failure is real and must surface.
                conn.close()
            else:
                # Error path: preserve the original exception.
                _safe_close(conn)
    else:
        # ---- nested: savepoint, atomic with the outer transaction -------
        depths[key] = depth + 1
        savepoint = f"zero_sp_{depth}"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            yield conn
        except BaseException:
            try:
                conn.execute(f"ROLLBACK TO {savepoint}")
                conn.execute(f"RELEASE {savepoint}")
            except sqlite3.Error as exc:
                logger.warning(
                    "SQLITE_TXN_SAVEPOINT_ROLLBACK_FAILED savepoint=%s exception_type=%s",
                    savepoint, type(exc).__name__,
                )
            raise
        else:
            conn.execute(f"RELEASE {savepoint}")
        finally:
            depths[key] = depth