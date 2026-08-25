import pytest

from zero.web_search.models import QueryPlan
from zero.web_search.providers.wigolo import WigoloProvider


class FakeTransport:
    async def post_json(self, url, payload, timeout, max_bytes):
        assert url == "http://127.0.0.1:3333/v1/search"
        assert payload["query"] == "new topic"
        assert payload["search_depth"] == "balanced"
        return '{"results":[{"title":"Official source","url":"https://example.org/a","excerpt":"evidence"}]}'


@pytest.mark.asyncio
async def test_wigolo_provider_maps_rest_results():
    provider = WigoloProvider("http://127.0.0.1:3333", FakeTransport(), max_results=3)
    results = await provider.search(QueryPlan("new topic", "new topic", "en"))
    assert [(r.title, r.url, r.provider, r.snippet) for r in results] == [
        ("Official source", "https://example.org/a", "wigolo", "evidence"),
    ]
