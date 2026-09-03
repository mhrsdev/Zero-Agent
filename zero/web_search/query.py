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
# Trailing labels that make a dotted token a file or a library name, not a host.
# `_DOMAIN_RE` matches any `word.word`, so without this every `.js` technology
# became a `site:` filter AND was stripped out of the topic: "best node.js
# framework 2026" was rewritten to "best framework 2026 site:node.js", which
# cannot match anything and no longer mentions what the user asked about.
# A few of these (py, md, rs, go, sh) are real ccTLDs; excluding them costs a
# site-filter on a rare query and buys back every query about a library.
_NOT_A_HOST_SUFFIX = frozenset({
    'js', 'mjs', 'cjs', 'jsx', 'tsx', 'py', 'rb', 'rs', 'go', 'sh', 'ps1',
    'json', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf', 'md', 'txt', 'log',
    'lock', 'env', 'sql', 'csv', 'tsv', 'xml', 'html', 'css', 'scss', 'php',
    'java', 'kt', 'cpp', 'hpp', 'cs', 'exe', 'dll', 'zip', 'tar', 'gz',
})
_EXPLICIT_SITE_RE = re.compile(r'(?<![\w-])site:\s*([a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-zA-Z]{2,})', re.I)
# Words a news query is *about* nothing when they are all that is left.
_NEWS_FILLER = frozenset({
    'خبر', 'اخبار', 'آخر', 'آخرین', 'چیا', 'چی', 'هست', 'است', 'مهم', 'جدید', 'روز',
    'news', 'latest', 'verified',
})
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
            # A query that is nothing but the domain would become
            # "digikala.com site:digikala.com"; the filter alone says it.
            if query.lower().removeprefix('www.') == preferred_domain:
                query = ''
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
        explicit = _EXPLICIT_SITE_RE.search(text or '')
        if explicit:
            return explicit.group(1).lower().removeprefix('www.')
        match = _DOMAIN_RE.search(text)
        if match and not _is_not_a_host(match.group(1)):
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
            # "Latest OpenAI news" plus whatever else the user named. Returning
            # the bare constant deleted the actual subject: "latest verified
            # OpenAI GPT-5 official release research news" became just "Latest
            # OpenAI news", so every OpenAI topic collapsed to one query — and,
            # because the query is the cache key, to one cache entry.
            extra = [t for t in _terms(low) if t not in _NEWS_FILLER and t != 'openai']
            return ' '.join(('Latest OpenAI news', *dict.fromkeys(extra))).strip()
        if 'خبر' in low or 'اخبار' in low or 'news' in low:
            if 'زومیت' in low and 'چیاست' in low and not re.search(r'\d|[۰-۹]', low):
                return low
            news_terms = [t.rstrip('؟،؛,.') for t in _terms(low) if t.rstrip('؟،؛,.') not in _NEWS_FILLER and not t.rstrip('؟،؛,.').isdigit()]
            subject = ' '.join(dict.fromkeys(news_terms))
            # The prefix follows the subject's own script. A Persian prefix on an
            # English subject also set plan.language='fa', and the ranker
            # down-weights results whose language does not match — so an English
            # news query was penalised for the words this function added.
            if subject and not re.search(r'[\u0600-\u06FF]', subject):
                return f'latest news {subject}'
            return f'آخرین اخبار {subject}' if subject else 'آخرین اخبار'
        if re.search(r'\brtx\s+spark\b', low, flags=re.I):
            return 'NVIDIA RTX Spark'
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


def _is_not_a_host(token: str) -> bool:
    """Whether a dotted token is a file or library name rather than a hostname."""
    return token.rsplit('.', 1)[-1].lower() in _NOT_A_HOST_SUFFIX


def _normalize(text: str) -> str:
    text = (text or '').lower().replace('ي', 'ی').replace('ك', 'ک')
    text = re.sub(r'@[\w_]+|[#*_`~|>\[\](){}<>"“”«»]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _terms(text: str) -> list[str]:
    text = _normalize(text)
    # An explicit `site:example.com` is an operator, not a term. Stripping the
    # domain alone left the bare word `site` behind, so `site:t.me zero agent`
    # — the exact string zero/telegram_search.py sends — became the query
    # `site agent site:t.me`.
    text = _EXPLICIT_SITE_RE.sub(' ', text)
    text = _DOMAIN_RE.sub(lambda m: ' ' if not _is_not_a_host(m.group(1)) else m.group(0), text)
    parts = re.findall(r'[A-Za-z0-9_+-]+|[\u0600-\u06FF]+', text)
    return [part for part in parts if part.lower() not in _GENERIC]
