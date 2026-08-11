from __future__ import annotations

import json
from urllib.parse import urlsplit

from ..models import QueryPlan, SearchResult
from .base import SearchProvider


class TavilyProvider(SearchProvider):
    name = "tavily"
    priority = 5
    api_url = "https://api.tavily.com/search"

    def __init__(self, api_key: str, transport, max_results: int = 5, timeout: float = 12.0):
        key = str(api_key or "").strip()
        if not key:
            raise ValueError("Tavily API key is required")
        self._api_key = key
        self.transport = transport
        self.max_results = max(1, int(max_results))
        self.timeout = max(0.1, float(timeout))

    async def search(self, request: QueryPlan) -> list[SearchResult]:
        payload = {
            "query": request.query,
            "search_depth": "basic",
            "max_results": self.max_results,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        raw = await self.transport.post_json(
            self.api_url,
            payload,
            self.timeout,
            2_000_000,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        data = json.loads(raw)
        results: list[SearchResult] = []
        for item in data.get("results", []):
            target = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            if not target or not title:
                continue
            results.append(SearchResult(
                title=title[:300],
                url=target,
                snippet=str(item.get("content") or "")[:1000],
                publisher=urlsplit(target).netloc.lower().removeprefix("www.")[:120],
                provider=self.name,
                metadata={"tavily_score": item.get("score")},
            ))
            if len(results) >= self.max_results:
                break
        return results
