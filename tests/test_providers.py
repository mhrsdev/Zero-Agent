"""Normalized provider contract: profiles, routing, limits and redaction."""
from __future__ import annotations

import asyncio
import json

import pytest

from zero.providers import (
    OPENAI_COMPATIBLE_PRESETS,
    CompletionRequest,
    GeminiProvider,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderKind,
    ProviderProfile,
    ProviderRegistry,
    ProviderUnavailable,
    RateLimiter,
)


class RecordingPost:
    """Stands in for HTTP. Records every call; opens no socket."""

    def __init__(self, response=None, fail_times: int = 0):
        self.calls: list[dict] = []
        self.response = response or {"choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
                                     "model": "m", "usage": {"prompt_tokens": 3, "completion_tokens": 5}}
        self.fail_times = fail_times

    async def __call__(self, url, payload, headers, timeout):
        self.calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("transient")
        return json.dumps(self.response)


def openai_profile(**kwargs):
    defaults = dict(
        name="fast", kind=ProviderKind.OPENAI_COMPATIBLE, model="test-model",
        base_url=OPENAI_COMPATIBLE_PRESETS["openrouter"], secret_ref="provider.openrouter",
    )
    defaults.update(kwargs)
    return ProviderProfile(**defaults)


# ---- profile validation ------------------------------------------------


def test_profile_rejects_a_credential_in_place_of_a_reference():
    """Groups select profiles; a profile must never carry the secret itself."""
    with pytest.raises(ValueError):
        openai_profile(secret_ref="sk-live-0123456789abcdef0123456789")


def test_profile_rejects_invalid_names_and_limits():
    for bad in ("", "Has Space", "UPPER", "a" * 65):
        with pytest.raises(ValueError):
            openai_profile(name=bad)
    with pytest.raises(ValueError):
        openai_profile(model="")
    with pytest.raises(ValueError):
        openai_profile(timeout_seconds=0)
    with pytest.raises(ValueError):
        openai_profile(context_limit=0)
    with pytest.raises(ValueError):
        openai_profile(max_retries=-1)


def test_redacted_profile_never_exposes_a_secret():
    profile = openai_profile()
    redacted = profile.redacted()
    assert redacted["secret_ref"] == "provider.openrouter"
    assert redacted["secret_configured"] is True
    # The reference is symbolic; no field carries a resolved value.
    assert "api_key" not in redacted and "secret" not in redacted


# ---- OpenAI-compatible implementation ----------------------------------


def test_openai_compatible_sends_chat_completions_with_bearer_auth():
    post = RecordingPost()
    provider = OpenAICompatibleProvider(openai_profile(), post, api_key="test-key")
    result = asyncio.run(provider.complete(CompletionRequest(prompt="hi", system="be brief")))

    call = post.calls[0]
    assert call["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer test-key"
    assert [m["role"] for m in call["payload"]["messages"]] == ["system", "user"]
    assert result.text == "hello"
    assert result.input_tokens == 3 and result.output_tokens == 5
    # The key authenticates via header only and must not reach the URL.
    assert "test-key" not in call["url"]


def test_openai_compatible_omits_authorization_when_no_key_is_configured():
    """Local runtimes such as Ollama and LM Studio accept no credential."""
    post = RecordingPost()
    provider = OpenAICompatibleProvider(
        openai_profile(name="local", base_url=OPENAI_COMPATIBLE_PRESETS["ollama"], secret_ref=None),
        post,
    )
    asyncio.run(provider.complete(CompletionRequest(prompt="hi")))
    assert "Authorization" not in post.calls[0]["headers"]


def test_openai_compatible_retries_then_surfaces_a_redacted_failure():
    post = RecordingPost(fail_times=5)
    provider = OpenAICompatibleProvider(openai_profile(max_retries=2), post, api_key="secret-key")
    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.complete(CompletionRequest(prompt="hi")))
    assert len(post.calls) == 3  # one attempt plus two retries
    assert "secret-key" not in str(error.value)


def test_openai_compatible_rejects_an_empty_choice_list():
    provider = OpenAICompatibleProvider(openai_profile(max_retries=0), RecordingPost(response={"choices": []}), api_key="k")
    with pytest.raises(ProviderError):
        asyncio.run(provider.complete(CompletionRequest(prompt="hi")))


def test_openai_compatible_requires_a_base_url_and_matching_kind():
    with pytest.raises(ValueError):
        OpenAICompatibleProvider(openai_profile(base_url=""), RecordingPost())
    gemini = ProviderProfile(name="g", kind=ProviderKind.GEMINI, model="gemini-x")
    with pytest.raises(ValueError):
        OpenAICompatibleProvider(gemini, RecordingPost())


# ---- Gemini implementation ---------------------------------------------


