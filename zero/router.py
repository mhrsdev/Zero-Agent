from __future__ import annotations

import asyncio
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import ProviderConfig, ZeroConfig
from .logging_utils import setup_logger
from .models import RouteResult


RATE_LIMIT_ERRORS = {"PER_KEY_AUTH_ERROR", "PER_MODEL_RATE_LIMIT", "PROJECT_QUOTA_EXHAUSTED", "PROVIDER_CAPACITY", "DAILY_LIMIT", "MINUTE_LIMIT", "TOKEN_LIMIT", "UNKNOWN_RATE_LIMIT"}


def key_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


@dataclass(slots=True)
class KeyState:
    key_id: str
    provider: str
    quota_scope: str = "unknown"
    enabled: bool = True
    healthy: bool = True
    cooldown_until: float = 0.0
    last_used_at: float = 0.0
    requests_minute: list[float] = field(default_factory=list)
    requests_day: int = 0
    day_key: str = ""
    consecutive_failures: int = 0
    last_error_type: str = ""
    last_success_at: float = 0.0
    weight: int = 1
    priority: int = 0

    def available(self, cfg: ProviderConfig, now: float, day: str) -> bool:
        if self.day_key != day:
            self.day_key, self.requests_day = day, 0
        self.requests_minute[:] = [x for x in self.requests_minute if now - x < 60]
        return self.enabled and self.healthy and self.cooldown_until <= now and (not cfg.rpm or len(self.requests_minute) < cfg.rpm) and (not cfg.rpd or self.requests_day < cfg.rpd)


class KeyPool:
    """Provider-local weighted LRU pool. Secrets never enter state or logs."""
    def __init__(self, provider: str, cfg: ProviderConfig):
        self.provider, self.cfg = provider, cfg
        self.states = [KeyState(key_id(key_id(k)), provider, getattr(cfg, "quota_scope", "unknown"), weight=max(1, getattr(cfg, "weight", 1))) for k in cfg.keys if k]
        self._secrets = {s.key_id: k for s, k in zip(self.states, [k for k in cfg.keys if k])}
        self._lock = asyncio.Lock()

    @staticmethod
    def _day() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    async def reserve(self) -> tuple[KeyState, str] | None:
        async with self._lock:
            now = time.time()
            candidates = [s for s in self.states if s.available(self.cfg, now, self._day())]
            if not candidates:
                return None
            # Headroom first, then least recent use, then weight. No blind RR.
            def score(s: KeyState) -> tuple[float, float, float]:
                minute_headroom = 1.0 if not self.cfg.rpm else max(0.0, 1 - len(s.requests_minute) / self.cfg.rpm)
                day_headroom = 1.0 if not self.cfg.rpd else max(0.0, 1 - s.requests_day / self.cfg.rpd)
                return (minute_headroom * .6 + day_headroom * .4, -s.last_used_at, float(s.weight))
            state = max(candidates, key=score)
            state.requests_minute.append(now)
            state.requests_day += 1
            state.last_used_at = now
            return state, self._secrets[state.key_id]

    def success(self, state: KeyState) -> None:
        state.healthy, state.consecutive_failures, state.last_error_type, state.last_success_at = True, 0, "", time.time()

    def failure(self, state: KeyState, error_type: str, *, cooldown: float = 0) -> None:
        state.consecutive_failures += 1
        state.last_error_type = error_type
        state.cooldown_until = max(state.cooldown_until, time.time() + cooldown)
        if error_type == "PER_KEY_AUTH_ERROR":
            state.enabled = state.healthy = False
        elif state.consecutive_failures >= 3:
            state.healthy = False
            state.cooldown_until = max(state.cooldown_until, time.time() + 60)

    def status(self) -> list[dict[str, Any]]:
        now = time.time()
        return [{"label": f"{self.provider.title()} Key #{i + 1}", "key_id": s.key_id, "enabled": s.enabled, "healthy": s.healthy, "cooldown": max(0, int(s.cooldown_until - now)), "quota_scope": s.quota_scope, "last_error_type": s.last_error_type} for i, s in enumerate(self.states)]


