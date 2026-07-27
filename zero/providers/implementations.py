"""Real provider implementations.

Two implementations cover the five target endpoints. OpenRouter, OpenAI, Ollama
and LM Studio all speak the same ``/v1/chat/completions`` contract, so they are
one implementation with different base URLs rather than four near-duplicates
that would each need their own tests to be honest about.

HTTP is injected. Nothing here opens a socket during tests, and the default
transport is the same stdlib-based one the rest of the codebase uses.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from .base import (
    CompletionRequest,
    CompletionResult,
    ProviderError,
    ProviderHealth,
    ProviderKind,
    ProviderProfile,
    ProviderUnavailable,
)

logger = logging.getLogger("zero.providers")

#: ``(url, payload, headers, timeout) -> response body`` as text.
HttpPost = Callable[[str, dict[str, Any], dict[str, str], float], Any]


def _redact(exc: BaseException) -> str:
    """Describe a failure by type only; bodies and headers may carry the key."""
    return type(exc).__name__


class OpenAICompatibleProvider:
    """Chat-completions provider for any OpenAI-compatible endpoint."""

    def __init__(self, profile: ProviderProfile, http_post: HttpPost, *, api_key: str = ""):
        if profile.kind is not ProviderKind.OPENAI_COMPATIBLE:
            raise ValueError(f"profile {profile.name} is not OpenAI-compatible")
        if not profile.base_url:
            raise ValueError(f"profile {profile.name} requires a base_url")
        self.profile = profile
        self._post = http_post
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            # Local runtimes such as Ollama and LM Studio accept no key; sending
            # an empty Authorization header would be rejected by some of them.
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _payload(self, request: CompletionRequest) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})
        return {
            "model": self.profile.model,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        url = self.profile.base_url.rstrip("/") + "/chat/completions"
        last: Exception | None = None
        for attempt in range(1, self.profile.max_retries + 2):
            try:
                raw = await self._post(url, self._payload(request), self._headers(), self.profile.timeout_seconds)
                document = json.loads(raw) if isinstance(raw, str) else raw
                choices = document.get("choices") or []
                if not choices:
                    raise ProviderError("provider returned no choices")
                text = str(((choices[0] or {}).get("message") or {}).get("content") or "")
                usage = document.get("usage") or {}
                return CompletionResult(
                    text=text,
                    profile=self.profile.name,
                    model=str(document.get("model") or self.profile.model),
                    attempts=attempt,
                    input_tokens=int(usage.get("prompt_tokens") or 0),
                    output_tokens=int(usage.get("completion_tokens") or 0),
                    metadata={"finish_reason": (choices[0] or {}).get("finish_reason", "")},
                )
            except Exception as exc:  # re-raised below, never swallowed
                last = exc
                logger.info(
                    "PROVIDER_ATTEMPT_FAILED profile=%s attempt=%d error=%s",
                    self.profile.name, attempt, _redact(exc),
                )
        raise ProviderError(f"{self.profile.name} failed after {self.profile.max_retries + 1} attempts: {_redact(last)}") from last

    async def health(self) -> ProviderHealth:
        try:
            result = await self.complete(CompletionRequest(prompt="ping", max_output_tokens=1))
        except ProviderError as exc:
            return ProviderHealth(self.profile.name, False, _redact(exc))
        return ProviderHealth(self.profile.name, bool(result.model), "ok")


class GeminiProvider:
    """Google Generative Language provider."""

    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, profile: ProviderProfile, http_post: HttpPost, *, api_key: str = ""):
        if profile.kind is not ProviderKind.GEMINI:
            raise ValueError(f"profile {profile.name} is not a Gemini profile")
        if not api_key:
            raise ProviderUnavailable(f"profile {profile.name} requires an API key")
        self.profile = profile
        self._post = http_post
        self._api_key = api_key

    def _url(self) -> str:
        base = (self.profile.base_url or self.DEFAULT_BASE_URL).rstrip("/")
        return f"{base}/models/{self.profile.model}:generateContent"

    def _payload(self, request: CompletionRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
            "generationConfig": {
                "maxOutputTokens": request.max_output_tokens,
                "temperature": request.temperature,
            },
        }
        if request.system:
            payload["systemInstruction"] = {"parts": [{"text": request.system}]}
        return payload

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        # The key travels as a header, never as a query parameter, so it cannot
        # be captured by intermediate logging of the URL.
        headers = {"Content-Type": "application/json", "x-goog-api-key": self._api_key}
        last: Exception | None = None
        for attempt in range(1, self.profile.max_retries + 2):
            try:
                raw = await self._post(self._url(), self._payload(request), headers, self.profile.timeout_seconds)
                document = json.loads(raw) if isinstance(raw, str) else raw
                candidates = document.get("candidates") or []
                if not candidates:
                    raise ProviderError("provider returned no candidates")
                parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
                text = "".join(str(part.get("text") or "") for part in parts)
                usage = document.get("usageMetadata") or {}
                return CompletionResult(
                    text=text,
                    profile=self.profile.name,
                    model=self.profile.model,
                    attempts=attempt,
                    input_tokens=int(usage.get("promptTokenCount") or 0),
                    output_tokens=int(usage.get("candidatesTokenCount") or 0),
                    metadata={"finish_reason": (candidates[0] or {}).get("finishReason", "")},
                )
            except Exception as exc:
                last = exc
                logger.info(
                    "PROVIDER_ATTEMPT_FAILED profile=%s attempt=%d error=%s",
                    self.profile.name, attempt, _redact(exc),
                )
        raise ProviderError(f"{self.profile.name} failed after {self.profile.max_retries + 1} attempts: {_redact(last)}") from last

    async def health(self) -> ProviderHealth:
        try:
            await self.complete(CompletionRequest(prompt="ping", max_output_tokens=1))
        except ProviderError as exc:
            return ProviderHealth(self.profile.name, False, _redact(exc))
        return ProviderHealth(self.profile.name, True, "ok")


#: Provider kinds that have a real implementation. A kind absent from this map
#: cannot be instantiated, so a profile can never name a provider that is only
#: aspirational.
IMPLEMENTATIONS = {
    ProviderKind.OPENAI_COMPATIBLE: OpenAICompatibleProvider,
    ProviderKind.GEMINI: GeminiProvider,
}
