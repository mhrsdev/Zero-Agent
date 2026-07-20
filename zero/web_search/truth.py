from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .models import SearchResult

_URL_RE = re.compile(r'https?://[^\s<>\]\[()]+', re.I)
_DOMAIN_RE = re.compile(r'(?<![@\w-])(?:www\.)?((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62})(?![\w-])', re.I)
_NUMBER_RE = re.compile(r'(?<!\w)\d[\d,.٬،]*\d|(?<!\w)\d(?!\w)')
_PRICE_MARKERS = ('تومان', 'ریال', 'دلار', 'یورو', 'usd', 'eur', '$', '€', 'قیمت', 'price')
_DIGIT_TRANSLATION = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٬،', '01234567890123456789,,')


NUMERIC_FALLBACK_CATEGORIES = frozenset({'current_price_or_market_query','market_rate','exchange_rate','numeric_live_value'})


def numeric_fallback_eligible(category: str) -> bool:
    return category in NUMERIC_FALLBACK_CATEGORIES


def _normalize_number(value: str) -> str:
    return re.sub(r'[^0-9]', '', (value or '').translate(_DIGIT_TRANSLATION))


def numeric_entities(text: str) -> list[str]:
    return [_normalize_number(x) for x in _NUMBER_RE.findall(text or '') if _normalize_number(x)]


def site_name(url: str, publisher: str = '') -> str:
    host=urlsplit(url or '').netloc.lower().removeprefix('www.')
    stem=host.split('.')[0] if host else (publisher or 'منبع').split('.')[0]
    return {'tgju':'TGJU','tabdeal':'Tabdeal','tala':'Tala.ir','reuters':'Reuters','bloomberg':'Bloomberg','wallex':'Wallex'}.get(stem, stem.title() or 'منبع')


def source_link(url: str, publisher: str = '') -> str:
    if not _valid_public_http_url(url):
        return '[منبع نامعتبر حذف شد]'
    safe_url = url.replace('(', '%28').replace(')', '%29')
    return f'[{site_name(url, publisher)}]({safe_url})'


def sanitize_source_display(text: str, results: list[SearchResult], allow_raw_links: bool = False) -> str:
    if allow_raw_links: return text or ''
    links={_normalize_url(r.url): source_link(r.url, r.publisher) for r in results}
    blocks=[]
    def protect_block(match):
        blocks.append(match.group(0)); return f'\x01{len(blocks)-1}\x02'
    text=re.sub(r'```[\s\S]*?```|`[^`\n]+`', protect_block, text or '')
    markdown_links=[]
    def protect_link(match):
        url=match.group(2); key=_normalize_url(url)
        if key not in links: links[key]=source_link(url)
        markdown_links.append(links[key]); return f'\x00{len(markdown_links)-1}\x00'
    text=re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)', protect_link, text)
    def replace_raw(match):
        raw=match.group(0).rstrip('.,،؛'); key=_normalize_url(raw)
        if key not in links: links[key]=source_link(raw)
        return links[key]
    text=_URL_RE.sub(replace_raw, text)
    for i,link in enumerate(markdown_links): text=text.replace(f'\x00{i}\x00',link)
    for i,block in enumerate(blocks): text=text.replace(f'\x01{i}\x02',block)
    for link in set(links.values()):
        first=True
        def keep(match):
            nonlocal first
            if first: first=False; return match.group(0)
            return ''
        text=re.sub(re.escape(link), keep, text)
    text=re.sub(r'(?m)^\s*(?:•\s*)?منبع:\s*$\n?', '', text)
    text=re.sub(r'(?m)^\s*•\s*$\n?', '', text)
    return text.rstrip()


