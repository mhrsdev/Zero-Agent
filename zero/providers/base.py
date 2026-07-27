"""Normalized LLM provider contract.

A *profile* is a named, group-selectable configuration: which provider kind,
which model, which endpoint, which secret reference, and the operational limits
that go with it. Groups select a profile by name; they never hold a credential.
The secret stays a symbolic reference that is resolved once, at composition, by
whatever holds the secret store.

Only provider kinds with a real, tested implementation are registered. An
unimplemented kind is absent rather than advertised, so a profile cannot name a
provider that does not exist.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class ProviderKind(str, Enum):
    """Provider families with a real implementation in this release."""

    OPENAI_COMPATIBLE = "openai_compatible"
    GEMINI = "gemini"


#: Presets for the endpoints an OpenAI-compatible profile commonly targets.
#: They are conveniences over one implementation, not separate providers.
OPENAI_COMPATIBLE_PRESETS: dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
}

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{2,63}$")

#: A symbolic reference is a short dotted name such as ``provider.gemini``.
#: A real credential is a long opaque token, so any segment long enough to be
#: one is rejected outright rather than stored and later leaked.
_MAX_REF_SEGMENT = 24
_CREDENTIAL_PREFIXES = ("sk-", "sk_", "pk-", "ghp_", "gho_", "github_pat_", "aiza", "xoxb-", "xoxp-", "bearer")


def _is_symbolic_ref(value: str) -> bool:
    if not _REF.fullmatch(value):
        return False
    lowered = value.lower()
    if lowered.startswith(_CREDENTIAL_PREFIXES):
        return False
    if any(len(segment) > _MAX_REF_SEGMENT for segment in re.split(r"[._-]", value)):
        return False
    # A bot token is "<digits>:<opaque>"; a reference never contains a colon,
    # which _REF already forbids, so only the entropy check remains.
    return True


class ProviderError(RuntimeError):
    """A provider call failed. Never carries the credential or raw response."""


class ProviderUnavailable(ProviderError):
    """The provider is not reachable or not configured."""


@dataclass(frozen=True)
class ProviderProfile:
    """A named provider configuration a group can select."""

    name: str
    kind: ProviderKind
    model: str
    base_url: str = ""
    secret_ref: str | None = None
    timeout_seconds: float = 45.0
    context_limit: int = 8192
    max_retries: int = 1
    requests_per_minute: int | None = None
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name):
            raise ValueError(f"invalid profile name: {self.name!r}")
        if not str(self.model).strip():
            raise ValueError("profile requires a model")
        if self.secret_ref is not None and not _is_symbolic_ref(self.secret_ref):
            raise ValueError("secret_ref must be a symbolic name, not a credential")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.context_limit <= 0:
            raise ValueError("context_limit must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must not be negative")

    def redacted(self) -> dict[str, Any]:
        """A representation safe to log, return from an API, or show in a panel."""
        return {
            "name": self.name,
            "kind": self.kind.value,
            "model": self.model,
            "base_url": self.base_url,
            "secret_ref": self.secret_ref,
            "secret_configured": self.secret_ref is not None,
            "timeout_seconds": self.timeout_seconds,
            "context_limit": self.context_limit,
            "max_retries": self.max_retries,
            "requests_per_minute": self.requests_per_minute,
        }


@dataclass(frozen=True)
class CompletionRequest:
    prompt: str
    max_output_tokens: int = 700
    temperature: float = 0.7
    system: str = ""


@dataclass(frozen=True)
class CompletionResult:
    text: str
    profile: str
    model: str
    attempts: int = 1
    input_tokens: int = 0
    output_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def cost(self, profile: ProviderProfile) -> float:
        return (
            self.input_tokens / 1000 * profile.cost_per_1k_input
            + self.output_tokens / 1000 * profile.cost_per_1k_output
        )


@dataclass(frozen=True)
class ProviderHealth:
    profile: str
    healthy: bool
    detail: str = ""


class LLMProvider(Protocol):
    """What every provider implementation must offer."""

    profile: ProviderProfile

    async def complete(self, request: CompletionRequest) -> CompletionResult: ...

    async def health(self) -> ProviderHealth: ...
