from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import urlsplit

from .models import QueryPlan, SearchKind


_SITE_ALIASES = {
    'میلی': 'milli.gold',
    'milli': 'milli.gold',
    'دیجیکالا': 'digikala.com',
    'digikala': 'digikala.com',
    'ترب': 'torob.com',
    'torob': 'torob.com',
}
_GENERIC = {
    'سرچ', 'جستجو', 'وب', 'چک', 'کن', 'بکن', 'بزن', 'ببین', 'نگاه', 'لطفا', 'لطفاً',
    'کامل', 'دقیق', 'یه', 'یک', 'رو', 'را', 'برای', 'برام', 'از', 'در', 'درباره', 'راجع',
    'نتیجه', 'نتایج', 'معتبر', 'منبع', 'منابع', 'عمیق', 'deep', 'هرچی', 'همه', 'اطلاعات', 'هست', 'بگو', 'به', 'نه', 'بله', 'آره', 'اره', 'باشه', 'اوکی', 'بگرد', 'برو',
    'حتما', 'حتماً', 'امروز', 'الان', 'جدید', 'اینترنت', 'پیدا', 'خب', 'خوب', 'حالا', 'پس', 'دوباره', 'بگو', 'please', 'search', 'find', 'lookup', 'zero', 'زیرو',
}
_DOMAIN_RE = re.compile(r'(?<![\w-])(?:https?://)?([a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9-]+)+)')
_URL_RE = re.compile(r'https?://[^\s<>\[\]()]+', re.I)
_LINK_REQUEST_RE = re.compile(
    r'(?:لینک|صفحه|پست|مقاله).{0,32}(?:باز|بخون|بررسی|تحلیل|چک|ببین)|'
    r'(?:این|اون)?\s*(?:چیه|چیست|چی\s+هست)|'
    r'(?:open|read|inspect|review|analy[sz]e).{0,32}(?:link|page|post|article)',
    re.I,
)


