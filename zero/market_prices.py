from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp


class PriceAPIError(RuntimeError):
    pass


@dataclass(slots=True)
class _CachedPrice:
    expires_at: float
    value: dict[str, Any]


class BinancePriceClient:
    BASE_URL = 'https://api.binance.com/api/v3/ticker/price'
    CACHE_TTL = 5.0
    _symbol_re = re.compile(r'^[A-Z0-9]{1,20}$')

    def __init__(self) -> None:
        self._cache: dict[str, _CachedPrice] = {}
        self._lock = asyncio.Lock()

    async def get_spot_price(self, symbol: str, quote: str = 'USDT') -> dict[str, Any]:
        asset = str(symbol or '').strip().upper()
        quote = str(quote or 'USDT').strip().upper()
        if not self._symbol_re.fullmatch(asset) or not self._symbol_re.fullmatch(quote):
            raise PriceAPIError('نماد رمزارز معتبر نیست.')
        pair = f'{asset}{quote}'
        now = time.monotonic()
        async with self._lock:
            cached = self._cache.get(pair)
            if cached and cached.expires_at > now:
                return {**cached.value, 'cached': True}

        timeout = aiohttp.ClientTimeout(total=8)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(self.BASE_URL, params={'symbol': pair}) as response:
                    if response.status == 400:
                        raise PriceAPIError(f'جفت‌ارز {pair} در Binance Spot پیدا نشد.')
                    response.raise_for_status()
                    data = await response.json()
        except PriceAPIError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise PriceAPIError('Binance فعلاً پاسخ نداد.') from exc

        try:
            price = Decimal(str(data['price']))
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise PriceAPIError('قیمت دریافتی از Binance معتبر نیست.') from exc
        if not price.is_finite() or price < 0:
            raise PriceAPIError('قیمت دریافتی از Binance معتبر نیست.')

        result = {
            'asset': asset,
            'pair': pair,
            'price': str(price),
            'quote': quote,
            'unit': quote,
            'market_type': 'spot',
            'source': 'Binance Spot API',
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'cached': False,
        }
        async with self._lock:
            self._cache[pair] = _CachedPrice(time.monotonic() + self.CACHE_TTL, result)
        return result


class NavasanPriceClient:
    BASE_URL = 'https://api.navasan.tech/latest/'
    DEFAULT_ITEMS = ('18ayar', 'sekkeh')
    _item_re = re.compile(r'^[a-z0-9_]{1,40}$')

    def __init__(self, api_key_path: str = '/root/zero/runtime/secrets/navasan_api_key') -> None:
        self.api_key_path = api_key_path

    def _api_key(self) -> str:
        try:
            import os
            mode = os.stat(self.api_key_path).st_mode & 0o777
            if mode & 0o077: raise PriceAPIError('کلید Navasan permission امن ندارد.')
            key = open(self.api_key_path, encoding='utf-8').read().strip()
        except PriceAPIError:
            raise
        except (OSError, UnicodeError) as exc:
            raise PriceAPIError('کلید Navasan تنظیم نشده است.') from exc
        if not key or key.startswith('['): raise PriceAPIError('کلید Navasan تنظیم نشده است.')
        return key

    async def get_prices(self, items: tuple[str, ...] = DEFAULT_ITEMS) -> dict[str, dict[str, Any]]:
        requested = tuple(str(x).strip().lower() for x in items)
        if not requested or any(not self._item_re.fullmatch(x) for x in requested):
            raise PriceAPIError('نماد بازار ایران معتبر نیست.')
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(self.BASE_URL, params={'api_key': self._api_key()}) as response:
                    if response.status in (401, 403): raise PriceAPIError('کلید Navasan معتبر نیست یا دسترسی ندارد.')
                    response.raise_for_status(); data = await response.json(content_type=None)
        except PriceAPIError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise PriceAPIError('Navasan فعلاً پاسخ نداد.') from exc
        result = {}
        for item in requested:
            row = data.get(item)
            if not isinstance(row, dict) or row.get('value') is None:
                raise PriceAPIError(f'نماد {item} در Navasan پیدا نشد.')
            value = Decimal(str(row['value']))
            if not value.is_finite() or value < 0:
                raise PriceAPIError(f'مقدار نماد {item} در Navasan معتبر نیست.')
            if item == '18ayar' and value < 100_000:
                raise PriceAPIError('قیمت طلای ۱۸ عیار Navasan نامعقول است.')
            if item == 'sekkeh' and value < 1_000_000:
                raise PriceAPIError('قیمت سکه امامی Navasan نامعقول است؛ عدد مشکوک نمایش داده نشد.')
            result[item] = {'asset': item, 'value': str(value), 'unit': 'تومان', 'market_type': 'Iran market', 'source': 'Navasan API', 'change': row.get('change'), 'updated_at': row.get('date') or row.get('timestamp'), 'cached': False}
        return result

    async def get_price(self, item: str) -> dict[str, Any]:
        return (await self.get_prices((item,)))[item]


class NobitexPriceClient:
    BASE_URL = 'https://api.nobitex.ir/v2/orderbook/USDTIRT'
    CACHE_TTL = 5.0

    def __init__(self) -> None:
        self._cache: _CachedPrice | None = None

    async def get_usdt_toman(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._cache and self._cache.expires_at > now:
            return {**self._cache.value, 'cached': True}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(self.BASE_URL) as response:
                    response.raise_for_status(); data = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise PriceAPIError('نوبیتکس فعلاً پاسخ نداد.') from exc
        if data.get('status') != 'ok': raise PriceAPIError('نوبیتکس پاسخ معتبر نداد.')
        try:
            ask = Decimal(str(data['asks'][0][0])); bid = Decimal(str(data['bids'][0][0]))
        except (KeyError, IndexError, InvalidOperation, TypeError, ValueError) as exc:
            raise PriceAPIError('order book نوبیتکس ناقص یا نامعتبر است.') from exc
        if not ask.is_finite() or not bid.is_finite() or ask < 0 or bid < 0: raise PriceAPIError('قیمت نوبیتکس معتبر نیست.')
        result = {'asset': 'USDT', 'buy_price_toman': str(ask), 'sell_price_toman': str(bid), 'average_price_toman': str((ask + bid) / 2), 'unit': 'تومان', 'market_type': 'Nobitex order book', 'source': 'Nobitex API', 'updated_at': datetime.now(timezone.utc).isoformat(), 'cached': False}
        self._cache = _CachedPrice(time.monotonic() + self.CACHE_TTL, result)
        return result
