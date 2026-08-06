import pytest

from zero.market_prices import NobitexPriceClient


class _Response:
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None
    def raise_for_status(self): return None
    async def json(self, content_type=None):
        return {
            'status': 'ok', 'lastUpdate': 1785871933098, 'lastTradePrice': '1898990',
            'bids': [['1898630', '87.7']], 'asks': [['1898990', '49.83']],
        }


class _Session:
    def __init__(self, *args, **kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None
    def get(self, *args, **kwargs): return _Response()


@pytest.mark.asyncio
async def test_nobitex_v3_maps_orderbook_and_last_trade(monkeypatch):
    import zero.market_prices as market_prices
    monkeypatch.setattr(market_prices.aiohttp, 'ClientSession', _Session)

    result = await NobitexPriceClient().get_usdt_toman()

    assert result['buy_price_toman'] == '1898630'
    assert result['sell_price_toman'] == '1898990'
    assert result['last_trade_price_toman'] == '1898990'
    assert result['last_update_ms'] == 1785871933098
    assert result['source'] == 'Nobitex API v3'