def build_numeric_fallback(results: list[SearchResult], searched_at: str = '') -> str:
    values=[]; sources=[]; seen_urls=set(); units=set()
    for result in results:
        key=_normalize_url(result.url)
        if key in seen_urls: continue
        seen_urls.add(key)
        text=f'{result.relevant_extract} {result.snippet}'
        raw=next((x for x in _NUMBER_RE.findall(text) if len(_normalize_number(x)) >= 6), '')
        if not raw: continue
        value=int(_normalize_number(raw)); values.append(value)
        unit = 'تومان' if 'تومان' in text else ('ریال' if 'ریال' in text else '')
        units.add(unit)
        label=site_name(result.url, result.publisher)
        sources.append(source_link(result.url, result.publisher))
        if len(values)>=5: break
    if not values or len(units) != 1:
        return ''
    average=round(sum(values) / len(values))
    formatted=f'{average:,}'.replace(',', '٬').translate(str.maketrans('0123456789','۰۱۲۳۴۵۶۷۸۹'))
    unit = next(iter(units - {''}), '')
    label='میانگین آخرین قیمت‌های معتبر' if len(values) > 1 else 'قیمت گزارش‌شده'
    out=f'{label}: {formatted}' + (f' {unit}' if unit else '') + '\n\nمنابع:\n'+'\n'.join(f'• {s}' for s in sources)
    if searched_at: out += f'\nزمان جست‌وجو: {searched_at}'
    return out


def build_news_fallback(results: list[SearchResult], searched_at: str = '') -> str:
    """Render source-backed headlines when the LLM answer fails the guard."""
    lines=[]; seen_urls=set()
    for result in results:
        key=_normalize_url(result.url)
        if key in seen_urls or not result.title.strip(): continue
        seen_urls.add(key)
        publisher=result.publisher.strip() or site_name(result.url)
        published=result.published_at.strip() or 'تاریخ انتشار در نتیجهٔ جست‌وجو مشخص نبود'
        lines.append(f'• {result.title.strip()}\n  ناشر: {publisher}\n  تاریخ انتشار: {published}\n  منبع: {source_link(result.url, result.publisher)}')
        if len(lines)>=5: break
    if not lines: return ''
    out='آخرین نتایج خبری که منبع و عنوانشان قابل‌تأیید بود:\n'+'\n'.join(lines)
    if searched_at: out += f'\n\nزمان جست‌وجو: {searched_at}'
    return out

@dataclass(frozen=True, slots=True)
class GuardDecision:
    allowed: bool
    reason: str = ''


class TruthfulnessGuard:
    def filter_results(self, results: list[SearchResult]) -> list[SearchResult]:
        return [result for result in results if _valid_public_http_url(result.url) and bool(result.title.strip())]

    def guard_answer(self, answer: str, results: list[SearchResult], trusted_text: str = '') -> GuardDecision:
        source_urls = {_normalize_url(result.url) for result in results}
        source_domains = {urlsplit(result.url).netloc.lower().removeprefix('www.') for result in results}
        source_text = ' '.join(
            f'{result.title} {result.snippet} {result.publisher} {result.published_at} {result.relevant_extract} {result.url}'
            for result in results
        ) + ' ' + (trusted_text or '')
        source_text = source_text.lower()
        source_numbers = set(numeric_entities(source_text))
        for url in _URL_RE.findall(answer or ''):
            if _normalize_url(url.rstrip('.,،؛')) not in source_urls:
                return GuardDecision(False, 'unsupported_url')
        for domain in _DOMAIN_RE.findall(answer or ''):
            normalized = domain.lower().removeprefix('www.')
            if normalized not in source_domains and not any(normalized.endswith('.' + allowed) for allowed in source_domains):
                return GuardDecision(False, 'unsupported_domain')
        low = (answer or '').lower()
        if any(marker in low for marker in _PRICE_MARKERS):
            for number in numeric_entities(low):
                if len(number) >= 4 and number not in source_numbers:
                    return GuardDecision(False, 'unsupported_numeric_claim')
        return GuardDecision(True)


def _normalize_url(url: str) -> str:
    parts=urlsplit((url or '').strip())
    host=(parts.hostname or '').lower().removeprefix('www.')
    path=(parts.path or '/').rstrip('/') or '/'
    return f'{host}{path}' + (f'?{parts.query}' if parts.query else '')


def _valid_public_http_url(url: str) -> bool:
    try:
        if re.search(r'[\x00-\x20\x7f<>\[\]{}\\]', url or ''):
            return False
        parts = urlsplit(url)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
            return False
        host = parts.hostname.lower()
        if host in {'localhost', 'localhost.localdomain'}:
            return False
        try:
            addr = ipaddress.ip_address(host)
            return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved)
        except ValueError:
            return '.' in host
    except ValueError:
        return False
