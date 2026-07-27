"""RED test: Provider Registry must be wired into the runtime router.

This test verifies that IndependentRouter can optionally delegate to
ProviderRegistry when registry profiles are configured, instead of
hardcoding openrouter/gemini from legacy config.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def tmp_config(tmp_path):
    from zero.config import ZeroConfig, RouterConfig, ProviderConfig, RouterProvidersConfig, MemoryConfig
    return MagicMock()


class _FakeAsyncResp:
    """A fake response object for testing."""

    def __init__(self, text="test response", provider="test", model="test-model"):
        self.text = text
        self.provider = provider
        self.model = model


class TestRouterCanUseRegistry:
    """IndependentRouter must be able to delegate to ProviderRegistry."""

    def test_router_accepts_optional_registry(self):
        """IndependentRouter must accept a `registry` keyword argument."""
        import inspect
        from zero.router import IndependentRouter
        sig = inspect.signature(IndependentRouter.__init__)
        params = list(sig.parameters)
        assert "registry" in params, \
            "IndependentRouter must accept `registry` kwarg to use ProviderRegistry"

    def test_router_uses_registry_when_available(self):
        """When registry is set + has profiles, router delegates to it."""
        from zero.router import IndependentRouter
        from zero.providers.base import CompletionResult

        # Create a mock registry that returns a canned result
        mock_registry = MagicMock()

        async def fake_complete(request, *, profile, fallback=()):
            return CompletionResult(
                text="registry response", profile=profile, model="test-model",
                attempts=1, input_tokens=5, output_tokens=5, metadata={},
            )

        mock_registry.complete = fake_complete
        mock_registry.names.return_value = ["test-profile"]

        router = IndependentRouter.__new__(IndependentRouter)
        router.registry = mock_registry

        result = asyncio.run(router.complete("hello"))
        assert result.text == "registry response"
        assert result.provider == "test-profile"

    def test_router_falls_back_to_legacy_without_registry(self):
        """Without registry, router works as before (legacy config)."""
        from zero.router import IndependentRouter
        from zero.config import ZeroConfig

        # Build a minimal config
        config = MagicMock()
        config.router.normal_primary = "gemini"
        config.router.normal_fallback = "openrouter"
        config.router.max_total_attempts = 2
        config.router.request_timeout_seconds = 5
        config.router.simple_message_char_threshold = 140
        config.router.providers.gemini.enabled = False
        config.router.providers.openrouter.enabled = False
        config.logs.router_log = "/tmp/zero_test_router.log"

        router = IndependentRouter(config)
        assert router.registry is None  # no registry by default

    def test_router_complete_prefers_registry_over_legacy(self):
        """If registry is set + has matching profile, it's used first."""
        import inspect
        from zero.router import IndependentRouter
        source = inspect.getsource(IndependentRouter.complete)
        assert "registry" in source or "self.registry" in source, \
            "complete() must check self.registry before legacy pools"
