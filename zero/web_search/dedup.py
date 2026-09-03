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
    seen_titles: dict[tuple[str, str], SearchResult] = {}
    for result in results:
        url_key = canonical_url(result.url)
        # The title key carries its host. Keyed on the title alone, two genuinely
        # different outlets running the same wire headline collapsed into one
        # result — and the numeric fallback needs two independent sources before
        # it will average a price, so the collapse silently disabled it.
        host = urlsplit(result.url).netloc.lower().removeprefix('www.')
        title_key = (host, re.sub(r'\W+', ' ', result.title.lower()).strip())
        existing = seen_urls.get(url_key) or (seen_titles.get(title_key) if title_key[1] else None)
        if existing:
            existing.metadata['duplicate_count'] = int(existing.metadata.get('duplicate_count', 0)) + 1
            if len(result.snippet) > len(existing.snippet):
                existing.snippet = result.snippet
            continue
        result.metadata.setdefault('duplicate_count', 0)
        seen_urls[url_key] = result
        if title_key[1]:
            seen_titles[title_key] = result
        unique.append(result)
    return unique
