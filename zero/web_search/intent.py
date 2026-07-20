from __future__ import annotations

import re

from .models import SearchIntent, SearchKind


class SearchIntentDetector:
    _web_patterns = (
        r'(^|[\s؟?!،:؛])(سرچ|جستجو)([\s$]|[؟?!،:؛])',
        r'(?:^|\s)(?:زیرو\s+)?(?:بگرد|سرچ|جستجو)(?:\s+کن)?\s*$',
        r'(?:زیرو\s+)?(?:بگرد|برو\s+بگرد|از\s+وب\s+پیدا\s+کن)\s+\S+',
        r'وب\s*رو\s*(چک|نگاه|ببین|بگرد)',
        r'(آخرین|اخرین|latest)\s*(خبر|اخبار|news)',
        r'(?:\d+|[۰-۹]+)\s*(خبر|اخبار).{0,24}(آخر|آخرین)',
        r'(آخر|آخرین).{0,16}(خبر|اخبار|news)',
        r'(چه\s+خبر|خبرهای?\s+(?:جدید|مهم|روز|رومیت))',
        r'(چطور|چگونه|چجوری).{2,}',
        r'خبر\s*(امروز|جدید|الان|لحظه)',
        r'قیمت\s*(الان\s*)?(دلار|تتر|طلا|سکه|بیت\s*کوین|اتریوم|ethereum|bitcoin|gold)',
        r'(?:قیمت|نرخ).{0,32}(?:الان|امروز|فعلی|current|now|today)',
        r'(?:مقایسه|مقایسه\s+کن|compare|comparison).{2,}',
        r'(?:تحلیل|تحلیل\s+کن|بررسی|بررسی\s+کن|تحقیق|fact\s*check|راستی\s*آزمایی).{2,}',
        r'(?:ادعا|درسته|واقعیه|صحت).{2,}(?:بررسی|چک|تحلیل|درست|غلط|واقعی|منبع)',
        r'\b(latest|current|news|search|find|look\s+up)\b',
        r'(?<![\w-])(?:https?://)?[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)*\.[a-z]{2,}',
        r'(بگرد|بررسی\s*کن|چک\s*کن|جستجو\s*کن)\s*\S+',
        r'(دستور\s*(پخت|آشپزی|اشپزی|غذا)|طرز\s+تهیه|recipe)',
        r'(از\s+اینترنت|از\s+وب).{0,40}(پیدا|بگرد|جستجو)',
    )

    def detect(self, text: str) -> SearchIntent:
        low = _normalize(text)
        explicit_search = bool(re.search(r'(?:سرچ|جستجو|بگرد|از\s+اینترنت|از\s+وب|search|find|look\s+up)', low))
        # A factual question quoted inside a story is not the current user's request.
        # Keep explicit search commands authoritative even when the message quotes someone.
        reported_quote = bool(re.search(r'[«“\"]+[^»”\"]*(?:چیست|چیه|کیست)[^»”\"]*[»”\"]+', low)) and bool(
            re.search(r'(?:پرسید|پرسیدن|گفت|گفته|می.?گفت|سوال\s+کرد|سؤال\s+کرد|جواب\s+داد)', low)
        )
        if reported_quote and not explicit_search:
            return SearchIntent(False, SearchKind.WEB, True, 'none')
        if re.search(r'(^|\s)(عکس|تصویر|image|photo)(\s|$)', low) and any(x in low for x in ('سرچ', 'جستجو', 'search', 'find')):
            return SearchIntent(True, SearchKind.IMAGE, False, 'image_search')
        if re.search(r'(^|\s)(محصول|کالا|product)(\s|$)', low) and any(x in low for x in ('سرچ', 'جستجو', 'search')):
            return SearchIntent(True, SearchKind.PRODUCT, False, 'product_search')
        needed = is_current_market_query(low) or any(re.search(pattern, low) for pattern in self._web_patterns)
        if re.search(r'(?:نظرت|فکر می.?کنی|به\s+نظر)\s+.{0,20}(?:چیست|چیه|چطور|چگونه)', low) and not re.search(r'(?:سرچ|جستجو|بگرد|از اینترنت|از وب|search|look up|تحلیل|بررسی)', low):
            needed = False
        if is_current_market_query(low):
            category = 'current_price_or_market_query'
        elif re.search(r'(آخرین|اخرین|خبر|اخبار|news|today|امروز)', low):
            category = 'latest_news'
        elif re.search(r'(مقایسه|compare)', low):
            category = 'comparison'
        elif re.search(r'(تحلیل|بررسی|تحقیق|ادعا|راستی\s*آزمایی|fact\s*check)', low):
            category = 'research_analysis'
        else:
            category = 'explicit_web_search' if needed else 'none'
        return SearchIntent(needed, SearchKind.WEB, True, category)


def _normalize(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '').lower().replace('ي', 'ی').replace('ك', 'ک')).strip()


def is_current_market_query(text: str) -> bool:
    low = _normalize(text)
    assets = ('طلا', 'عیار', 'gold', 'karat', 'carat', 'دلار', 'usd', 'تتر', 'usdt', 'سکه', 'بیت کوین', 'بیت‌کوین', 'bitcoin', 'btc', 'اتریوم', 'ethereum', 'eth', 'سولانا', 'solana', 'crypto', 'کریپتو', 'سهام', 'stock')
    prices = ('قیمت', 'نرخ', 'چنده', 'چند شد', 'price', 'current price')
    has_asset = any(a in low for a in assets)
    has_price = any(p in low for p in prices)
    has_market = any(marker in low for marker in ('بازار', 'market'))
    has_time = any(marker in low for marker in ('امروز', 'الان', 'لحظه', 'today', 'now', 'current'))
    return has_asset and (has_price or (has_market and has_time))
