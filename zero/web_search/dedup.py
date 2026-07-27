from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import SearchResult

_TRACKING = {'fbclid', 'gclid', 'ref', 'referrer'}


def canonical_url(url: str) -> str:
    parts = urlsplit((url or '').strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in _TRACKING]
    path = re.sub(r'/+', '/', parts.path or '/').rstrip('/') or '/'
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower().removeprefix('www.'), path, urlencode(query), ''))


def deduplicate_results(results: list[SearchResult]) -> list[SearchResult]:
    unique: list[SearchResult] = []
    seen_urls: dict[str, SearchResult] = {}
    seen_titles: dict[str, SearchResult] = {}
    for result in results:
        url_key = canonical_url(result.url)
        title_key = re.sub(r'\W+', ' ', result.title.lower()).strip()
        existing = seen_urls.get(url_key) or (seen_titles.get(title_key) if title_key else None)
        if existing:
            existing.metadata['duplicate_count'] = int(existing.metadata.get('duplicate_count', 0)) + 1
            if len(result.snippet) > len(existing.snippet):
                existing.snippet = result.snippet
            continue
        result.metadata.setdefault('duplicate_count', 0)
        seen_urls[url_key] = result
        if title_key:
            seen_titles[title_key] = result
        unique.append(result)
    return unique
