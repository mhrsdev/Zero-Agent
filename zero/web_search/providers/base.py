from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict

from ..models import SearchKind, SearchResult


class SearchProvider(ABC):
    name: str
    priority: int = 100
    supported_kinds: frozenset[SearchKind] = frozenset({SearchKind.WEB})

    def supports(self, kind: SearchKind) -> bool:
        return kind in self.supported_kinds

    @abstractmethod
    async def search(self, request) -> list[SearchResult]:
        raise NotImplementedError

    async def fetch_url(self, url: str, *, query: str, max_chars: int) -> SearchResult | None:
        """Return readable evidence for a public URL, or ``None`` when unsupported."""
        return None


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, SearchProvider] = {}

    def register(self, provider: SearchProvider) -> None:
        if not provider.name or provider.name in self._providers:
            raise ValueError(f'duplicate or empty provider name: {provider.name!r}')
        self._providers[provider.name] = provider

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)

    def priority_groups(self, kind: SearchKind) -> list[list[SearchProvider]]:
        groups: dict[int, list[SearchProvider]] = defaultdict(list)
        for provider in self._providers.values():
            if provider.supports(kind):
                groups[provider.priority].append(provider)
        return [sorted(groups[key], key=lambda p: p.name) for key in sorted(groups)]

    def names(self, kind: SearchKind = SearchKind.WEB) -> list[str]:
        return [provider.name for group in self.priority_groups(kind) for provider in group]

    def fetch_providers(self, kind: SearchKind = SearchKind.WEB) -> list[SearchProvider]:
        return [
            provider
            for group in self.priority_groups(kind)
            for provider in group
            if type(provider).fetch_url is not SearchProvider.fetch_url
        ]
