import pytest

from zero.market_prices import MilliGoldPriceClient, PriceAPIError


class _Response:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def raise_for_status(self):
        return None

    async def json(self, content_type=None):
        return self.payload


class _Session:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def get(self, *args, **kwargs):
        return _Response({'code': 0, 'data': {'price18': 182550, 'date': '2026-08-04T22:42:34'}})


@pytest.mark.asyncio
async def test_milli_gold_maps_price18_to_zero_schema(monkeypatch):
    import zero.market_prices as market_prices
    monkeypatch.setattr(market_prices.aiohttp, 'ClientSession', _Session)

    result = await MilliGoldPriceClient().get_price('18ayar')

    assert result['asset'] == '18ayar'
    assert result['value'] == '18255'
    assert result['unit'] == 'تومان'
    assert result['weight'] == '1 میلی‌گرم'
    assert result['raw_unit'] == 'ریال'
    assert result['source'] == 'Milli Gold API'
    assert result['updated_at'] == '2026-08-04T22:42:34'


@pytest.mark.asyncio
async def test_milli_gold_rejects_unsupported_asset():
    with pytest.raises(PriceAPIError):
        await MilliGoldPriceClient().get_price('sekkeh')
