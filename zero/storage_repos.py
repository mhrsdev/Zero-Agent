"""Thin SQL helpers used by ZeroStore. Call-sites keep using the façade."""
from __future__ import annotations

import json
from typing import Any


class SettingsRepo:
    def __init__(self, conn):
        self.conn = conn

    def get(self, key: str, default: str | None = None) -> str | None:
        return get_setting(self.conn, key, default)

    def set(self, key: str, value: Any) -> None:
        set_setting(self.conn, key, value)


class RecentMessagesRepo:
    def __init__(self, conn):
        self.conn = conn

    def list(self, chat_id: int, limit: int) -> list:
        return list_recent(self.conn, chat_id, limit)


class DailyStatsRepo:
    _COLUMNS = frozenset({
        "message_count", "reply_count", "api_calls", "retries", "errors",
        "input_chars", "output_chars", "total_cost_usd",
    })

    def __init__(self, conn):
        self.conn = conn

    def add(self, day: str, **deltas: int | float) -> None:
        self.conn.execute("INSERT OR IGNORE INTO stats(day) VALUES (?)", (day,))
        for key, value in deltas.items():
            if key not in self._COLUMNS:
                continue
            self.conn.execute(f"UPDATE stats SET {key} = COALESCE({key}, 0) + ? WHERE day=?", (value, day))
        self.conn.commit()


def get_setting(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    return row["value"] if not isinstance(row, tuple) else row[0]


def set_setting(conn, key: str, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, payload),
    )
    conn.commit()


def list_recent(conn, chat_id: int, limit: int) -> list:
    rows = conn.execute(
        "SELECT * FROM recent_messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
        (int(chat_id), int(limit)),
    ).fetchall()
    return list(reversed(rows))
