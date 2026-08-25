from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from html import unescape
from urllib.parse import urlsplit

from .models import SearchResult


_MIN_EVIDENCE_CHARS = 80


def has_usable_evidence(text: str) -> bool:
    """Avoid giving an LLM a title/challenge shell and calling it page evidence."""
    return len(re.sub(r'\s+', '', text or '')) >= _MIN_EVIDENCE_CHARS


class WebExtractor:
    def __init__(self, transport, max_extract_chars: int = 1200, request_timeout: float = 8.0, max_bytes: int = 1_000_000):
        self.transport = transport
        self.max_extract_chars = max(100, int(max_extract_chars))
        self.request_timeout = max(0.1, float(request_timeout))
        self.max_bytes = max(1024, int(max_bytes))

    async def extract_many(self, results: list[SearchResult], query: str, limit: int) -> list[SearchResult]:
        async def one(result: SearchResult) -> None:
            if not _safe_public_url(result.url):
                return
            try:
                html = await self.transport.get_text(result.url, self.request_timeout, self.max_bytes)
                text = _html_to_text(html)
                result.relevant_extract = _relevant_extract(text, query, self.max_extract_chars)
            except Exception:
                result.relevant_extract = result.snippet[:self.max_extract_chars]

        await asyncio.gather(*(one(result) for result in results[:max(0, limit)]))
        return results

    async def extract_url(self, url: str, query: str = '') -> SearchResult:
        if not _safe_public_url(url):
            raise ValueError('unsafe public URL')
        html = await self.transport.get_text(url, self.request_timeout, self.max_bytes)
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html or '', flags=re.I | re.S)
        title = re.sub(r'\s+', ' ', unescape(title_match.group(1))).strip() if title_match else url
        text = _html_to_text(html)
        extract = _relevant_extract(text, query or title, self.max_extract_chars)
        return SearchResult(title=title[:300], url=url, snippet=extract[:500], relevant_extract=extract, provider='direct-url')


def _safe_public_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in {'http', 'https'} or not parts.hostname:
        return False
    return _public_addresses(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80))


def _public_addresses(host: str, port: int) -> bool:
    return bool(_resolved_public_addresses(host, port))


def _resolved_public_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        addresses = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except (OSError, ValueError):
        return ()
    metadata = {'169.254.169.254', '169.254.169.253', '100.100.100.200'}
    if not addresses or not all(address.is_global and str(address) not in metadata for address in addresses):
        return ()
    return tuple(sorted(str(address) for address in addresses))


def _html_to_text(html: str) -> str:
    text = re.sub(r'<(script|style|noscript)[^>]*>.*?</\1>', ' ', html or '', flags=re.I | re.S)
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', unescape(text)).strip()


def _relevant_extract(text: str, query: str, limit: int) -> str:
    sentences = re.split(r'(?<=[.!؟])\s+', text)
    terms = set(re.findall(r'[a-z0-9]+|[\u0600-\u06FF]+', query.lower()))
    ranked = sorted(enumerate(sentences), key=lambda item: (sum(term in item[1].lower() for term in terms), -item[0]), reverse=True)
    selected = [sentence for _, sentence in ranked[:4] if sentence]
    return ' '.join(selected)[:limit].strip()
