from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .models import QueryPlan, SearchResult

_AUTHORITY = {
    'openai.com': 1.0, 'nvidia.com': 1.0, 'microsoft.com': 0.95, 'google.com': 0.95,
    'reuters.com': 0.95, 'apnews.com': 0.95, 'bbc.com': 0.9, 'wikipedia.org': 0.8,
}
# Words the query rewriter adds itself. They are not part of what the user asked
# about, so the relevance gate must not demand them of a result.
_GATE_FILLER = frozenset({
    'latest', 'news', 'اخبار', 'آخرین', 'اخرین', 'خبر', 'today', 'امروز',
    'official', 'documentation', 'independent', 'review', 'analysis', 'verified',
})


class ResultRanker:
    def rank(self, plan: QueryPlan, results: list[SearchResult]) -> list[SearchResult]:
        query_tokens = set(_tokens(plan.query))
        for result in results:
            haystack = f'{result.title} {result.snippet} {result.publisher}'.lower()
            result_tokens = set(_tokens(haystack))
            overlap = len(query_tokens & result_tokens) / max(1, len(query_tokens))
            exact = 1.0 if plan.query.lower() in haystack else min(1.0, sum(term in haystack for term in plan.exact_terms) / max(1, len(plan.exact_terms)))
            domain = urlsplit(result.url).netloc.lower().removeprefix('www.')
            preferred = 1.0 if plan.preferred_domain and (domain == plan.preferred_domain or domain.endswith('.' + plan.preferred_domain)) else 0.0
            authority = max((score for key, score in _AUTHORITY.items() if domain == key or domain.endswith('.' + key)), default=0.45)
            freshness = _freshness(result.published_at)
            language = 1.0 if (_language(haystack) == plan.language) else 0.3
            duplicate = 1.0 / (1.0 + int(result.metadata.get('duplicate_count', 0)))
            parts = {
                'relevance': overlap,
                'freshness': freshness,
                'authority': authority,
                'duplicate': duplicate,
                'domain_preference': preferred,
                'language': language,
                'exact_match': exact,
            }
            result.score_parts = parts
            result.score = round(
                overlap * 0.30 + freshness * 0.14 + authority * 0.14 + duplicate * 0.08
                + preferred * 0.16 + language * 0.06 + exact * 0.12,
                6,
            )
        return sorted(results, key=lambda row: (row.score, row.title.lower()), reverse=True)

    def is_relevant(self, plan: QueryPlan, result: SearchResult) -> bool:
        # Filler the rewriter itself adds. Requiring two matches out of terms the
        # user never typed rejected every legitimate result for a news query:
        # "آخرین اخبار زومیت" carries three terms of which two are the rewriter's
        # own words, and a real zoomit.ir article matches only "زومیت". The gate
        # must judge the subject, not the scaffolding.
        terms = [
            term.casefold() for term in plan.exact_terms
            if (len(term) > 1 or term.isdigit()) and term.casefold() not in _GATE_FILLER
        ]
        if not terms:
            return True
        haystack = f'{result.title} {result.snippet} {result.publisher} {result.url}'.casefold()
        if ('kimi' in terms or 'کیمی' in terms) and '3' in terms:
            version_match = bool(re.search(r'(?:\bkimi|کیمی)[\s_-]*(?:k[\s_-]*)?3\b', haystack, re.I))
            ai_context = any(marker in haystack for marker in ('moonshot', 'artificial intelligence', ' ai ', 'llm', 'language model', 'هوش مصنوعی', 'مدل زبانی', 'benchmark', 'agent'))
            return version_match and ai_context
        matched = sum(term in haystack for term in terms)
        # Two matches is a reasonable bar for a rich query and an impossible one
        # for a two-term subject where the publisher name is transliterated.
        required = 1 if len(terms) <= 2 else 2
        return matched >= required


def _tokens(text: str) -> list[str]:
    return re.findall(r'[a-z0-9]+|[\u0600-\u06FF]+', (text or '').lower())


def _language(text: str) -> str:
    return 'fa' if re.search(r'[\u0600-\u06FF]', text or '') else 'en'


def _freshness(value: str) -> float:
    if not value:
        return 0.35
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400)
        return max(0.0, math.exp(-age_days / 180.0))
    except ValueError:
        return 0.35
