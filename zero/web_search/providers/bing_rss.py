from __future__ import annotations

import xml.etree.ElementTree as ET
import logging
import re
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote, urlsplit

from ..models import QueryPlan, SearchResult
from .base import SearchProvider

logger = logging.getLogger('zero.web')

_NEWS_MARKERS = ('news', 'خبر', 'اخبار')
_GENERIC_NEWS_TERMS = {'latest', 'news', 'today', 'current', 'خبر', 'اخبار', 'آخرین', 'اخرین', 'جدید', 'امروز'}
_REJECTED_DOMAINS = {'outlook.com', 'office.com', 'office365.com', 'live.com'}


class BingRSSProvider(SearchProvider):
    name = 'bing-rss'
    priority = 20

    def __init__(self, transport, max_results: int = 5, timeout: float = 12.0):
        self.transport = transport
        self.max_results = max(1, int(max_results))
        self.timeout = max(0.1, float(timeout))

    async def search(self, request: QueryPlan) -> list[SearchResult]:
        if not any(marker in request.query.lower() for marker in _NEWS_MARKERS):
            logger.info('WEB_PROVIDER_UNAVAILABLE provider=bing-rss reason=news_only query=%r', request.query)
            return []
        url = 'https://www.bing.com/news/search?format=rss&q=' + quote(request.query)
        payload = await self.transport.get_text(url, self.timeout, 2_000_000)
        root = ET.fromstring(payload)
        results: list[SearchResult] = []
        for item in root.findall('./channel/item')[:self.max_results]:
            target = _source_url((item.findtext('link') or '').strip())
            title = (item.findtext('title') or '').strip()
            snippet = (item.findtext('description') or '')[:1000]
            if not target or not title or not _relevant(request.query, title, snippet, target):
                continue
            published = _iso_date(item.findtext('pubDate') or '')
            results.append(SearchResult(
                title=title[:300],
                url=target,
                snippet=snippet,
                publisher=urlsplit(target).netloc.lower().removeprefix('www.'),
                published_at=published,
                provider=self.name,
            ))
        return results


def _relevant(query: str, title: str, snippet: str, url: str) -> bool:
    domain = urlsplit(url).netloc.lower().removeprefix('www.')
    if any(domain == blocked or domain.endswith('.' + blocked) for blocked in _REJECTED_DOMAINS):
        return False
    terms = {
        token for token in re.findall(r'[a-z0-9]+|[\u0600-\u06FF]+', query.lower())
        if len(token) > 2 and token not in _GENERIC_NEWS_TERMS
    }
    if not terms:
        return False
    haystack = f'{title} {snippet} {domain}'.lower()
    return any(term in haystack for term in terms)


def _source_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.netloc.lower().endswith('bing.com'):
        target = parse_qs(parts.query).get('url', [''])[0]
        if target.startswith(('http://', 'https://')):
            return target
    return value


def _iso_date(value: str) -> str:
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return ''
