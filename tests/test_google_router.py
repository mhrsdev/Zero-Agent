from conftest import CONFIG_EXAMPLE
import pytest

from zero.config import ZeroConfig
from zero.google_grounding import GoogleGroundingSearch
from zero.router import IndependentRouter


def cfg(tmp_path):
    c = ZeroConfig.load(CONFIG_EXAMPLE)
    return c.model_copy(update={
        'memory': c.memory.model_copy(update={'db_path': str(tmp_path / 'zero.db')}),
        'web': c.web.model_copy(update={'enabled': True, 'google_grounding_enabled': True}),
        'router': c.router.model_copy(update={
            'providers': c.router.providers.model_copy(update={
                'openrouter': c.router.providers.openrouter.model_copy(update={'keys': ['or-a', 'or-b']}),
                'gemini': c.router.providers.gemini.model_copy(update={'keys': ['gg-a', 'gg-b', 'gg-c']}),
            })
        }),
    })


@pytest.mark.asyncio
async def test_openrouter_failure_falls_back_to_google(tmp_path):
    r = IndependentRouter(cfg(tmp_path))
    r._openrouter_call = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('down'))
    r._google_call = lambda *a, **k: ('ok', {'candidates': [{'content': {'parts': [{'text': 'ok'}]}}]})
    out = await r.complete('hello')
    assert out.provider == 'gemini' and out.text == 'ok'
    assert out.metadata['fallback_used'] is True


@pytest.mark.asyncio
async def test_grounding_without_metadata_is_rejected(tmp_path):
    r = IndependentRouter(cfg(tmp_path))
    r._google_call = lambda *a, **k: ('answer', {'candidates': [{'content': {'parts': [{'text': 'answer'}]}}]})
    out = await r.complete_search('latest news')
    assert out.text == '' and out.metadata['error'] == 'GROUNDING_METADATA_MISSING'


@pytest.mark.asyncio
async def test_grounding_exhausts_all_google_keys_before_failing(tmp_path):
    r = IndependentRouter(cfg(tmp_path))
    attempts = []

    def google_call(model, key, prompt, max_output_tokens, *, search=False):
        attempts.append(key)
        if len(attempts) < 3:
            from urllib.error import HTTPError
            raise HTTPError('https://generativelanguage.googleapis.com', 429, 'rate limited', {}, None)
        return 'grounded', {'candidates': [{'content': {'parts': [{'text': 'grounded'}]}, 'groundingMetadata': {'groundingChunks': [{'web': {'uri': 'https://example.com', 'title': 'Example'}}]}}]}

    r._google_call = google_call
    out = await r.complete_search('latest news')
    assert out.text == 'grounded'
    assert len(attempts) == 3


@pytest.mark.asyncio
async def test_grounding_requires_real_source_and_returns_metadata(tmp_path):
    r = IndependentRouter(cfg(tmp_path))
    r._google_call = lambda *a, **k: ('answer', {'candidates': [{'content': {'parts': [{'text': 'answer'}]}, 'groundingMetadata': {'groundingChunks': [{'web': {'uri': 'https://example.com/a', 'title': 'Example'}}]}}]})
    out = await GoogleGroundingSearch(cfg(tmp_path), r).run('آخرین خبر OpenAI', trace_id='t')
    assert len(out.results) == 1 and out.results[0].metadata['grounding'] is True
    assert out.results[0].relevant_extract == 'answer'
    assert 'WEB_DATA_IS_UNTRUSTED' in out.context


@pytest.mark.asyncio
async def test_grounding_runs_for_plain_query_authorized_by_slash_search(tmp_path):
    r = IndependentRouter(cfg(tmp_path))
    r._google_call = lambda *a, **k: ('plain answer', {'candidates': [{'content': {'parts': [{'text': 'plain answer'}]}, 'groundingMetadata': {'groundingChunks': [{'web': {'uri': 'https://example.com/plain', 'title': 'Plain'}}]}}]})

    out = await GoogleGroundingSearch(cfg(tmp_path), r).run('plain topic', trace_id='plain')

    assert out.results
    assert out.results[0].provider == 'google-grounding'


def test_status_has_only_hashed_key_ids(tmp_path):
    r = IndependentRouter(cfg(tmp_path))
    status = r.status()
    assert all(len(item['key_id']) == 16 and 'secret' not in str(item).lower() for p in status['providers'].values() for item in p['keys'])
