"""Shared transaction scope for SQLite connections.

``sqlite3.Connection`` used as a context manager commits or rolls back but
never closes the connection.  Every ``with store._conn() as conn:`` site in
this codebase therefore leaked a file handle until GC ran.  Use
``sqlite_txn(conn)`` instead: it keeps the commit/rollback semantics and
always closes the underlying connection.
"""
from __future__ import annotations

from contextlib import contextmanager
import sqlite3


@contextmanager
def sqlite_txn(conn: sqlite3.Connection):
    try:
        with conn:
            yield conn
    finally:
        conn.close()