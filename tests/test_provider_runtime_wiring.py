"""Provider Registry → runtime wiring tests.

Verifies that the ProviderRegistry is or can be connected to the real runtime,
not just tested in isolation. The registry must:
- Accept profiles with symbolic secret_refs (not raw credentials)
- Route completions through registered providers
- Support fallback chains
- Track health and cost per profile
- Be group-selectable (provider_profile is a group setting in tenancy)
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from zero.providers.base import (
    CompletionRequest, CompletionResult, ProviderProfile,
    ProviderKind, ProviderUnavailable, _is_symbolic_ref,
)
from zero.providers.registry import ProviderRegistry, RateLimiter


class TestProviderRegistryContract:
    """The ProviderRegistry must be a real, usable runtime component."""

    def test_provider_profile_uses_symbolic_secret_ref(self):
        """A credential must never be accepted as a secret_ref."""
        assert _is_symbolic_ref("provider.gemini") is True
        assert _is_symbolic_ref("sk-1234567890abcdef") is False
        assert _is_symbolic_ref("xoxb-1234567890") is False
        assert _is_symbolic_ref("ghp_abcdef1234567890") is False

    def test_registry_routes_through_registered_provider(self):
        async def fake_post(url, payload, headers, timeout):
            return {"choices": [{"message": {"content": "hello"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 5}}

        registry = ProviderRegistry(fake_post, secret_resolver=lambda ref: "fake-key")
        profile = ProviderProfile(
            name="test-profile",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            model="test-model",
            base_url="https://api.test.com/v1",
            secret_ref="provider.test",
        )
        registry.register(profile)
        result = asyncio.run(registry.complete(
            CompletionRequest(prompt="hello", max_output_tokens=100),
            profile="test-profile",
        ))
        assert result.text == "hello"

    def test_unknown_profile_raises(self):
        registry = ProviderRegistry(lambda *a, **kw: None)
        with pytest.raises(Exception):  # ProviderError or ProviderUnavailable
            asyncio.run(registry.complete(
                CompletionRequest(prompt="hello"), profile="nonexistent",
            ))

    def test_registry_describe_is_safe_to_return(self):
        registry = ProviderRegistry(lambda *a, **kw: None, secret_resolver=lambda r: "secret-key")
        profile = ProviderProfile(
            name="safe-profile",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            model="m",
            base_url="https://api.test.com/v1",
            secret_ref="provider.test",
        )
        registry.register(profile)
        desc = registry.describe()
        assert len(desc) == 1
        assert "secret-key" not in str(desc)
        assert "fake-key" not in str(desc)

    def test_provider_profile_is_group_selectable(self):
        """provider_profile is a recognized group setting key in the tenancy registry."""
        from zero.tenancy.registry import GROUP_SETTING_KEYS
        assert "provider_profile" in GROUP_SETTING_KEYS, \
            "group_settings must include provider_profile so groups can select their provider"

    def test_rate_limiter_bounds_requests(self):
        limiter = RateLimiter(requests_per_minute=3)
        assert limiter.allow(0) is True
        assert limiter.allow(1) is True
        assert limiter.allow(2) is True
        assert limiter.allow(3) is False
        # After 60s, should allow again
        assert limiter.allow(61) is True

    def test_fallback_chain_moves_to_next_profile(self):
        call_count = [0]

        async def fake_post(url, payload, headers, timeout):
            call_count[0] += 1
            if call_count[0] <= 2:  # primary fails both attempts (max_retries=1)
                raise RuntimeError("first provider failed")
            return {"choices": [{"message": {"content": "fallback ok"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}

        registry = ProviderRegistry(fake_post, secret_resolver=lambda r: "k")
        for name in ("primary", "secondary"):
            registry.register(ProviderProfile(
                name=name, kind=ProviderKind.OPENAI_COMPATIBLE, model="m",
                base_url="https://api.test.com/v1", secret_ref="provider.test",
            ))
        result = asyncio.run(registry.complete(
            CompletionRequest(prompt="hello"), profile="primary", fallback=("secondary",),
        ))
        assert result.text == "fallback ok"
        assert result.profile == "secondary"
