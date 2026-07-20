import asyncio
from pathlib import Path
from types import SimpleNamespace

from zero.knowledge import KnowledgeWorker, LocalLLMKnowledgeBackend, validate_model_output
from zero.storage import ZeroStore


class FakeWeb:
    async def run(self, query, trace_id='-'):
        hit = SimpleNamespace(title='Official release', url='https://example.org/release?utm_source=x', snippet='A verified release.', relevant_extract='A verified release.', published_at='2026-07-10')
        return SimpleNamespace(results=[hit])


class FakeRouter:
    def __init__(self):
        self.structured_calls = 0

    async def complete_structured(self, prompt, *, max_output_tokens=850):
        self.structured_calls += 1
        return SimpleNamespace(text='{"topic":"AI Models","title":"Official release","summary":"A verified release.","facts":[{"text":"A verified release.","confidence":0.9,"source_indices":[0]}],"tags":["ai"],"importance":0.8,"freshness":"daily","suggested_ttl_hours":24,"contradictions":[],"should_store":true}', model='fake-model', provider='fake', metadata={'json_mode': True})

    async def complete(self, prompt, *, max_output_tokens=700):
        raise AssertionError('public conversational router path used')


def test_dry_run_is_one_topic_and_does_not_store(tmp_path: Path):
    async def scenario():
        store = ZeroStore(str(tmp_path / 'zero.db'))
        router = FakeRouter()
        worker = KnowledgeWorker(store, FakeWeb(), router)
        result = await worker.run_nightly(dry_run=True)
        assert router.structured_calls == 1
        assert result['topics'] == 1
        assert result['accepted_count'] == 1
        with store._conn() as conn:
            assert conn.execute('SELECT COUNT(*) c FROM knowledge_items').fetchone()['c'] == 0
    asyncio.run(scenario())


def test_production_dedup_and_retrieval_are_separate_from_memory(tmp_path: Path):
    async def scenario():
        store = ZeroStore(str(tmp_path / 'zero.db'))
        worker = KnowledgeWorker(store, FakeWeb(), FakeRouter())
        first = await worker.run_nightly(topic_limit=1)
        async with store._lock:
            with store._conn() as conn:
                conn.execute("UPDATE knowledge_topics SET enabled=CASE WHEN id=1 THEN 1 ELSE 0 END")
                conn.commit()
        second = await worker.run_nightly(topic_limit=1)
        assert first['accepted_count'] == second['accepted_count'] == 1
        with store._conn() as conn:
            assert conn.execute('SELECT COUNT(*) c FROM knowledge_items').fetchone()['c'] == 1
            assert conn.execute('SELECT COUNT(*) c FROM knowledge_sources').fetchone()['c'] == 1
            assert conn.execute('SELECT COUNT(*) c FROM memory_items').fetchone()['c'] == 0
        context = await worker.retrieval_context('official release')
        assert '[KNOWLEDGE_ITEM]' in context
        assert '<html' not in context.lower()
    asyncio.run(scenario())


def test_invalid_source_mapping_rejected():
    payload = {'_raw':'{"topic":"x","title":"t","summary":"s","facts":[{"text":"f","confidence":0.9,"source_indices":[4]}],"tags":[],"importance":0.5,"freshness":"daily","suggested_ttl_hours":24,"contradictions":[],"should_store":true}'}
    try:
        validate_model_output(payload, [{'url':'https://example.org'}])
    except ValueError as exc:
        assert str(exc) == 'invalid_source_mapping'
    else:
        raise AssertionError('invalid mapping accepted')


def test_local_backend_is_explicitly_not_configured():
    async def scenario():
        try:
            await LocalLLMKnowledgeBackend().summarize_and_extract('AI', [], None)
        except RuntimeError as exc:
            assert str(exc) == 'LOCAL_BACKEND_NOT_CONFIGURED'
        else:
            raise AssertionError('local backend unexpectedly configured')
    asyncio.run(scenario())


def test_telegram_candidate_queue_is_bounded_and_processed(tmp_path: Path):
    async def scenario():
        store = ZeroStore(str(tmp_path / 'zero.db'))
        await store.enqueue_telegram_knowledge_candidate(topic='Gemini', source_provider='channel_inspector', channel_identifier='public_x', message_id=1, canonical_link='https://t.me/public_x/1', text_excerpt='Verified public update.', published_at='2026-07-11', relevance_score=.9, confidence=.8, dedup_key='candidate-1', expires_at=9999999999)
        class BackendRouter:
            async def complete_structured(self, prompt, *, max_output_tokens=850):
                return SimpleNamespace(text='{"topic":"Telegram Search","title":"Public Telegram update","summary":"Verified public update.","facts":[{"text":"Verified public update.","confidence":0.9,"source_indices":[0]}],"tags":["telegram"],"importance":0.7,"freshness":"daily","suggested_ttl_hours":24,"contradictions":[],"should_store":true}', model='fake', provider='fake', metadata={})
        worker = KnowledgeWorker(store, FakeWeb(), BackendRouter())
        result = await worker.process_telegram_candidates('run', 'trace', budget=1)
        assert result['accepted'] == 1 and result['calls'] == 1
        with store._conn() as conn:
            assert conn.execute("SELECT COUNT(*) c FROM knowledge_items WHERE topic_id IN (SELECT id FROM knowledge_topics WHERE topic='Telegram Search')").fetchone()['c'] == 1
            assert conn.execute("SELECT COUNT(*) c FROM memory_items").fetchone()['c'] == 0
    asyncio.run(scenario())


def test_web_candidate_queue_dedup_restart_and_nightly_batch(tmp_path: Path):
    async def scenario():
        store = ZeroStore(str(tmp_path / 'zero.db'))
        first = await store.enqueue_web_knowledge_candidate(query='q', normalized_query='q', title='Public web update', url='https://example.org/a', publisher='Example', snippet='Verified public update.', extracted_relevant_text='Verified public update with source context.', language='en', confidence=.9, freshness='daily', trace_id='t', content_hash='h', semantic_key='s', expires_at=9999999999)
        duplicate = await store.enqueue_web_knowledge_candidate(query='q2', normalized_query='q', title='Public web update', url='https://example.org/a', publisher='Example', snippet='same', extracted_relevant_text='Verified public update with source context.', language='en', confidence=.9, freshness='daily', trace_id='t2', content_hash='h', semantic_key='s', expires_at=9999999999)
        assert first == 'created' and duplicate == 'duplicate'
        assert (await ZeroStore(str(tmp_path / 'zero.db')).web_knowledge_queue_status())['pending'] == 1
        class BackendRouter:
            async def complete_structured(self, prompt, *, max_output_tokens=850):
                return SimpleNamespace(text='{"topic":"Web Search","title":"Public web update","summary":"Verified public update.","facts":[{"text":"Verified public update.","confidence":0.9,"source_indices":[0]}],"tags":["web"],"importance":0.8,"freshness":"daily","suggested_ttl_hours":24,"contradictions":[],"should_store":true}', model='fake', provider='fake', metadata={})
        worker = KnowledgeWorker(store, FakeWeb(), BackendRouter())
        result = await worker.process_web_candidates('run', 'trace', budget=1)
        assert result == {'calls': 1, 'accepted': 1, 'rejected': 0}
        assert (await store.web_knowledge_queue_status())['processed'] == 1
    asyncio.run(scenario())
