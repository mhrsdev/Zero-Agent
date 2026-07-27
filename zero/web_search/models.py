from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SearchKind(str, Enum):
    WEB = 'web'
    IMAGE = 'image'
    PRODUCT = 'product'
    NEWS = 'news'
    PDF = 'pdf'
    DOWNLOAD = 'download'


@dataclass(frozen=True, slots=True)
class SearchIntent:
    needed: bool
    kind: SearchKind = SearchKind.WEB
    supported: bool = True
    category: str = 'none'


@dataclass(frozen=True, slots=True)
class QueryPlan:
    original: str
    query: str
    language: str
    kind: SearchKind = SearchKind.WEB
    preferred_domain: str = ''
    exact_terms: tuple[str, ...] = ()


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ''
    publisher: str = ''
    published_at: str = ''
    relevant_extract: str = ''
    provider: str = 'web'
    kind: SearchKind = SearchKind.WEB
    score: float = 0.0
    score_parts: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    provider: str
    reason: str
    timed_out: bool = False
    attempts: int = 1


@dataclass(slots=True)
class SearchOutcome:
    intent: SearchIntent
    plan: QueryPlan
    results: list[SearchResult] = field(default_factory=list)
    context: str = ''
    failures: list[ProviderFailure] = field(default_factory=list)
    cache_hit: bool = False
    all_providers_failed: bool = False
    no_results: bool = False
    clarification_required: bool = False