class QueryRewriter:
    def rewrite(
        self,
        text: str,
        *,
        reply_text: str = '',
        recent_messages: list[dict] | None = None,
        kind: SearchKind = SearchKind.WEB,
        followup_subject: str = '',
    ) -> QueryPlan:
        original = text or ''
        clean = _normalize(re.sub(r'\[(?:ZERO_TEST|ZERO_REG|WEBV2)[^\]]*\]', ' ', original, flags=re.I))
        recent_messages = recent_messages or []
        reply_url = self._reply_url(reply_text)
        current_url = self._reply_url(original)
        target_url = current_url or reply_url
        if not target_url and re.search(r'(?:قیمتش|مشخصاتش|اطلاعاتش|این|اون|همین|چنده\??)', clean):
            for row in reversed(recent_messages[-12:]):
                recent_url = self._reply_url(str(row.get('text', '') or ''))
                if recent_url:
                    target_url = recent_url
                    break
        if target_url and (not clean or _LINK_REQUEST_RE.search(clean) or re.search(r'(?:قیمتش|مشخصاتش|اطلاعاتش|چنده\??)', clean)):
            domain = urlsplit(target_url).netloc.lower().removeprefix('www.')
            return QueryPlan(
                original=original, query=target_url, language='en', kind=kind,
                preferred_domain=domain,
                exact_terms=tuple(dict.fromkeys(t.lower() for t in _terms(target_url)[:8])),
            )
        preferred_domain = self._preferred_domain(clean)
        topic_source = clean
        terms = _terms(clean)
        if not terms:
            recent_user_texts = [str(row.get('text', '')) for row in reversed(recent_messages[-12:]) if row.get('role', 'user') == 'user']
            for candidate in [*recent_user_texts, reply_text, *(str(row.get('text', '')) for row in reversed(recent_messages[-10:]))]:
                if _terms(candidate):
                    topic_source = _normalize(candidate)
                    break

        query = self._semantic_rewrite(topic_source)
        if preferred_domain:
            if self._is_domain_only_followup(clean):
                if followup_subject:
                    query = followup_subject
                else:
                    for row in reversed(recent_messages[-12:]):
                        if row.get('role') and row.get('role') != 'user':
                            continue
                        candidate = str(row.get('text', '') or '')
                        if _DOMAIN_RE.search(candidate) or not _terms(candidate):
                            continue
                        query = self._semantic_rewrite(candidate)
                        break
            query = re.sub(r'\s+site:\S+', '', query).strip()
            query = f'{query} site:{preferred_domain}'.strip()

        language = 'fa' if re.search(r'[\u0600-\u06FF]', query) else 'en'
        exact_terms = tuple(dict.fromkeys(t.lower() for t in _terms(query)[:8]))
        return QueryPlan(original=original, query=query[:240].strip(), language=language, kind=kind, preferred_domain=preferred_domain, exact_terms=exact_terms)

    def expand_deep(self, plan: QueryPlan) -> tuple[QueryPlan, ...]:
        base = plan.query.strip()
        if re.search(r'\bkimi\b', base, re.I) and re.search(r'(?<!\d)3(?!\d)', base):
            variants = (
                'Kimi K3 Moonshot AI model', '"Kimi K3" Moonshot AI', '"Kimi 3" Moonshot AI',
                'Kimi K3 release benchmarks', 'Kimi K3 technical report', 'Kimi K3 news review',
            )
        else:
            variants = tuple(dict.fromkeys((base, f'"{base}"', f'{base} official documentation', f'{base} independent review analysis')))
        return tuple(replace(plan, query=query[:240]) for query in variants if query)

    def _preferred_domain(self, text: str) -> str:
        match = _DOMAIN_RE.search(text)
        if match:
            return match.group(1).lower().removeprefix('www.')
        for alias, domain in _SITE_ALIASES.items():
            if re.search(rf'(?:از|تو|در)\s+{re.escape(alias)}\b', text) or f'{alias} ببین' in text:
                return domain
        return ''

    @staticmethod
    def _reply_url(text: str) -> str:
        match = _URL_RE.search((text or '').replace('\u200b', '').replace('\u200e', '').replace('\u200f', ''))
        return match.group(0).rstrip('.,،؛؟!') if match else ''

    def is_domain_only_followup(self, text: str) -> bool:
        clean = re.sub(r'\[(?:ZERO_TEST|ZERO_REG|WEBV2)[^\]]*\]', ' ', text or '', flags=re.I)
        return self._is_domain_only_followup(_normalize(clean))

    @staticmethod
    def _is_domain_only_followup(text: str) -> bool:
        without_domain = _DOMAIN_RE.sub(' ', text)
        meaningful = [t for t in _terms(without_domain) if t not in {'سایت', 'دامنه'}]
        return len(meaningful) == 0

    def _semantic_rewrite(self, text: str) -> str:
        low = _normalize(text).replace('رومیت', 'زومیت')
        low = re.sub(r'(?<![\w\u0600-\u06FF])کیمی(?![\w\u0600-\u06FF])', 'کیمی Kimi', low)
        if ('openai' in low) and any(x in low for x in ('آخرین خبر', 'اخرین خبر', 'خبر', 'اخبار', 'latest', 'جدید')):
            return 'Latest OpenAI news'
        if 'خبر' in low or 'اخبار' in low or 'news' in low:
            if 'زومیت' in low and 'چیاست' in low and not re.search(r'\d|[۰-۹]', low):
                return low
            news_terms = [t.rstrip('؟،؛,.') for t in _terms(low) if t.rstrip('؟،؛,.') not in {'خبر', 'اخبار', 'آخر', 'آخرین', 'چیا', 'چی', 'هست', 'است', 'مهم', 'جدید', 'روز'} and not t.rstrip('؟،؛,.').isdigit()]
            return 'آخرین اخبار ' + ' '.join(dict.fromkeys(news_terms)) if news_terms else 'آخرین اخبار'
        if re.search(r'\brtx\s+spark\b', low, flags=re.I):
            return 'NVIDIA RTX Spark'
        if ('openai' in low) and any(x in low for x in ('آخرین خبر', 'اخرین خبر', 'latest', 'جدید')):
            return 'Latest OpenAI news'
        if ('عیار' in low or re.search(r'\bkarat\b|\bcarat\b', low)) and any(x in low for x in ('قیمت', 'نرخ', 'چنده', 'price')):
            karat = '۲۴' if re.search(r'(?:^|\D)(?:24|۲۴)(?:\s*عیار|\s*(?:karat|carat))', low) else '۱۸'
            return f'قیمت طلای {karat} عیار امروز ایران'
        if re.search(r'(?<![\w\u0600-\u06FF])طلا(?![\w\u0600-\u06FF])', low) or re.search(r'\bgold\b', low):
            return 'قیمت طلای ۱۸ عیار امروز ایران' if 'gold' not in low else '18K gold price today Iran'
        market_map = (
            (('اتریوم', 'ethereum', 'eth'), ('قیمت لحظه‌ای اتریوم ETH', 'Ethereum ETH price today')),
            (('بیت کوین', 'بیت‌کوین', 'بیتکوین', 'bitcoin', 'btc'), ('قیمت لحظه‌ای بیت کوین BTC', 'Bitcoin BTC price today')),
            (('تتر', 'usdt'), ('قیمت لحظه‌ای تتر USDT', 'USDT price today')),
            (('دلار', 'usd', 'dollar'), ('نرخ دلار امروز', 'USD dollar price today')),
            (('سکه',), ('قیمت سکه امروز', 'Iran coin price today')),
        )
        for markers, labels in market_map:
            if any(marker in low for marker in markers) and any(x in low for x in ('قیمت', 'نرخ', 'چنده', 'price')):
                return labels[1] if re.search(r'[a-z]', low) else labels[0]
        cleaned = ' '.join(dict.fromkeys(_terms(low)))
        cleaned = re.sub(r'\bkimi\b', 'Kimi', cleaned)
        return cleaned or low


def _normalize(text: str) -> str:
    text = (text or '').lower().replace('ي', 'ی').replace('ك', 'ک')
    text = re.sub(r'@[\w_]+|[#*_`~|>\[\](){}<>"“”«»]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _terms(text: str) -> list[str]:
    text = _normalize(text)
    text = _DOMAIN_RE.sub(' ', text)
    parts = re.findall(r'[A-Za-z0-9_+-]+|[\u0600-\u06FF]+', text)
    return [part for part in parts if part.lower() not in _GENERIC]
