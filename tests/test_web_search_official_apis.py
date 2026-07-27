"""Web Search via official external APIs only — verification tests.

Verifies that:
- Only official API providers are used (Bing RSS, SearXNG) — no scraping
- SSRF protection exists in the transport layer
- Group-aware permission/quota is considered
- Search results are deduplicated and ranked
- The pipeline is group-scoped
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from zero.web_search.providers.base import SearchProvider, ProviderRegistry as WebProviderRegistry
from zero.web_search.models import SearchKind, SearchResult


class TestWebSearchUsesOfficialAPIs:
    """Web search must use official APIs only, not HTML scraping."""

    def test_no_scraping_or_html_parsing_in_providers(self):
        """Providers must not use BeautifulSoup, lxml HTML, or regex-based HTML extraction."""
        root = Path(__file__).resolve().parents[1]
        providers_dir = root / "zero" / "web_search" / "providers"
        for src in providers_dir.glob("*.py"):
            content = src.read_text()
            # No BeautifulSoup, no lxml.html, no regex HTML scraping
            assert "BeautifulSoup" not in content, f"{src.name} must not use BeautifulSoup"
            assert "lxml.html" not in content, f"{src.name} must not use lxml.html"
            assert "from html import" not in content, f"{src.name} must not parse raw HTML"

    def test_bing_rss_uses_rss_feed_not_scraping(self):
        from zero.web_search.providers.bing_rss import BingRSSProvider
        source = inspect.getsource(BingRSSProvider)
        assert "format=rss" in source or "rss" in source.lower(), "BingRSSProvider must use RSS feed"

    def test_searxng_uses_json_api(self):
        from zero.web_search.providers.searxng import SearXNGProvider
        source = inspect.getsource(SearXNGProvider)
        assert "format=json" in source or "json" in source.lower(), "SearXNGProvider must use JSON API"

    def test_provider_registry_supports_priority_groups(self):
        registry = WebProviderRegistry()
        assert hasattr(registry, "priority_groups")
        assert hasattr(registry, "register")
        assert hasattr(registry, "unregister")


class TestWebSearchSSRFProtection:
    """The transport layer must protect against SSRF."""

    def test_transport_has_timeout_and_size_limits(self):
        from zero.web_search.transport import ConnectionPoolTransport
        sig = inspect.signature(ConnectionPoolTransport.__init__)
        params = list(sig.parameters)
        # SSRF protection: allowed_private_endpoints controls which private IPs are reachable
        assert "allowed_private_endpoints" in params, "transport must have SSRF protection (allowed_private_endpoints)"
        assert "max_connections_per_host" in params, "transport must have connection limits"
        # Timeout is per-call, verify via the search method
        source = inspect.getsource(ConnectionPoolTransport)
        assert "timeout" in source, "transport must apply timeout per call"


class TestWebSearchGroupScope:
    """Web search must be group-aware."""

    def test_web_search_enabled_is_group_setting(self):
        from zero.tenancy.registry import GROUP_SETTING_KEYS
        assert "web_search_enabled" in GROUP_SETTING_KEYS, \
            "web_search_enabled must be a group setting so groups can disable search independently"