def gemini_response():
    return {
        "candidates": [{"content": {"parts": [{"text": "grounded"}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 6},
    }


def test_gemini_sends_the_key_as_a_header_never_in_the_url():
    post = RecordingPost(response=gemini_response())
    profile = ProviderProfile(name="quality", kind=ProviderKind.GEMINI, model="gemini-test", secret_ref="provider.gemini")
    provider = GeminiProvider(profile, post, api_key="gemini-secret")
    result = asyncio.run(provider.complete(CompletionRequest(prompt="hi")))

    call = post.calls[0]
    assert call["headers"]["x-goog-api-key"] == "gemini-secret"
    assert "gemini-secret" not in call["url"]
    assert call["url"].endswith("/models/gemini-test:generateContent")
    assert result.text == "grounded" and result.output_tokens == 6


def test_gemini_refuses_to_construct_without_a_key():
    profile = ProviderProfile(name="quality", kind=ProviderKind.GEMINI, model="gemini-test")
    with pytest.raises(ProviderUnavailable):
        GeminiProvider(profile, RecordingPost(), api_key="")


# ---- registry ----------------------------------------------------------


def registry_with(*profiles, post=None, secrets=None):
    resolver = (lambda ref: (secrets or {}).get(ref, "resolved-key"))
    registry = ProviderRegistry(post or RecordingPost(), secret_resolver=resolver)
    for profile in profiles:
        registry.register(profile)
    return registry


def test_registry_refuses_a_kind_without_an_implementation():
    """A profile can never name a provider that is only aspirational."""
    from zero.providers import implementations

    registry = ProviderRegistry(RecordingPost())
    original = dict(implementations.IMPLEMENTATIONS)
    try:
        implementations.IMPLEMENTATIONS.pop(ProviderKind.GEMINI)
        with pytest.raises(ProviderUnavailable):
            registry.register(ProviderProfile(name="g", kind=ProviderKind.GEMINI, model="m"))
    finally:
        implementations.IMPLEMENTATIONS.clear()
        implementations.IMPLEMENTATIONS.update(original)


def test_registry_rejects_duplicate_profile_names():
    registry = registry_with(openai_profile())
    with pytest.raises(ValueError):
        registry.register(openai_profile())


def test_registry_describe_is_safe_to_return_from_an_api():
    registry = registry_with(openai_profile(), secrets={"provider.openrouter": "super-secret"})
    payload = json.dumps(registry.describe())
    assert "super-secret" not in payload
    assert "provider.openrouter" in payload


def test_fallback_chain_moves_to_the_next_profile_on_failure():
    failing = RecordingPost(fail_times=99)
    working = RecordingPost()
    registry = ProviderRegistry(failing, secret_resolver=lambda ref: "k")
    registry.register(openai_profile(name="primary", max_retries=0))
    registry.register(openai_profile(name="secondary", max_retries=0))
    # Point only the secondary at a working transport.
    registry._providers["secondary"] = OpenAICompatibleProvider(registry.profile("secondary"), working, api_key="k")

    result = asyncio.run(registry.complete(CompletionRequest(prompt="hi"), profile="primary", fallback=("secondary",)))
    assert result.profile == "secondary"
    assert result.metadata["fallback_from"] == "primary"


def test_exhausted_fallback_chain_raises_rather_than_returning_empty_text():
    registry = registry_with(openai_profile(name="a", max_retries=0), openai_profile(name="b", max_retries=0),
                             post=RecordingPost(fail_times=99))
    with pytest.raises(ProviderError):
        asyncio.run(registry.complete(CompletionRequest(prompt="hi"), profile="a", fallback=("b",)))


def test_unknown_profile_is_rejected():
    registry = registry_with(openai_profile())
    with pytest.raises(ProviderUnavailable):
        registry.profile("missing")
    with pytest.raises(ProviderError):
        asyncio.run(registry.complete(CompletionRequest(prompt="hi"), profile="missing"))


def test_rate_limiter_bounds_requests_per_minute():
    limiter = RateLimiter(2)
    assert limiter.allow(now=0.0) and limiter.allow(now=1.0)
    assert not limiter.allow(now=2.0)
    # The window slides: an hour later the budget is free again.
    assert limiter.allow(now=120.0)


def test_rate_limiter_without_a_limit_always_allows():
    limiter = RateLimiter(None)
    assert all(limiter.allow(now=float(i)) for i in range(100))


def test_rate_limited_profile_falls_through_to_the_next():
    registry = registry_with(openai_profile(name="capped", requests_per_minute=1),
                             openai_profile(name="spare"))
    first = asyncio.run(registry.complete(CompletionRequest(prompt="hi"), profile="capped", fallback=("spare",)))
    assert first.profile == "capped"
    second = asyncio.run(registry.complete(CompletionRequest(prompt="hi"), profile="capped", fallback=("spare",)))
    assert second.profile == "spare"


def test_cost_accounting_tracks_tokens_and_money_per_profile():
    registry = registry_with(openai_profile(cost_per_1k_input=1.0, cost_per_1k_output=2.0))
    asyncio.run(registry.complete(CompletionRequest(prompt="hi"), profile="fast"))
    usage = registry.usage("fast")
    assert usage["requests"] == 1
    assert usage["input_tokens"] == 3 and usage["output_tokens"] == 5
    # 3/1000 * 1.0 + 5/1000 * 2.0
    assert usage["cost"] == pytest.approx(0.013)


def test_usage_is_tracked_separately_per_profile():
    registry = registry_with(openai_profile(name="a"), openai_profile(name="b"))
    asyncio.run(registry.complete(CompletionRequest(prompt="hi"), profile="a"))
    assert registry.usage("a")["requests"] == 1
    assert registry.usage("b")["requests"] == 0


def test_health_reports_every_registered_profile():
    registry = registry_with(openai_profile(name="a"), openai_profile(name="b"))
    report = {h.profile: h.healthy for h in asyncio.run(registry.health())}
    assert report == {"a": True, "b": True}


def test_health_reports_unhealthy_without_raising():
    registry = registry_with(openai_profile(name="a", max_retries=0), post=RecordingPost(fail_times=99))
    report = asyncio.run(registry.health())
    assert report[0].healthy is False and report[0].profile == "a"


def test_only_implemented_provider_kinds_are_exposed():
    """The public enum must not advertise a provider without an implementation."""
    from zero.providers import IMPLEMENTATIONS

    assert set(ProviderKind) == set(IMPLEMENTATIONS)
