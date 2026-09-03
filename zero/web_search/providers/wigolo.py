from __future__ import annotations

import json
from urllib.parse import urlsplit

from ..models import QueryPlan, SearchResult
from .base import SearchProvider


class WigoloProvider(SearchProvider):
    """Local Wigolo REST adapter; Wigolo remains a separate process and data store."""

    name = "wigolo"
    priority = 5

    def __init__(self, base_url: str, transport, max_results: int = 5, timeout: float = 12.0):
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.max_results = max(1, int(max_results))
        self.timeout = max(0.1, float(timeout))

    async def search(self, request: QueryPlan) -> list[SearchResult]:
        payload = {
            "query": request.query,
            "max_results": self.max_results,
            "search_depth": "balanced",
        }
        raw = await self.transport.post_json(
            f"{self.base_url}/v1/search", payload, self.timeout, 2_000_000,
        )
        data = json.loads(raw)
        results: list[SearchResult] = []
        # See searxng.py: a null `results` must read as empty, not raise.
        for item in (data.get("results") or []):
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            if not url or not title or urlsplit(url).scheme not in {"http", "https"}:
                continue
            results.append(SearchResult(
                title=title[:300],
                url=url,
                snippet=str(item.get("snippet") or item.get("excerpt") or "")[:1000],
                publisher=str(item.get("publisher") or urlsplit(url).netloc).strip()[:120],
                published_at=str(item.get("published_at") or item.get("publishedAt") or "")[:80],
                provider=self.name,
                metadata={
                    "citation_id": str(item.get("citation_id") or "")[:120],
                    "evidence_score": item.get("evidence_score"),
                },
            ))
            if len(results) >= self.max_results:
                break
        return results

    async def fetch_url(self, url: str, *, query: str, max_chars: int) -> SearchResult | None:
        host = (urlsplit(url).hostname or '').lower()
        payload = {"url": url, "max_content_chars": max_chars}
        if host == 'news.google.com':
            payload['force_refresh'] = True
        raw = await self.transport.post_json(
            f"{self.base_url}/v1/fetch",
            payload,
            self.timeout,
            2_000_000,
        )
        data = json.loads(raw)
        markdown = str(
            data.get("markdown") or data.get("markdown_content") or data.get("content") or data.get("text") or ""
        ).strip()
        target = str(data.get("canonical_url") or data.get("url") or url).strip()
        if not markdown or urlsplit(target).scheme not in {"http", "https"}:
            return None
        return SearchResult(
            title=str(data.get("title") or target)[:300],
            url=target,
            snippet=markdown[:1000],
            publisher=urlsplit(target).netloc.lower().removeprefix("www.")[:120],
            relevant_extract=markdown[:max_chars],
            provider=f"{self.name}-fetch",
        )
