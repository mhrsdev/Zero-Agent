from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..paths import zero_home

_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{2,127}$")
_RAW_TELEGRAM_BOT_TOKEN = re.compile(r"^\d{5,12}:[A-Za-z0-9_-]{20,}$")
_RAW_TELEGRAM_API_HASH = re.compile(r"^[A-Fa-f0-9]{32}$")
_RAW_OPAQUE_SECRET = re.compile(r"^[A-Za-z0-9_-]{48,}$")


def is_credential_like(value: str | None) -> bool:
    """Return whether a string matches a credential shape that must not be stored."""
    return isinstance(value, str) and any(
        pattern.fullmatch(value)
        for pattern in (
            _RAW_TELEGRAM_BOT_TOKEN,
            _RAW_TELEGRAM_API_HASH,
            _RAW_OPAQUE_SECRET,
        )
    )


def is_symbolic_secret_reference(value: str | None) -> bool:
    """Return whether ``value`` is a reference name rather than secret material.

    The canonical config deliberately holds only identifiers for a separate
    secret store.  A reference-like regex alone is not sufficient because
    Telegram API hashes and URL-safe session strings can also match it.
    """
    if value is None or _REF.fullmatch(value) is None:
        return False
    return not any(
        pattern.fullmatch(value)
        for pattern in (
            _RAW_TELEGRAM_BOT_TOKEN,
            _RAW_TELEGRAM_API_HASH,
            _RAW_OPAQUE_SECRET,
        )
    )


def is_safe_installation_id(value: str | None) -> bool:
    """Return whether an installation label cannot be mistaken for a credential."""
    if not isinstance(value, str) or not (1 <= len(value) <= 128):
        return False
    return not any(
        pattern.fullmatch(value)
        for pattern in (
            _RAW_TELEGRAM_BOT_TOKEN,
            _RAW_TELEGRAM_API_HASH,
            _RAW_OPAQUE_SECRET,
        )
    )


def canonical_config_path(env: Mapping[str, str] | None = None) -> Path:
    """Return the canonical setup file shared by every runtime entry point."""
    values = env if env is not None else os.environ
    configured = values.get("ZERO_CANONICAL_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return zero_home(values) / "config" / "zero.json"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TelegramConfig(StrictModel):
    mode: Literal["disabled", "bot", "user_session", "hybrid"] = "disabled"
    bot_token_ref: str | None = None
    api_id: int | None = Field(default=None, gt=0, strict=True)
    api_hash_ref: str | None = None
    session_ref: str | None = None

    @model_validator(mode="after")
    def validate_transport(self) -> "TelegramConfig":
        refs = (self.bot_token_ref, self.api_hash_ref, self.session_ref)
        if any(not is_symbolic_secret_reference(value) for value in refs if value is not None):
            raise ValueError("secret references must be symbolic names")
        if self.mode in {"bot", "hybrid"} and not self.bot_token_ref:
            raise ValueError("bot mode requires bot_token_ref")
        if self.mode in {"user_session", "hybrid"} and not (self.api_id and self.api_hash_ref and self.session_ref):
            raise ValueError("user session mode requires api_id, api_hash_ref and session_ref")
        return self


class CanonicalConfig(StrictModel):
    schema_version: int = Field(default=1, ge=1)
    installation_id: str = Field(min_length=1, max_length=128)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)

    @field_validator("installation_id")
    @classmethod
    def validate_installation_id(cls, value: str) -> str:
        if not is_safe_installation_id(value):
            raise ValueError("installation id must not contain credential-like material")
        return value


def _apply_windows_private_acl(path: str | Path, *, directory: bool) -> None:
    """Remove inherited ACLs and grant access only to the current user and SYSTEM."""
    identity = os.environ.get("USERNAME")
    if not identity:
        raise PermissionError("cannot determine the Windows user for private storage")
    permission = "(OI)(CI)F" if directory else "F"
    command = [
        "icacls",
        str(path),
        "/inheritance:r",
        "/grant:r",
        f"{identity}:{permission}",
        "/grant:r",
        f"SYSTEM:{permission}",
        "/c",
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=False, text=True)
    except OSError as exc:
        raise PermissionError("Windows ACL hardening is unavailable") from exc
    if result.returncode != 0:
        raise PermissionError("Windows ACL hardening failed")


def ensure_private_directory(path: str | Path, *, repair_existing: bool = False) -> Path:
    """Create or verify a private directory without weakening an arbitrary parent."""
    directory = Path(path)
    existed = directory.exists()
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        _apply_windows_private_acl(directory, directory=True)
        return directory
    mode = stat.S_IMODE(directory.stat().st_mode)
    if mode & 0o077:
        if existed and not repair_existing:
            raise PermissionError("private storage directory is group/world accessible")
        os.chmod(directory, 0o700)
    if stat.S_IMODE(directory.stat().st_mode) & 0o077:
        raise PermissionError("private storage directory could not be secured")
    return directory


def restrict_private_file(path: str | Path, *, fd: int | None = None) -> None:
    """Apply and verify private-file permissions on POSIX and Windows.

    Windows has no usable ``os.fchmod``.  Its standard ``icacls`` utility is
    used to remove inherited ACLs; failure is explicit rather than silently
    leaving setup state readable by other accounts.
    """
    if os.name == "nt":
        _apply_windows_private_acl(path, directory=False)
        return
    fchmod = getattr(os, "fchmod", None)
    if fd is not None and fchmod is not None:
        fchmod(fd, 0o600)
        mode = stat.S_IMODE(os.fstat(fd).st_mode)
    else:
        os.chmod(path, 0o600)
        mode = stat.S_IMODE(Path(path).stat().st_mode)
    if mode & 0o077:
        raise PermissionError("private storage file could not be secured")


class ConfigStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else canonical_config_path()

    @classmethod
    def default_path(cls) -> Path:
        return canonical_config_path()

    def _prepare_parent(self) -> Path:
        return ensure_private_directory(
            self.path.parent,
            repair_existing=self.path == canonical_config_path(),
        )

    @staticmethod
    def new_config(installation_id: str) -> CanonicalConfig:
        return CanonicalConfig(installation_id=installation_id)

    def load(self) -> CanonicalConfig:
        return CanonicalConfig.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, config: CanonicalConfig) -> None:
        self._prepare_parent()
        payload = json.dumps(config.model_dump(mode="json", exclude_none=True), indent=2, sort_keys=True) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                restrict_private_file(temporary, fd=handle.fileno())
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if self.path.exists():
                shutil.copy2(self.path, self.backup_path)
                restrict_private_file(self.backup_path)
            os.replace(temporary, self.path)
            restrict_private_file(self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @property
    def backup_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.bak")

    def rollback(self) -> bool:
        if not self.backup_path.exists():
            return False
        os.replace(self.backup_path, self.path)
        return True

    def snapshot(self) -> dict[Path, tuple[bytes, int] | None]:
        """Capture canonical files before a coordinated setup transaction."""
        snapshot: dict[Path, tuple[bytes, int] | None] = {}
        for path in (self.path, self.backup_path):
            if path.exists():
                snapshot[path] = (path.read_bytes(), path.stat().st_mode & 0o777)
            else:
                snapshot[path] = None
        return snapshot

    def restore(self, snapshot: Mapping[Path, tuple[bytes, int] | None]) -> None:
        """Restore an in-memory canonical snapshot without creating a new backup."""
        for path, saved in snapshot.items():
            if saved is None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            payload, _mode = saved
            self._prepare_parent()
            fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.restore.", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
                restrict_private_file(path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)


class SetupState(StrictModel):
    config: CanonicalConfig
    completed_steps: tuple[str, ...] = ()


class SetupService:
    def __init__(self, store: ConfigStore, *, installation_id: str = "local"):
        self.store = store
        try:
            self._config = store.load()
        except FileNotFoundError:
            self._config = CanonicalConfig(installation_id=installation_id)

    def apply_profile(self, *, installation_id: str) -> SetupState:
        """Persist the installation identity used by all local components."""
        installation_id = installation_id.strip()
        if not is_safe_installation_id(installation_id):
            return SetupState(config=self._config)
        candidate = self._config.model_copy(update={"installation_id": installation_id})
        self.store.save(candidate)
        self._config = candidate
        return SetupState(config=candidate, completed_steps=("profile",))

    def prepare_setup(
        self,
        *,
        installation_id: str,
        mode: Literal["disabled", "bot", "user_session", "hybrid"],
        bot_token_ref: str | None = None,
        api_id: int | None = None,
        api_hash_ref: str | None = None,
        session_ref: str | None = None,
    ) -> CanonicalConfig | None:
        """Build a fully validated setup candidate without persisting it."""
        installation_id = installation_id.strip()
        if not is_safe_installation_id(installation_id):
            return None
        try:
            telegram = TelegramConfig(
                mode=mode,
                bot_token_ref=bot_token_ref,
                api_id=api_id,
                api_hash_ref=api_hash_ref,
                session_ref=session_ref,
            )
            return CanonicalConfig(
                schema_version=self._config.schema_version,
                installation_id=installation_id,
                telegram=telegram,
            )
        except ValidationError:
            return None

    def commit_setup(self, candidate: CanonicalConfig) -> SetupState:
        """Persist a candidate made by :meth:`prepare_setup` exactly once."""
        self.store.save(candidate)
        self._config = candidate
        return SetupState(config=candidate, completed_steps=("profile", "telegram"))

    def apply_setup(
        self,
        *,
        installation_id: str,
        mode: Literal["disabled", "bot", "user_session", "hybrid"],
        bot_token_ref: str | None = None,
        api_id: int | None = None,
        api_hash_ref: str | None = None,
        session_ref: str | None = None,
    ) -> SetupState:
        """Validate and persist the setup wizard's profile and Telegram data once."""
        candidate = self.prepare_setup(
            installation_id=installation_id,
            mode=mode,
            bot_token_ref=bot_token_ref,
            api_id=api_id,
            api_hash_ref=api_hash_ref,
            session_ref=session_ref,
        )
        if candidate is None:
            return SetupState(config=self._config)
        return self.commit_setup(candidate)

    def apply_telegram(
        self,
        *,
        mode: Literal["disabled", "bot", "user_session", "hybrid"],
        bot_token_ref: str | None = None,
        api_id: int | None = None,
        api_hash_ref: str | None = None,
        session_ref: str | None = None,
    ) -> SetupState:
        try:
            telegram = TelegramConfig(
                mode=mode,
                bot_token_ref=bot_token_ref,
                api_id=api_id,
                api_hash_ref=api_hash_ref,
                session_ref=session_ref,
            )
        except ValidationError:
            return SetupState(config=self._config)
        candidate = self._config.model_copy(update={"telegram": telegram})
        self.store.save(candidate)
        self._config = candidate
        return SetupState(config=candidate, completed_steps=("telegram",))
