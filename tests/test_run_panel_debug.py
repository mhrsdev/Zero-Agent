from types import SimpleNamespace

import pytest

from scripts.run_panel import debug_web_search_hits


class AsyncWeb:
    def __init__(self):
        self.query = None

    async def run(self, query):
        self.query = query
        return SimpleNamespace(results=[SimpleNamespace(title='result', url='https://example.test')])

    def search(self, query):
        raise AssertionError('async command handler must not call the sync compatibility API')


@pytest.mark.asyncio
async def test_debug_web_search_hits_awaits_web_run():
    web = AsyncWeb()

    hits = await debug_web_search_hits(web, 'fresh query')

    assert web.query == 'fresh query'
    assert [(hit.title, hit.url) for hit in hits] == [('result', 'https://example.test')]
