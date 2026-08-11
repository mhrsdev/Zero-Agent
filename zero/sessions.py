"""Secure local registry and attended Telegram login for Zero sessions."""
from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .paths import zero_home

_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SESSION_STATES = {"new", "authorizing", "authorized", "unauthorized", "cancelled", "error"}


class SessionRegistryError(ValueError):
    pass


class ActiveSessionError(SessionRegistryError):
    pass


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: str
    label: str
    session_path: Path
    state: str
    active: bool
    managed: bool
    user_id: int | None
    username: str
    last_error: str
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class LoginOutcome:
    status: str
    user_id: int | None = None
    username: str = ""
    detail: str = ""


class LoginAdapter(Protocol):
    async def login(
        self,
        *,
        session_path: Path,
        api_id: int,
        api_hash: str,
        phone: str,
        code_provider: Callable[[], str],
        password_provider: Callable[[], str],
    ) -> LoginOutcome: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
    session_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    session_path TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
    managed INTEGER NOT NULL DEFAULT 1 CHECK(managed IN (0,1)),
    user_id INTEGER,
    username TEXT NOT NULL DEFAULT "",
    last_error TEXT NOT NULL DEFAULT "",
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS sessions_one_active_idx ON sessions(active) WHERE active=1;
"""


class SessionRegistry:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else zero_home() / "sessions"
        self.root = self.root.expanduser().resolve()
        self.files_dir = self.root / "files"
        self.db_path = self.root / "registry.db"
        self.root.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        os.chmod(self.files_dir, 0o700)
        with self._conn() as db:
            db.executescript(_SCHEMA)
        os.chmod(self.db_path, 0o600)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @staticmethod
    def _validate_id(session_id: str) -> str:
        value = str(session_id or "").strip()
        if not _SESSION_ID.fullmatch(value):
            raise SessionRegistryError("session id must be symbolic and contain only letters, numbers, dot, dash or underscore")
        return value

    @staticmethod
    def _record(row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            session_id=row["session_id"],
            label=row["label"],
            session_path=Path(row["session_path"]),
            state=row["state"],
            active=bool(row["active"]),
            managed=bool(row["managed"]),
            user_id=int(row["user_id"]) if row["user_id"] is not None else None,
            username=row["username"],
            last_error=row["last_error"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def add(self, session_id: str, *, label: str = "", session_path: str | Path | None = None) -> SessionRecord:
        session_id = self._validate_id(session_id)
        managed = session_path is None
        path = (self.files_dir / session_id) if managed else Path(session_path).expanduser().resolve()
        if managed and path.parent.resolve() != self.files_dir.resolve():
            raise SessionRegistryError("managed session path escaped its protected directory")
        now = time.time()
        try:
            with self._conn() as db:
                db.execute(
                    "INSERT INTO sessions(session_id,label,session_path,state,active,managed,created_at,updated_at) VALUES(?,?,?,?,0,?,?,?)",
                    (session_id, str(label or session_id).strip(), str(path), "new", int(managed), now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise SessionRegistryError("session id or path is already registered") from exc
        return self.get(session_id)

    def get(self, session_id: str) -> SessionRecord:
        session_id = self._validate_id(session_id)
        with self._conn() as db:
            row = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise SessionRegistryError("unknown session")
        return self._record(row)

    def list(self) -> list[SessionRecord]:
        with self._conn() as db:
            rows = db.execute("SELECT * FROM sessions ORDER BY active DESC, session_id").fetchall()
        return [self._record(row) for row in rows]

    def active(self) -> SessionRecord | None:
        with self._conn() as db:
            row = db.execute("SELECT * FROM sessions WHERE active=1").fetchone()
        return self._record(row) if row is not None else None

    def mark_state(self, session_id: str, state: str, *, last_error: str = "") -> SessionRecord:
        if state not in _SESSION_STATES:
            raise SessionRegistryError("invalid session state")
        safe_error = re.sub(r"[^A-Za-z0-9_.:-]", "_", str(last_error))[:80]
        with self._conn() as db:
            result = db.execute(
                "UPDATE sessions SET state=?,last_error=?,active=CASE WHEN ?=1 THEN active ELSE 0 END,updated_at=? WHERE session_id=?",
                (state, safe_error, int(state == "authorized"), time.time(), self._validate_id(session_id)),
            )
        if result.rowcount != 1:
            raise SessionRegistryError("unknown session")
        return self.get(session_id)

    def mark_authorized(self, session_id: str, *, user_id: int | None, username: str = "") -> SessionRecord:
        safe_username = re.sub(r"[^A-Za-z0-9_]", "", str(username or ""))[:64]
        with self._conn() as db:
            result = db.execute(
                "UPDATE sessions SET state=?,user_id=?,username=?,last_error=?,updated_at=? WHERE session_id=?",
                ("authorized", int(user_id) if user_id is not None else None, safe_username, "", time.time(), self._validate_id(session_id)),
            )
        if result.rowcount != 1:
            raise SessionRegistryError("unknown session")
        self._secure_session_files(self.get(session_id).session_path)
        return self.get(session_id)

    @staticmethod
    def _candidate_files(base: Path) -> tuple[Path, ...]:
        primary = base if base.suffix == ".session" else Path(str(base) + ".session")
        return tuple(dict.fromkeys((base, primary, Path(str(primary) + "-journal"), Path(str(primary) + "-wal"), Path(str(primary) + "-shm"))))

    @classmethod
    def _secure_session_files(cls, base: Path) -> None:
        for path in cls._candidate_files(base):
            if path.is_file() and not path.is_symlink():
                os.chmod(path, 0o600)

    @classmethod
    def _session_exists(cls, base: Path) -> bool:
        return any(path.is_file() for path in cls._candidate_files(base)[:2])

    def activate(self, session_id: str) -> SessionRecord:
        record = self.get(session_id)
        if record.state != "authorized":
            raise SessionRegistryError("only an authorized session can become active")
        if not self._session_exists(record.session_path):
            raise SessionRegistryError("authorized session file is missing")
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE sessions SET active=0,updated_at=? WHERE active=1", (time.time(),))
            db.execute("UPDATE sessions SET active=1,updated_at=? WHERE session_id=?", (time.time(), record.session_id))
            db.execute("COMMIT")
        return self.get(record.session_id)

    def deactivate(self, session_id: str) -> SessionRecord:
        record = self.get(session_id)
        with self._conn() as db:
            db.execute("UPDATE sessions SET active=0,updated_at=? WHERE session_id=?", (time.time(), record.session_id))
        return self.get(record.session_id)

    def resolve_active_path(self, fallback: str | Path) -> Path:
        record = self.active()
        return record.session_path if record is not None else Path(fallback).expanduser()

    def remove(self, session_id: str, *, confirmed: bool = False) -> None:
        if not confirmed:
            raise SessionRegistryError("session removal requires explicit confirmation")
        record = self.get(session_id)
        if record.active:
            raise ActiveSessionError("active session cannot be removed; activate another session first")
        if record.managed:
            if record.session_path.parent.resolve() != self.files_dir.resolve():
                raise SessionRegistryError("refusing to delete a managed path outside the protected directory")
            for path in self._candidate_files(record.session_path):
                if path.is_symlink() or path.is_file():
                    path.unlink()
        with self._conn() as db:
            db.execute("DELETE FROM sessions WHERE session_id=?", (record.session_id,))


class TelethonLoginAdapter:
    def __init__(self, *, timeout_seconds: float = 90.0, client_factory=None):
        self.timeout_seconds = float(timeout_seconds)
        self.client_factory = client_factory

    async def login(self, *, session_path: Path, api_id: int, api_hash: str, phone: str, code_provider: Callable[[], str], password_provider: Callable[[], str]) -> LoginOutcome:
        from telethon import TelegramClient
        from telethon.errors import FloodWaitError, PasswordHashInvalidError, PhoneCodeExpiredError, PhoneCodeInvalidError, PhoneNumberInvalidError, SessionPasswordNeededError

        factory = self.client_factory or TelegramClient
        client = factory(str(session_path), int(api_id), str(api_hash))
        try:
            await asyncio.wait_for(client.connect(), timeout=self.timeout_seconds)
            if not await asyncio.wait_for(client.is_user_authorized(), timeout=self.timeout_seconds):
                try:
                    challenge = await asyncio.wait_for(client.send_code_request(str(phone)), timeout=self.timeout_seconds)
                except PhoneNumberInvalidError:
                    return LoginOutcome("invalid_phone")
                code = str(code_provider() or "").replace(" ", "").replace("-", "")
                if not code:
                    return LoginOutcome("cancelled")
                try:
                    await asyncio.wait_for(client.sign_in(str(phone), code, phone_code_hash=challenge.phone_code_hash), timeout=self.timeout_seconds)
                except SessionPasswordNeededError:
                    password = str(password_provider() or "")
                    if not password:
                        return LoginOutcome("cancelled")
                    try:
                        await asyncio.wait_for(client.sign_in(password=password), timeout=self.timeout_seconds)
                    except PasswordHashInvalidError:
                        return LoginOutcome("invalid_password")
                except PhoneCodeInvalidError:
                    return LoginOutcome("invalid_code")
                except PhoneCodeExpiredError:
                    return LoginOutcome("expired_code")
            me = await asyncio.wait_for(client.get_me(), timeout=self.timeout_seconds)
            if me is None:
                return LoginOutcome("unauthorized")
            return LoginOutcome("authorized", user_id=int(me.id), username=str(getattr(me, "username", "") or ""))
        except asyncio.TimeoutError:
            return LoginOutcome("timeout")
        except FloodWaitError:
            return LoginOutcome("rate_limited")
        except OSError:
            return LoginOutcome("network_error")
        finally:
            try:
                await client.disconnect()
            finally:
                SessionRegistry._secure_session_files(session_path)


async def login_session(registry: SessionRegistry, session_id: str, *, adapter: LoginAdapter, api_id: int, api_hash: str, phone: str, code_provider: Callable[[], str], password_provider: Callable[[], str]) -> LoginOutcome:
    record = registry.get(session_id)
    registry.mark_state(session_id, "authorizing")
    try:
        outcome = await adapter.login(
            session_path=record.session_path,
            api_id=int(api_id),
            api_hash=str(api_hash),
            phone=str(phone),
            code_provider=code_provider,
            password_provider=password_provider,
        )
    except Exception as exc:
        registry.mark_state(session_id, "error", last_error=type(exc).__name__)
        return LoginOutcome("error", detail=type(exc).__name__)
    if outcome.status == "authorized":
        registry.mark_authorized(session_id, user_id=outcome.user_id, username=outcome.username)
    elif outcome.status == "cancelled":
        registry.mark_state(session_id, "cancelled")
    else:
        registry.mark_state(session_id, "unauthorized", last_error=outcome.status)
    return outcome
