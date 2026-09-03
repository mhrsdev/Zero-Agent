from __future__ import annotations

import json
from urllib.parse import urlencode, urlsplit

from ..models import QueryPlan, SearchResult
from .base import SearchProvider


class SearXNGProvider(SearchProvider):
    name = 'searxng'
    priority = 10

    def __init__(
        self, base_url: str, transport, max_results: int = 5, timeout: float = 12.0,
        *, engines: tuple[str, ...] = (), name: str = 'searxng', priority: int = 10,
    ):
        self.base_url = base_url.rstrip('/')
        self.transport = transport
        self.max_results = max(1, int(max_results))
        self.timeout = max(0.1, float(timeout))
        self.engines = tuple(str(engine).strip() for engine in engines if str(engine).strip())
        self.name = name
        self.priority = int(priority)

    async def search(self, request: QueryPlan) -> list[SearchResult]:
        params = {'q': request.query, 'format': 'json'}
        if self.engines:
            params['engines'] = ','.join(self.engines)
        url = self.base_url + '/search?' + urlencode(params)
        payload = await self.transport.get_text(url, self.timeout, 2_000_000)
        data = json.loads(payload)
        results: list[SearchResult] = []
        # `or []` as well as a default: a server that answers {"results": null}
        # otherwise raised TypeError, which the pipeline logged as an
        # indistinguishable generic provider failure.
        for item in (data.get('results') or []):
            item_engines = set(item.get('engines') or [item.get('engine') or ''])
            if self.engines and not item_engines.intersection(self.engines):
                continue
            target = str(item.get('url') or '').strip()
            title = str(item.get('title') or '').strip()
            if not target or not title:
                continue
            results.append(SearchResult(
                title=title[:300],
                url=target,
                snippet=str(item.get('content') or item.get('snippet') or '')[:1000],
                publisher=str(item.get('publisher') or _domain(target))[:120],
                published_at=str(item.get('publishedDate') or item.get('published_at') or item.get('date') or '')[:80],
                provider=self.name,
                metadata={'engine': str(item.get('engine') or next(iter(item_engines), ''))[:80]},
            ))
            if len(results) >= self.max_results:
                break
        return results


def _domain(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix('www.')
