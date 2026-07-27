"""Profile registry, fallback chain, rate limiting and cost accounting."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Callable

from .base import (
    CompletionRequest,
    CompletionResult,
    LLMProvider,
    ProviderError,
    ProviderHealth,
    ProviderProfile,
    ProviderUnavailable,
)
from .implementations import IMPLEMENTATIONS

logger = logging.getLogger("zero.providers")

#: ``secret_ref -> secret`` resolution, owned by whoever holds the secret store.
SecretResolver = Callable[[str], str]


class RateLimiter:
    """Requests-per-minute limiter, one window per profile."""

    def __init__(self, requests_per_minute: int | None):
        self.limit = requests_per_minute
        self._hits: deque[float] = deque()

    def allow(self, now: float | None = None) -> bool:
        if not self.limit:
            return True
        current = now if now is not None else time.monotonic()
        while self._hits and self._hits[0] <= current - 60:
            self._hits.popleft()
        if len(self._hits) >= self.limit:
            return False
        self._hits.append(current)
        return True


class ProviderRegistry:
    """Builds providers from profiles and routes completions through them."""

    def __init__(self, http_post: Any, *, secret_resolver: SecretResolver | None = None):
        self._http_post = http_post
        self._resolve = secret_resolver or (lambda ref: "")
        self._profiles: dict[str, ProviderProfile] = {}
        self._providers: dict[str, LLMProvider] = {}
        self._limiters: dict[str, RateLimiter] = {}
        self._usage: dict[str, dict[str, float]] = {}

    # ---- registration --------------------------------------------------

    def register(self, profile: ProviderProfile) -> None:
        if profile.name in self._profiles:
            raise ValueError(f"duplicate provider profile: {profile.name}")
        implementation = IMPLEMENTATIONS.get(profile.kind)
        if implementation is None:
            raise ProviderUnavailable(f"provider kind {profile.kind.value} has no implementation")
        api_key = self._resolve(profile.secret_ref) if profile.secret_ref else ""
        self._providers[profile.name] = implementation(profile, self._http_post, api_key=api_key)
        self._profiles[profile.name] = profile
        self._limiters[profile.name] = RateLimiter(profile.requests_per_minute)

    def names(self) -> list[str]:
        return sorted(self._profiles)

    def profile(self, name: str) -> ProviderProfile:
        try:
            return self._profiles[name]
        except KeyError as exc:
            raise ProviderUnavailable(f"unknown provider profile: {name}") from exc

    def describe(self) -> list[dict[str, Any]]:
        """Redacted profiles, safe to return from the Admin API or panel."""
        return [self._profiles[name].redacted() for name in self.names()]

    # ---- routing -------------------------------------------------------

    async def complete(self, request: CompletionRequest, *, profile: str, fallback: tuple[str, ...] = ()) -> CompletionResult:
        """Complete through ``profile``, falling back through ``fallback`` in order."""
        chain = [profile, *[name for name in fallback if name != profile]]
        last: Exception | None = None
        for name in chain:
            provider = self._providers.get(name)
            if provider is None:
                last = ProviderUnavailable(f"unknown provider profile: {name}")
                continue
            if not self._limiters[name].allow():
                logger.info("PROVIDER_RATE_LIMITED profile=%s", name)
                last = ProviderUnavailable(f"{name} is rate limited")
                continue
            try:
                result = await provider.complete(request)
            except ProviderError as exc:
                last = exc
                logger.info("PROVIDER_FAILED profile=%s error=%s", name, type(exc).__name__)
                continue
            self._record(name, result)
            if name != profile:
                result.metadata["fallback_from"] = profile
            return result
        raise ProviderError(f"all provider profiles failed: {chain}") from last

    def _record(self, name: str, result: CompletionResult) -> None:
        bucket = self._usage.setdefault(name, {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0})
        bucket["requests"] += 1
        bucket["input_tokens"] += result.input_tokens
        bucket["output_tokens"] += result.output_tokens
        bucket["cost"] += result.cost(self._profiles[name])

    def usage(self, name: str | None = None) -> dict[str, Any]:
        if name is None:
            return {key: dict(value) for key, value in self._usage.items()}
        return dict(self._usage.get(name, {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0}))

    # ---- health --------------------------------------------------------

    async def health(self) -> list[ProviderHealth]:
        results = await asyncio.gather(
            *(self._providers[name].health() for name in self.names()),
            return_exceptions=True,
        )
        report: list[ProviderHealth] = []
        for name, outcome in zip(self.names(), results):
            if isinstance(outcome, BaseException):
                report.append(ProviderHealth(name, False, type(outcome).__name__))
            else:
                report.append(outcome)
        return report
