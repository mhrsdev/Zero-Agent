"""Reply generation and search-command helpers extracted from ZeroBrain."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from .config import ZeroConfig
from .knowledge import KnowledgePolicy
from .market_prices import PriceAPIError
from .models import IncomingMessage
from .web import is_current_price_or_market_query
from .web_search.truth import source_link

logger = logging.getLogger('zero.brain')

_LONG_REPLY_MARKERS = (
    'برنامه', 'برنامه‌ریزی', 'برنامه ریزی', 'درس', 'آموزش', 'توضیح', 'مراحل',
    'مرحله', 'لیست', 'فهرست', 'مقایسه', 'تحلیل', 'راهنما', 'کد', 'پروژه',
    'جزئیات', 'چطور', 'چگونه', 'بررسی', 'پیشنهاد', 'پلن', 'schedule', 'plan',
    'explain', 'how to', 'واژه', 'لغت', 'کلمه', 'معنی', 'مترادف', 'فرهنگ',
    'تعریف', 'دانشنامه', 'ویکی', 'ویکی‌پدیا', 'wikipedia',
    'سرچ عمیق', 'جستجوی عمیق', 'تحقیق عمیق', 'deep search', 'deepsearch', 'گزارش کامل',
)


def needs_long_reply(text: str) -> bool:
    normalized = (text or '').lower().replace('‌', ' ')
    return len(normalized) >= 180 or any(marker in normalized for marker in _LONG_REPLY_MARKERS)


def reply_char_limit(config: ZeroConfig, text: str) -> int:
    return 3900 if needs_long_reply(text) else config.policy.max_reply_chars


def reply_token_limit(text: str) -> int:
    return 2200 if needs_long_reply(text) else 700


def deterministic_market_tool_calls(text: str) -> list[dict]:
    low = (text or '').casefold().replace('ي', 'ی').replace('ك', 'ک')
    calls = []
    if re.search(r'دلار|usd|dollar', low):
        calls.append({'name': 'read_iran_market_price', 'arguments': {'asset': 'usd'}})
    if re.search(r'طلا|عیار|gold', low):
        calls.append({'name': 'read_iran_market_price', 'arguments': {'asset': '18ayar'}})
    if re.search(r'سکه(?:\s+امامی)?|coin', low):
        calls.append({'name': 'read_iran_market_price', 'arguments': {'asset': 'sekkeh'}})
    for markers, symbol in (
        (r'بیت\s*کوین|بیتکوین|bitcoin|btc', 'BTC'),
        (r'اتریوم|ethereum|eth', 'ETH'),
        (r'سولانا|solana|sol', 'SOL'),
    ):
        if re.search(markers, low):
            calls.append({'name': 'read_market_price', 'arguments': {'symbol': symbol, 'quote': 'USDT'}})
    return calls



_INTERNAL_SEARCH_STATUS_RE = re.compile(r'(?im)^\s*(?:WEB_STATUS|TG_STATUS|GOOGLE_GROUNDING_STATUS)\s*:\s*[^\r\n]*\r?\n?')


def sanitize_internal_search_status(text: str) -> str:
    return _INTERNAL_SEARCH_STATUS_RE.sub('', text or '').strip()


def parse_search_command(text: str) -> tuple[str, str] | None:
    """Return the query only when /search is the first exact token."""
    deep = re.match(r'^/deep(?:_|-)?search(?:\s+(.*))?$', text or '', re.IGNORECASE | re.DOTALL)
    if deep:
        return 'deep', (deep.group(1) or '').strip()
    match = re.match(r'^/search(?:\s+(.*))?$', text or '', re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return 'web', (match.group(1) or '').strip()


def is_telegram_search_request(text: str) -> bool:
    """Route explicit Telegram/channel searches to the TG-search client."""
    command = parse_search_command(text)
    if command and command[0] in {'telegram', 'combined'}:
        return True
    low = (text or '').lower()
    asks_for_search = any(x in low for x in ('سرچ', 'جستجو', 'بگرد', 'search', 'find', 'look up'))
    targets_telegram = 'تلگرام' in low or 'کانال' in low or 'telegram' in low or 'channel' in low
    return asks_for_search and targets_telegram


def build_live_market_disclosure(title: str, url: str, searched_at_utc: str) -> str:
    """Deterministic provenance footer for volatile market answers."""
    return f'\n\nمنبع: {source_link(url)}\nزمان جستجو: {searched_at_utc}\nقیمت‌ها نوسانی‌اند و ممکنه تا همین الان تغییر کرده باشن.'



class BrainGenerateMixin:
    """LLM completion + knowledge/market tools. Mixed into ZeroBrain."""

    async def _generate_with_knowledge_tool(self, message: IncomingMessage, prompt: str, chat_id: int, evidence: dict | None = None) -> str:
        complete_with_tools = getattr(self.router, 'complete_with_tools', None)
        if not complete_with_tools:
            return await self._generate_and_sanitize(message, prompt, chat_id)
        tools = [
            {'name': 'read_knowledge', 'description': 'Read relevant source-backed public facts and news from Zero Knowledge Memory. Use only when needed; do not use for greetings or casual conversation.', 'parameters': {'type': 'object', 'properties': {'query': {'type': 'string', 'description': 'The focused subject to look up.'}, 'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 5}}, 'required': ['query']}},
            {'name': 'read_market_price', 'description': 'Read a current public Binance Spot crypto price. Use for cryptocurrency price/rate requests. Never invent a number; the result includes source, unit, market type, and timestamp.', 'parameters': {'type': 'object', 'properties': {'symbol': {'type': 'string', 'description': 'Base crypto asset, e.g. BTC, ETH, BNB, SOL.'}, 'quote': {'type': 'string', 'description': 'Quote asset, normally USDT.'}}, 'required': ['symbol']}},
            {'name': 'read_iran_market_price', 'description': 'Read current Iran dollar, gold, and coin rates from Navasan. Symbols: usd, 18ayar, sekkeh. Never invent a number; result includes unit, source, change, and timestamp.', 'parameters': {'type': 'object', 'properties': {'asset': {'type': 'string', 'enum': ['usd', '18ayar', 'sekkeh']}}, 'required': ['asset']}},
            {'name': 'read_usdt_toman_price', 'description': 'Read the current USDT/Toman order book from Nobitex. Returns best ask (buy), best bid (sell), average, unit Toman, source, market type, and timestamp. Never invent a number.', 'parameters': {'type': 'object', 'properties': {}}},
        ]
        result = await complete_with_tools(prompt, tools, max_output_tokens=reply_token_limit(message.text or ''))
        calls = result.metadata.get('tool_calls', []) if result.metadata else []
        if not calls:
            calls = deterministic_market_tool_calls(message.text) if is_current_price_or_market_query(message.text) else []
        if not calls:
            raw = sanitize_internal_search_status(result.text or '')
            if not raw:
                raw = 'فعلاً نتونستم پاسخ مناسبی آماده کنم؛ یک لحظه بعد دوباره بپرس.'
            return await self._maybe_reply_with_sticker(raw, chat_id=chat_id, user_text=message.text or '')
        blocks = []
        for call in calls[:3]:
            name, args = call.get('name'), call.get('arguments') or {}
            if name == 'read_knowledge' and self.knowledge:
                query = str(args.get('query') or message.text or '')[:500]
                try: limit = max(1, min(5, int(args.get('max_results', 3))))
                except (TypeError, ValueError): limit = 3
                value = await self.knowledge.retrieval_context(query, policy=KnowledgePolicy(max_items=limit, context_token_budget=900))
                value = value or 'No relevant Knowledge Memory items were found. Do not invent facts.'
                blocks.append(f'[TOOL_RESULT read_knowledge]\n{value}\n[/TOOL_RESULT]')
                logger.info('KNOWLEDGE_TOOL_EXECUTED trace_id=%s query_chars=%s result_chars=%s', message.trace_id or '-', len(query), len(value))
            elif name == 'read_market_price':
                symbol = str(args.get('symbol') or args.get('asset') or '')[:20]
                quote = str(args.get('quote') or 'USDT')[:20]
                try:
                    value = await self.market_prices.get_spot_price(symbol, quote)
                    encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
                except PriceAPIError as exc:
                    encoded = json.dumps({'error': str(exc), 'source': 'Binance Spot API'}, ensure_ascii=False)
                blocks.append(f'[TOOL_RESULT read_market_price]\n{encoded}\n[/TOOL_RESULT]')
                logger.info('MARKET_PRICE_TOOL_EXECUTED trace_id=%s symbol=%s', message.trace_id or '-', symbol.upper())
            elif name == 'read_iran_market_price':
                asset = str(args.get('asset') or '')[:40].lower()
                try:
                    value = await self.navasan_prices.get_price(asset)
                    encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
                except PriceAPIError as exc:
                    if asset in {'18ayar', 'sekkeh'}:
                        try:
                            value = await self.tgju_prices.get_price(asset)
                            encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
                            logger.info('MARKET_WEB_FALLBACK_USED trace_id=%s asset=%s source=TGJU', message.trace_id or '-', asset)
                        except PriceAPIError as web_exc:
                            encoded = json.dumps({'error': str(exc), 'web_fallback_error': str(web_exc), 'source': 'Navasan API + TGJU'}, ensure_ascii=False)
                    else:
                        encoded = json.dumps({'error': str(exc), 'source': 'Navasan API'}, ensure_ascii=False)
                blocks.append(f'[TOOL_RESULT read_iran_market_price]\n{encoded}\n[/TOOL_RESULT]')
                logger.info('IRAN_MARKET_PRICE_TOOL_EXECUTED trace_id=%s asset=%s', message.trace_id or '-', asset)
            elif name == 'read_usdt_toman_price':
                try:
                    value = await self.nobitex_prices.get_usdt_toman()
                    encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
                except PriceAPIError as exc:
                    encoded = json.dumps({'error': str(exc), 'source': 'Nobitex API'}, ensure_ascii=False)
                blocks.append(f'[TOOL_RESULT read_usdt_toman_price]\n{encoded}\n[/TOOL_RESULT]')
                logger.info('USDT_TOMAN_PRICE_TOOL_EXECUTED trace_id=%s', message.trace_id or '-')
        if not blocks:
            return await self._generate_and_sanitize(message, prompt, chat_id)
        if evidence is not None:
            evidence['trusted_text'] = '\n\n'.join(blocks)
        final_prompt = prompt + '\n\n' + '\n\n'.join(blocks) + '\nUse tool results when relevant. If a result has an error, report that honestly. Return the final natural reply only; never invent a price.'
        return await self._generate_and_sanitize(message, final_prompt, chat_id)

    async def _generate_and_sanitize(self, message: IncomingMessage, prompt: str, chat_id: int) -> str:
        result = await self.router.complete(prompt, max_output_tokens=reply_token_limit(message.text or ''))
        try:
            from .spend import add_spend, estimate_usd
            cost = float(getattr(result, 'estimated_cost_usd', 0) or 0) or estimate_usd(prompt, result.text or '')
            await add_spend(self.store, message.chat_id, cost)
        except Exception:
            pass
        raw = sanitize_internal_search_status(result.text or '')
        try:
            await self._memory_for(message).observe(message, raw)
            if os.getenv('ZERO_PROACTIVE_FOLLOWUP_ENABLED','false').lower()=='true':
                outcome=await self.proactive_followups.consider(message)
                await self._memory_for(message).metric(message.trace_id or '-', 'proactive_followup', outcome)
        except Exception as exc:
            logger.warning('MEMORY_V3_WRITE_FAILED trace_id=%s exception_type=%s', message.trace_id or '-', type(exc).__name__)
        return await self._maybe_reply_with_sticker(raw, chat_id=chat_id, user_text=message.text or '')