class IndependentRouter:
    def __init__(self, config: ZeroConfig):
        self.config = config
        self.logger = setup_logger("zero.router", config.logs.router_log)
        self.pools = {name: KeyPool(name, getattr(config.router.providers, name)) for name in ("openrouter", "gemini")}
        self._states = {}  # compatibility for older callers/tests
        self.last_route: dict[str, Any] = {}

    @property
    def keys(self) -> list[str]:
        return [secret for pool in self.pools.values() for secret in pool._secrets.values()]

    @property
    def gemini_keys(self) -> list[str]:
        """Keys valid for direct Google/Gemini endpoints only."""
        return list(self.pools['gemini']._secrets.values())

    def _route_order(self, prompt: str) -> list[tuple[str, ProviderConfig]]:
        # Compatibility inspection only. Actual normal-chat policy is always OR -> Google.
        p = self.config.router.providers
        return [("openrouter", p.openrouter), ("gemini", p.gemini)] if len(prompt) <= self.config.router.simple_message_char_threshold else [("gemini", p.gemini), ("openrouter", p.openrouter)]

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        if isinstance(exc, urllib.error.HTTPError):
            if exc.code in (401, 403): return "PER_KEY_AUTH_ERROR"
            if exc.code == 429: return "UNKNOWN_RATE_LIMIT"
            if 500 <= exc.code < 600: return "PROVIDER_CAPACITY"
            return "INVALID_REQUEST"
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError, urllib.error.URLError)): return "NETWORK_TIMEOUT"
        return type(exc).__name__.upper()

    def _google_call(self, model: str, key: str, prompt: str, max_output_tokens: int, *, search: bool = False, tools: list[dict[str, Any]] | None = None) -> tuple[str, dict[str, Any]]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model)}:generateContent?key={urllib.parse.quote(key)}"
        payload: dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"temperature": .45, "maxOutputTokens": max_output_tokens}}
        if search: payload["tools"] = [{"google_search": {}}]
        elif tools: payload["tools"] = [{"function_declarations": tools}]
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.config.router.request_timeout_seconds) as response:
            data = json.loads(response.read().decode())
        text = "".join(p.get("text", "") for p in data.get("candidates", [{}])[0].get("content", {}).get("parts", [])).strip()
        return text, data

    def _openrouter_call(self, model: str, key: str, prompt: str, max_output_tokens: int, *, tools: list[dict[str, Any]] | None = None) -> tuple[str, dict[str, Any]]:
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": .45, "max_tokens": max_output_tokens}
        if tools:
            payload["tools"] = [{"type": "function", "function": tool} for tool in tools]
            payload["tool_choice"] = "auto"
        req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}", "HTTP-Referer": "https://zero.local/", "X-Title": "Zero"})
        with urllib.request.urlopen(req, timeout=self.config.router.request_timeout_seconds) as response:
            data = json.loads(response.read().decode())
        return (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip(), data

    async def _complete_provider(self, provider: str, prompt: str, max_output_tokens: int, *, model: str | None = None, search: bool = False, tools: list[dict[str, Any]] | None = None) -> RouteResult:
        cfg = getattr(self.config.router.providers, provider)
        if not cfg.enabled: return RouteResult(text="", provider=provider, model=model or cfg.model, attempts=0, metadata={"error": "disabled"})
        pool = self.pools[provider]
        reservation = await pool.reserve()
        if not reservation: return RouteResult(text="", provider=provider, model=model or cfg.model, attempts=0, metadata={"error": "no_capacity"})
        state, secret = reservation
        chosen_model = model or cfg.model
        try:
            call: Callable[..., tuple[str, dict[str, Any]]] = self._google_call if provider == "gemini" else self._openrouter_call
            kwargs = {"search": search} if provider == "gemini" else {}
            if tools: kwargs["tools"] = tools
            text, raw = await asyncio.to_thread(call, chosen_model, secret, prompt, max_output_tokens, **kwargs)
            tool_calls = []
            if tools:
                if provider == 'gemini':
                    parts = raw.get('candidates', [{}])[0].get('content', {}).get('parts', [])
                    tool_calls = [{'name': p['functionCall'].get('name', ''), 'arguments': p['functionCall'].get('args', {})} for p in parts if p.get('functionCall')]
                else:
                    tool_calls = [{'id': x.get('id', ''), 'name': x.get('function', {}).get('name', ''), 'arguments': json.loads(x.get('function', {}).get('arguments', '{}') or '{}')} for x in raw.get('choices', [{}])[0].get('message', {}).get('tool_calls', [])]
            if not text and not tool_calls: raise ValueError("EMPTY_RESPONSE")
            pool.success(state)
            self.last_route = {"provider": provider, "model": chosen_model, "key_id": state.key_id, "quota_scope": state.quota_scope, "search": search, "fallback": False}
            self.logger.info("ROUTE_OK provider=%s model=%s key_id=%s capability=%s", provider, chosen_model, state.key_id, "live_web_search" if search else "normal_chat")
            return RouteResult(text=text, provider=provider, model=chosen_model, attempts=1, metadata={"key_id": state.key_id, "quota_scope": state.quota_scope, "raw": raw if search else {}, "tool_calls": tool_calls})
        except Exception as exc:
            kind = self._classify_error(exc)
            cooldown = 65 if kind in RATE_LIMIT_ERRORS else 0
            pool.failure(state, kind, cooldown=cooldown)
            self.logger.warning("ROUTE_FAIL provider=%s model=%s key_id=%s error_type=%s cooldown=%s", provider, chosen_model, state.key_id, kind, int(cooldown))
            return RouteResult(text="", provider=provider, model=chosen_model, attempts=1, metadata={"error": kind, "key_id": state.key_id})

    async def complete(self, prompt: str, *, max_output_tokens: int = 700) -> RouteResult:
        configured = [self.config.router.normal_primary, self.config.router.normal_fallback]
        order = []
        for provider in configured:
            if provider in self.pools and provider not in order:
                order.append(provider)
        if not order:
            order = ['gemini', 'openrouter']
        last = RouteResult(text='', provider='fallback', model='none', attempts=0, metadata={})
        max_attempts = max(1, min(len(order), int(self.config.router.max_total_attempts or len(order))))
        for provider in order[:max_attempts]:
            result = await self._complete_provider(provider, prompt, max_output_tokens)
            last = result
            if result.text:
                if provider != order[0]:
                    result.metadata['fallback_used'] = True
                    self.last_route['fallback'] = True
                return result
        return RouteResult(text="الان پاسخ‌گویی در دسترس نیست؛ کمی بعد دوباره امتحان کن.", provider="fallback", model="none", attempts=last.attempts, metadata={"error": last.metadata.get("error", "unavailable"), "fallback_used": True})

    async def complete_with_tools(self, prompt: str, tools: list[dict[str, Any]], *, max_output_tokens: int = 700) -> RouteResult:
        configured = [self.config.router.normal_primary, self.config.router.normal_fallback]
        order = []
        for provider in configured:
            if provider in self.pools and provider not in order:
                order.append(provider)
        if not order: order = ['gemini', 'openrouter']
        last = RouteResult(text='', provider='fallback', model='none', attempts=0, metadata={})
        max_attempts = max(1, min(len(order), int(self.config.router.max_total_attempts or len(order))))
        for provider in order[:max_attempts]:
            result = await self._complete_provider(provider, prompt, max_output_tokens, tools=tools)
            last = result
            if result.text or result.metadata.get('tool_calls'):
                if provider != order[0]: result.metadata['fallback_used'] = True
                return result
        return last

    async def complete_structured(self, prompt: str, *, max_output_tokens: int = 850) -> RouteResult:
        for provider in ("gemini", "openrouter"):
            result = await self._complete_provider(provider, prompt, max_output_tokens)
            if result.text:
                return result
        return RouteResult(text="", provider="structured_failure", model="none", attempts=1, metadata={"error": "unavailable"})

    async def complete_search(self, prompt: str, *, max_output_tokens: int = 900) -> RouteResult:
        # Web grounding is Google-only. Exhaust every configured Google key before
        # declaring the search unavailable; a fixed two-attempt loop can skip a
        # healthy third key after two independent 429 responses.
        result = RouteResult(text="", provider="gemini", model=self.config.router.providers.gemini.model, attempts=0, metadata={})
        max_attempts = max(1, len(self.pools["gemini"].states))
        for attempt in range(max_attempts):
            if attempt:
                self.logger.info("GOOGLE_SEARCH_KEY_SWITCH attempt=%s/%s", attempt + 1, max_attempts)
            result = await self._complete_provider("gemini", prompt, max_output_tokens, search=True)
            if result.text and result.metadata.get("raw", {}).get("candidates", [{}])[0].get("groundingMetadata"):
                return result
            if result.text:
                result.text = ""
                result.metadata["error"] = "GROUNDING_METADATA_MISSING"
                return result
            if result.metadata.get("error") == "GROUNDING_METADATA_MISSING":
                return result
        return result

    def status(self) -> dict[str, Any]:
        return {"providers": {name: {"enabled": getattr(self.config.router.providers, name).enabled, "model": getattr(self.config.router.providers, name).model, "keys": pool.status()} for name, pool in self.pools.items()}, "last_route": dict(self.last_route)}
