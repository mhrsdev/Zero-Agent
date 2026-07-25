from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from .configuration import SetupService

SETUP_STEPS = (
    "welcome",
    "profile",
    "administrator",
    "telegram",
    "credentials",
    "provider",
    "web_search",
    "features",
    "groups",
    "review",
    "validation",
    "start",
)
_SECRET_KEYS = {"token", "bot_token", "api_key", "api_hash", "password", "otp", "session"}


class DuplicateAdminError(ValueError):
    pass


def _hash_password(password: str, salt: bytes | None = None, *, allow_weak: bool = False) -> str:
    if len(password) < 12 and not allow_weak:
        raise ValueError("password must be at least 12 characters")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(actual.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _strip_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "[stored securely]" if k.casefold() in _SECRET_KEYS or any(part in k.casefold() for part in ("token", "secret", "password", "api_key", "api_hash")) else _strip_secrets(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_secrets(v) for v in value]
    return value


class PanelStore:
    def __init__(self, path: str | Path, *, setup_service: SetupService | None = None):
        self.path = Path(path)
        self.setup_service = setup_service
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def _init(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS panel_admins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'owner',
                    created_at INTEGER NOT NULL,
                    disabled INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS panel_sessions (
                    token_hash TEXT PRIMARY KEY,
                    admin_id INTEGER NOT NULL REFERENCES panel_admins(id),
                    csrf_token TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS panel_setup (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    current_step TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    data_json TEXT NOT NULL DEFAULT '{}',
                    updated_at INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO panel_setup(id,current_step,completed,data_json,updated_at)
                VALUES (1, 'welcome', 0, '{}', strftime('%s','now'));
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(panel_admins)")}
            if "must_change_password" not in columns:
                db.execute("ALTER TABLE panel_admins ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def create_admin(self, username: str, password: str, *, must_change_password: bool = False) -> int:
        username = username.strip().lower()
        if not username or len(username) > 120:
            raise ValueError("username is required")
        try:
            with self._connect() as db:
                cur = db.execute(
                    "INSERT INTO panel_admins(username,password_hash,must_change_password,created_at) VALUES(?,?,?,?)",
                    (username, _hash_password(password, allow_weak=(must_change_password and username == "admin" and password == "Admin")), int(must_change_password), int(time.time())),
                )
                return int(cur.lastrowid or 0)
        except sqlite3.IntegrityError as exc:
            raise DuplicateAdminError("administrator already exists") from exc

    def change_admin_password(self, admin_id: int, current_password: str, new_password: str) -> None:
        with self._connect() as db:
            row = db.execute("SELECT password_hash FROM panel_admins WHERE id=? AND disabled=0", (admin_id,)).fetchone()
            if not row or not _verify_password(current_password, row["password_hash"]):
                raise ValueError("current password is incorrect")
            db.execute("UPDATE panel_admins SET password_hash=?,must_change_password=0 WHERE id=?", (_hash_password(new_password), admin_id))

    def verify_admin(self, username: str, password: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM panel_admins WHERE username=? AND disabled=0", (username.strip().lower(),)).fetchone()
        return dict(row) if row and _verify_password(password, row["password_hash"]) else None

    def create_session(self, admin_id: int, ttl_seconds: int = 86400) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        now = int(time.time())
        with self._connect() as db:
            db.execute("INSERT INTO panel_sessions VALUES(?,?,?,?,?)", (self._token_hash(token), admin_id, csrf, now, now + ttl_seconds))
        return token, csrf

    def get_session(self, token: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT s.*, a.username, a.role, a.must_change_password FROM panel_sessions s JOIN panel_admins a ON a.id=s.admin_id WHERE s.token_hash=? AND s.expires_at>? AND a.disabled=0",
                (self._token_hash(token), int(time.time())),
            ).fetchone()
        return dict(row) if row else None

    def revoke_session(self, token: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM panel_sessions WHERE token_hash=?", (self._token_hash(token),))

    def get_setup_state(self) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM panel_setup WHERE id=1").fetchone()
        data = json.loads(row["data_json"])
        return {"current_step": row["current_step"], "completed": bool(row["completed"]), "data": _strip_secrets(data), "updated_at": row["updated_at"]}

    def save_setup_step(self, step: str, data: dict[str, Any]) -> None:
        if step not in SETUP_STEPS:
            raise ValueError("unknown setup step")
        if step == "telegram" and self.setup_service is not None:
            state = self.setup_service.apply_telegram(
                mode=data.get("mode", "disabled"),
                bot_token_ref=data.get("bot_token_ref"),
                api_id=data.get("api_id"),
                api_hash_ref=data.get("api_hash_ref"),
                session_ref=data.get("session_ref"),
            )
            if "telegram" not in state.completed_steps:
                raise ValueError("telegram setup validation failed")
        with self._connect() as db:
            row = db.execute("SELECT data_json FROM panel_setup WHERE id=1").fetchone()
            current = json.loads(row["data_json"])
            current[step] = _strip_secrets(data)
            index = SETUP_STEPS.index(step)
            next_step = SETUP_STEPS[min(index + 1, len(SETUP_STEPS) - 1)]
            db.execute("UPDATE panel_setup SET current_step=?,data_json=?,completed=?,updated_at=? WHERE id=1", (next_step, json.dumps(current), int(step == "start"), int(time.time())))

    def skip_setup(self) -> None:
        with self._connect() as db:
            row = db.execute("SELECT data_json FROM panel_setup WHERE id=1").fetchone()
            data = json.loads(row["data_json"])
            data["skipped"] = {"reason": "existing installation", "updated_at": int(time.time())}
            db.execute("UPDATE panel_setup SET current_step='start',data_json=?,completed=1,updated_at=? WHERE id=1", (json.dumps(data), int(time.time())))
