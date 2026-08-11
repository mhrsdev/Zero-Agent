from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{2,127}$")


def canonical_config_path(env: Mapping[str, str] | None = None) -> Path:
    values = env if env is not None else os.environ
    return Path(values.get("ZERO_CANONICAL_CONFIG", "config/zero.json"))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TelegramConfig(StrictModel):
    mode: Literal["disabled", "bot", "user_session", "hybrid"] = "disabled"
    bot_token_ref: str | None = None
    api_id: int | None = Field(default=None, gt=0)
    api_hash_ref: str | None = None
    session_ref: str | None = None

    @model_validator(mode="after")
    def validate_transport(self) -> "TelegramConfig":
        refs = (self.bot_token_ref, self.api_hash_ref, self.session_ref)
        if any(value is not None and not _REF.fullmatch(value) for value in refs):
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


class ConfigStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else canonical_config_path()

    @classmethod
    def default_path(cls) -> Path:
        return canonical_config_path()

    @staticmethod
    def new_config(installation_id: str) -> CanonicalConfig:
        return CanonicalConfig(installation_id=installation_id)

    def load(self) -> CanonicalConfig:
        return CanonicalConfig.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, config: CanonicalConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(config.model_dump(mode="json", exclude_none=True), indent=2, sort_keys=True) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if self.path.exists():
                shutil.copy2(self.path, self.backup_path)
                os.chmod(self.backup_path, 0o600)
            os.replace(temporary, self.path)
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
        if not installation_id or len(installation_id) > 128:
            return SetupState(config=self._config)
        candidate = self._config.model_copy(update={"installation_id": installation_id})
        self.store.save(candidate)
        self._config = candidate
        return SetupState(config=candidate, completed_steps=("profile",))

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
