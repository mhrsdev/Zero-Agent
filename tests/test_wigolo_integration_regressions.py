import pytest

from zero.config import WebConfig, ZeroConfig
from zero.panel_store import PanelStore
from zero.web import HybridWeb
from zero.web_search.providers.wigolo import WigoloProvider


class DisabledPrimary:
    enabled = False


class FakeTransport:
    def __init__(self):
        self.posts = []

    async def get_text(self, url, timeout, max_bytes):
        if url.endswith('/health'):
            return '{"status":"healthy"}'
        raise RuntimeError('direct fetch unavailable')

    async def post_json(self, url, payload, timeout, max_bytes, *, headers=None):
        self.posts.append((url, payload))
        if url.endswith('/v1/fetch'):
            return '{"title":"Fetched","url":"https://example.com/article","markdown":"Evidence from the page with enough concrete, source-specific context to explain what the linked article is actually about."}'
        return '{"results":[{"title":"Result","url":"https://example.com/result","snippet":"evidence"}]}'


def config_from_example(tmp_path):
    config = ZeroConfig.load('config/zero.example.yaml')
    memory = config.memory.model_copy(update={'db_path': str(tmp_path / 'zero.db')})
    web = config.web.model_copy(update={
        'enabled': True,
        'google_grounding_enabled': False,
        'searxng_base_url': '',
        'wigolo_enabled': True,
        'wigolo_base_url': 'http://127.0.0.1:3333',
        'max_fetch_pages_per_query': 1,
    })
    return config.model_copy(update={'memory': memory, 'web': web})


@pytest.mark.asyncio
async def test_disabled_google_uses_only_wigolo_without_searxng_failures(tmp_path):
    web = HybridWeb(config_from_example(tmp_path), transport=FakeTransport(), primary=DisabledPrimary())
    assert web._local_pipeline.registry.names() == ['wigolo']
    outcome = await web.run('/search a new topic')
    assert outcome.results and outcome.results[0].provider == 'wigolo'


@pytest.mark.asyncio
async def test_direct_url_falls_back_to_wigolo_fetch(tmp_path):
    transport = FakeTransport()
    web = HybridWeb(config_from_example(tmp_path), transport=transport, primary=DisabledPrimary())
    outcome = await web.run('این چیه؟ https://example.com/article')
    assert outcome.results and outcome.results[0].provider == 'wigolo-fetch'
    assert any(url.endswith('/v1/fetch') for url, _ in transport.posts)


@pytest.mark.asyncio
async def test_wigolo_health_is_reported_as_web_health(tmp_path):
    web = HybridWeb(config_from_example(tmp_path), transport=FakeTransport(), primary=DisabledPrimary())
    assert await web.health_check() == (True, 'wigolo')


@pytest.mark.asyncio
async def test_missing_optional_tavily_key_does_not_hide_healthy_wigolo(tmp_path):
    config = config_from_example(tmp_path)
    config = config.model_copy(update={
        'web': config.web.model_copy(update={'tavily_enabled': True}),
    })
    web = HybridWeb(config, transport=FakeTransport(), primary=DisabledPrimary())
    assert await web.health_check() == (True, 'wigolo')


def test_panel_accepts_wigolo_provider(tmp_path):
    assert PanelStore._normalize_setup_step('web_search', {'enabled': True, 'provider': 'wigolo'}) == {
        'enabled': True, 'provider': 'wigolo'
    }


@pytest.mark.asyncio
async def test_wigolo_refreshes_google_news_fetches():
    transport = FakeTransport()
    provider = WigoloProvider('http://127.0.0.1:3333', transport)
    await provider.fetch_url(
        'https://news.google.com/rss/articles/opaque-token?oc=5',
        query='این چیه؟',
        max_chars=1200,
    )
    _, payload = transport.posts[-1]
    assert payload['force_refresh'] is True
