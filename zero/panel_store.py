from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from contextlib import contextmanager, suppress
import sqlite3
import time
from pathlib import Path
from typing import Any

from .configuration import (
    SetupService,
    TelegramConfig,
    ensure_private_directory,
    is_credential_like,
    is_safe_installation_id,
    is_symbolic_secret_reference,
    restrict_private_file,
)
from .paths import zero_home
from .sqlite_tx import sqlite_txn

# Shares the panel's logger so a storage failure reaches the same file the panel
# serves back through /api/logs.
logger = logging.getLogger("zero.panel")

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
_TELEGRAM_SETUP_KEYS = frozenset({"mode", "bot_token_ref", "api_id", "api_hash_ref", "session_ref"})
_SETUP_STEP_KEYS: dict[str, frozenset[str]] = {
    "welcome": frozenset(),
    "profile": frozenset({"profile", "installation_id"}),
    "administrator": frozenset(),
    "telegram": _TELEGRAM_SETUP_KEYS,
    "credentials": frozenset(),
    "provider": frozenset({"provider", "model"}),
    "web_search": frozenset({"enabled", "provider", "api_key_ref"}),
    "features": frozenset({"office", "proactive"}),
    "groups": frozenset({"chat_id", "name"}),
    "review": frozenset(),
    "validation": frozenset({"config_valid"}),
    "start": frozenset({"ready"}),
}
_PROFILE_CHOICES = frozenset({"personal", "single_group", "multi_group", "advanced"})
_PROVIDER_CHOICES = frozenset({"openrouter", "gemini", "custom"})
_WEB_SEARCH_PROVIDERS = frozenset({"disabled", "tavily", "brave", "wigolo"})


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
        return {
            k: "[stored securely]"
            if k.casefold() in _SECRET_KEYS
            or any(part in k.casefold() for part in ("token", "secret", "password", "api_key", "api_hash", "session"))
            else _strip_secrets(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_strip_secrets(v) for v in value]
    return value


class PanelStore:
    def __init__(self, path: str | Path, *, setup_service: SetupService | None = None):
        self.path = Path(path)
        self.setup_service = setup_service
        try:
            managed_runtime_path = self.path.parent.resolve() == zero_home().resolve()
        except OSError as exc:
            # Deciding "not managed" skips the permission repair on an existing
            # directory, so an unresolvable path can leave the panel database in
            # a directory whose inherited access was never stripped. Recorded
            # because nothing else would reveal it.
            managed_runtime_path = False
            logger.warning('PANEL_STORE_RUNTIME_PATH_UNRESOLVED exception_type=%s', type(exc).__name__)
        ensure_private_directory(self.path.parent, repair_existing=managed_runtime_path)
        self._init()
        restrict_private_file(self.path)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def _conn(self):
        # sqlite3's connection context manager only wraps a transaction; it
        # never closes. On Windows an open handle keeps the DB file locked and
        # breaks rollback/cleanup paths that unlink the file. sqlite_txn owns
        # commit, rollback and close, and never lets a close failure mask the
        # exception that caused the rollback.
        with sqlite_txn(self._connect()) as db:
            yield db


    def _init(self) -> None:
        with self._conn() as db:
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

    def snapshot_setup_state(self) -> tuple[str, int, str, int]:
        """Capture the wizard row so a coordinated setup can be compensated."""
        with self._conn() as db:
            row = db.execute(
                "SELECT current_step, completed, data_json, updated_at FROM panel_setup WHERE id=1"
            ).fetchone()
        if row is None:  # pragma: no cover - _init creates the singleton row
            raise sqlite3.Error("panel setup row is unavailable")
        return (row["current_step"], row["completed"], row["data_json"], row["updated_at"])

    def restore_setup_state(self, snapshot: tuple[str, int, str, int]) -> None:
        """Restore only wizard progress, leaving administrators and sessions intact."""
        with self._conn() as db:
            db.execute(
                "UPDATE panel_setup SET current_step=?, completed=?, data_json=?, updated_at=? WHERE id=1",
                snapshot,
            )

    @staticmethod
    def remove_database_files(path: str | Path) -> None:
        """Remove a newly-created panel database and any SQLite sidecars."""
        panel_path = Path(path)
        for suffix in ("", "-journal", "-wal", "-shm"):
            # Absence is the goal, so it is suppressed rather than handled.
            with suppress(FileNotFoundError):
                panel_path.with_name(f"{panel_path.name}{suffix}").unlink()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _normalize_telegram_setup(data: dict[str, Any]) -> dict[str, Any]:
        """Convert browser form values to the canonical reference-only schema."""
        if set(data) - _TELEGRAM_SETUP_KEYS:
            raise ValueError("telegram setup validation failed")

        mode = data.get("mode", "disabled")
        if not isinstance(mode, str) or not mode.strip():
            raise ValueError("telegram setup validation failed")
        normalized: dict[str, Any] = {"mode": mode.strip().casefold()}

        for key in ("bot_token_ref", "api_hash_ref", "session_ref"):
            value = data.get(key)
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError("telegram setup validation failed")
            value = value.strip()
            if value:
                normalized[key] = value

        raw_api_id = data.get("api_id")
        if isinstance(raw_api_id, str):
            raw_api_id = raw_api_id.strip() or None
        if raw_api_id is not None:
            if isinstance(raw_api_id, bool) or not isinstance(raw_api_id, (str, int)):
                raise ValueError("telegram setup validation failed")
            try:
                api_id = int(raw_api_id)
            except ValueError as exc:
                raise ValueError("telegram setup validation failed") from exc
            if api_id <= 0:
                raise ValueError("telegram setup validation failed")
            normalized["api_id"] = api_id
        try:
            return TelegramConfig.model_validate(normalized).model_dump(mode="python", exclude_none=True)
        except ValueError as exc:
            raise ValueError("telegram setup validation failed") from exc

    @staticmethod
    def _normalize_setup_step(step: str, data: dict[str, Any]) -> dict[str, Any]:
        """Validate every persisted setup payload against its public schema."""
        if not isinstance(data, dict) or set(data) - _SETUP_STEP_KEYS[step]:
            raise ValueError("setup validation failed")
        if step == "telegram":
            return PanelStore._normalize_telegram_setup(data)
        if step in {"welcome", "administrator", "credentials", "review"}:
            return {}
        if step == "profile":
            if not data:
                return {}
            if set(data) == {"installation_id"}:
                installation_id = data["installation_id"]
                if not is_safe_installation_id(installation_id):
                    raise ValueError("setup validation failed")
                return {"installation_id": installation_id.strip()}
            profile = data.get("profile")
            if set(data) != {"profile"} or not isinstance(profile, str) or profile not in _PROFILE_CHOICES:
                raise ValueError("setup validation failed")
            return {"profile": profile}
        if step == "provider":
            provider = data.get("provider")
            model = data.get("model", "")
            if (
                not isinstance(provider, str)
                or provider not in _PROVIDER_CHOICES
                or not isinstance(model, str)
                or len(model) > 128
                or is_credential_like(model)
            ):
                raise ValueError("setup validation failed")
            return {"provider": provider, "model": model.strip()}
        if step == "web_search":
            provider = data.get("provider", "disabled")
            enabled = data.get("enabled", False)
            api_key_ref = data.get("api_key_ref")
            if provider not in _WEB_SEARCH_PROVIDERS or enabled not in {False, True, "", "on"}:
                raise ValueError("setup validation failed")
            normalized: dict[str, Any] = {"enabled": enabled in {True, "on"}, "provider": provider}
            if api_key_ref not in {None, ""}:
                if not isinstance(api_key_ref, str) or not is_symbolic_secret_reference(api_key_ref.strip()):
                    raise ValueError("setup validation failed")
                normalized["api_key_ref"] = api_key_ref.strip()
            return normalized
        if step == "features":
            if any(value not in {True, False, "", "on"} for value in data.values()):
                raise ValueError("setup validation failed")
            return {"office": data.get("office") in {True, "on"}, "proactive": data.get("proactive") in {True, "on"}}
        if step == "groups":
            chat_id = data.get("chat_id", "")
            name = data.get("name", "")
            if not isinstance(name, str) or len(name) > 128 or is_credential_like(name):
                raise ValueError("setup validation failed")
            normalized = {"name": name.strip()}
            if chat_id not in {None, ""}:
                if isinstance(chat_id, bool) or not isinstance(chat_id, (str, int)):
                    raise ValueError("setup validation failed")
                try:
                    normalized["chat_id"] = int(chat_id)
                except ValueError as exc:
                    raise ValueError("setup validation failed") from exc
            return normalized
        if step == "validation":
            if set(data) != {"config_valid"} or not isinstance(data["config_valid"], bool):
                raise ValueError("setup validation failed")
            return {"config_valid": data["config_valid"]}
        if step == "start":
            if set(data) != {"ready"} or not isinstance(data["ready"], bool):
                raise ValueError("setup validation failed")
            return {"ready": data["ready"]}
        raise ValueError("setup validation failed")

    def create_admin(self, username: str, password: str, *, must_change_password: bool = False) -> int:
        username = username.strip().lower()
        if not username or len(username) > 120:
            raise ValueError("username is required")
        try:
            with self._conn() as db:
                cur = db.execute(
                    "INSERT INTO panel_admins(username,password_hash,must_change_password,created_at) VALUES(?,?,?,?)",
                    (username, _hash_password(password, allow_weak=(must_change_password and username == "admin" and password == "Admin")), int(must_change_password), int(time.time())),
                )
                return int(cur.lastrowid or 0)
        except sqlite3.IntegrityError as exc:
            raise DuplicateAdminError("administrator already exists") from exc

    def change_admin_password(self, admin_id: int, current_password: str, new_password: str) -> None:
        with self._conn() as db:
            row = db.execute("SELECT password_hash FROM panel_admins WHERE id=? AND disabled=0", (admin_id,)).fetchone()
            if not row or not _verify_password(current_password, row["password_hash"]):
                raise ValueError("current password is incorrect")
            db.execute("UPDATE panel_admins SET password_hash=?,must_change_password=0 WHERE id=?", (_hash_password(new_password), admin_id))

    def verify_admin(self, username: str, password: str) -> dict[str, Any] | None:
        with self._conn() as db:
            row = db.execute("SELECT * FROM panel_admins WHERE username=? AND disabled=0", (username.strip().lower(),)).fetchone()
        return dict(row) if row and _verify_password(password, row["password_hash"]) else None

    def create_session(self, admin_id: int, ttl_seconds: int = 86400) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        now = int(time.time())
        with self._conn() as db:
            # Expiry was applied on lookup only, so every login left a row behind
            # for the lifetime of the installation. Sweeping on the write path
            # keeps the table proportional to live sessions without a timer.
            db.execute("DELETE FROM panel_sessions WHERE expires_at<=?", (now,))
            db.execute("INSERT INTO panel_sessions VALUES(?,?,?,?,?)", (self._token_hash(token), admin_id, csrf, now, now + ttl_seconds))
        return token, csrf

    def get_session(self, token: str) -> dict[str, Any] | None:
        with self._conn() as db:
            row = db.execute(
                "SELECT s.*, a.username, a.role, a.must_change_password FROM panel_sessions s JOIN panel_admins a ON a.id=s.admin_id WHERE s.token_hash=? AND s.expires_at>? AND a.disabled=0",
                (self._token_hash(token), int(time.time())),
            ).fetchone()
        return dict(row) if row else None

    def revoke_session(self, token: str) -> None:
        with self._conn() as db:
            db.execute("DELETE FROM panel_sessions WHERE token_hash=?", (self._token_hash(token),))

    def get_setup_state(self) -> dict[str, Any]:
        with self._conn() as db:
            row = db.execute("SELECT * FROM panel_setup WHERE id=1").fetchone()
        data = json.loads(row["data_json"])
        return {"current_step": row["current_step"], "completed": bool(row["completed"]), "data": _strip_secrets(data), "updated_at": row["updated_at"]}

    def save_setup_step(self, step: str, data: dict[str, Any]) -> None:
        if step not in SETUP_STEPS:
            raise ValueError("unknown setup step")
        data = self._normalize_setup_step(step, data)
        if step == "telegram":
            if self.setup_service is not None:
                state = self.setup_service.apply_telegram(
                    mode=data.get("mode", "disabled"),
                    bot_token_ref=data.get("bot_token_ref"),
                    api_id=data.get("api_id"),
                    api_hash_ref=data.get("api_hash_ref"),
                    session_ref=data.get("session_ref"),
                )
                if "telegram" not in state.completed_steps:
                    raise ValueError("telegram setup validation failed")
        with self._conn() as db:
            row = db.execute("SELECT data_json FROM panel_setup WHERE id=1").fetchone()
            current = json.loads(row["data_json"])
            current[step] = _strip_secrets(data)
            index = SETUP_STEPS.index(step)
            next_step = SETUP_STEPS[min(index + 1, len(SETUP_STEPS) - 1)]
            db.execute("UPDATE panel_setup SET current_step=?,data_json=?,completed=?,updated_at=? WHERE id=1", (next_step, json.dumps(current), int(step == "start"), int(time.time())))

    def skip_setup(self) -> None:
        with self._conn() as db:
            row = db.execute("SELECT data_json FROM panel_setup WHERE id=1").fetchone()
            data = json.loads(row["data_json"])
            data["skipped"] = {"reason": "existing installation", "updated_at": int(time.time())}
            db.execute("UPDATE panel_setup SET current_step='start',data_json=?,completed=1,updated_at=? WHERE id=1", (json.dumps(data), int(time.time())))
