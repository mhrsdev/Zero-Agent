import asyncio

from zero.storage import ZeroStore
from zero.semantic_memory import SemanticUserMemory


def test_rag_refresh_and_scope(tmp_path):
    async def run():
        store = ZeroStore(tmp_path / 'zero.db')
        await store.add_long_memory(
            1,
            'project',
            'پروژه دانشگاهی ربات تلگرام',
            created_by=7,
            subject_user_id=7,
            source_message_ids=[11],
        )
        assert await store.retrieve_rag(1, 7, 'پروژه دانشگاهی')
        assert not await store.retrieve_rag(1, 8, 'پروژه دانشگاهی')

    asyncio.run(run())


def test_rag_updates_on_correction_and_clear_without_read_path_rebuild(tmp_path):
    async def run():
        store = ZeroStore(tmp_path / 'zero.db')
        memory_id = await store.add_long_memory(
            1, 'project', 'پروژه دانشگاهی قدیمی', created_by=7,
            subject_user_id=7, source_message_ids=[11],
        )
        assert await store.retrieve_rag(1, 7, 'دانشگاهی قدیمی')
        assert await store.correct_long_memory(memory_id, 'پروژه دانشگاهی جدید', actor_user_id=7, trace_id='t')
        assert await store.retrieve_rag(1, 7, 'دانشگاهی جدید')
        assert not await store.retrieve_rag(1, 7, 'قدیمی')
        await store.soft_clear_memory(1, 'long', actor_user_id=7, trace_id='t2', reason='test')
        assert not await store.retrieve_rag(1, 7, 'دانشگاهی جدید')

    asyncio.run(run())


def test_semantic_rag_updates_on_approve_correct_and_forget(tmp_path):
    async def run():
        db = tmp_path / 'zero.db'
        store = ZeroStore(db)
        memory = SemanticUserMemory(db)
        candidate = memory.candidate(chat_id=1, sender_id=7, category='interest', key='drink', value='قهوه', confidence=.9, evidence_message_ids=[12], source_text='قهوه دوست دارم')
        memory_id = memory.approve(candidate, 7)
        assert await store.retrieve_rag(1, 7, 'قهوه')
        new_id = memory.correct(memory_id, 'چای', 7)
        assert new_id != memory_id
        assert await store.retrieve_rag(1, 7, 'چای')
        assert not await store.retrieve_rag(1, 7, 'قهوه')
        memory.forget(1, 7, 'drink')
        assert not await store.retrieve_rag(1, 7, 'چای')

    asyncio.run(run())
