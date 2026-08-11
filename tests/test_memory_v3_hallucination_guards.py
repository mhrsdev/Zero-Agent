import asyncio

from zero.core.memory_service import MemoryService
from zero.memory_v3 import MemoryV3Item, MemoryV3Service
from zero.models import IncomingMessage


def message(*, sender=1, text="موضوع جدیدی داریم"):
    return IncomingMessage(
        chat_id=-100, chat_title="group", sender_id=sender, sender_label=f"u{sender}",
        text=text, message_id=50, platform="telegram", account_scope="listener",
    )


def test_unrelated_high_importance_memories_are_not_injected(tmp_path):
    async def run():
        service = MemoryV3Service(str(tmp_path / "v3.db"))
        await service.put(MemoryV3Item.personal(
            chat_id=-100, user_id=1, content="رنگ مورد علاقه کاربر بنفش است",
            importance=1.0, confidence=1.0, source_message_ids=(1,),
        ))
        await service.put(MemoryV3Item.group(
            chat_id=-100, content="جلسه قدیمی گروه روز شنبه بود",
            importance=1.0, confidence=1.0, source_message_ids=(2,),
        ))
        context, meta = await service.context(message(text="در مورد معماری شبکه توضیح بده"))
        assert context == ""
        assert meta["selected"] == 0

    asyncio.run(run())


def test_identity_lookup_excludes_speaker_and_unrelated_group_memory(tmp_path):
    async def run():
        service = MemoryV3Service(str(tmp_path / "v3.db"))
        await service.put(MemoryV3Item.personal(
            chat_id=-100, user_id=1, content="گوینده عاشق رنگ قرمز است",
            importance=1.0, confidence=1.0, source_message_ids=(1,),
        ))
        await service.put(MemoryV3Item.group(
            chat_id=-100, content="گروه جمعه تعطیل است",
            importance=.99, confidence=1.0, source_message_ids=(2,),
        ))
        await service.put(MemoryV3Item.personal(
            chat_id=-100, user_id=2, content="کاربر هدف برنامه نویس پایتون است",
            importance=.8, confidence=1.0, source_message_ids=(3,),
        ))
        context, meta = await service.context(
            message(text="این کیه؟"), target_user_id=2, identity_lookup=True,
        )
        assert "کاربر هدف برنامه نویس پایتون است" in context
        assert "گوینده عاشق رنگ قرمز است" not in context
        assert "گروه جمعه تعطیل است" not in context
        assert meta["target_user_ids"] == (2,)

    asyncio.run(run())


def test_explicit_self_recall_can_use_profile_memory_without_word_overlap(tmp_path):
    async def run():
        service = MemoryV3Service(str(tmp_path / "v3.db"))
        await service.put(MemoryV3Item.personal(
            chat_id=-100, user_id=1, content="نام کاربر مهراسه است", kind="profile",
            importance=.9, confidence=1.0, source_message_ids=(1,),
        ))
        context, _ = await service.context(message(text="اسم من چیه؟"))
        assert "نام کاربر مهراسه است" in context

    asyncio.run(run())


def test_memory_boundary_forwards_identity_lookup():
    class Backend:
        async def context(self, message, **kwargs):
            self.kwargs = kwargs
            return "", {}

    async def run():
        backend = Backend()
        service = MemoryService(backend)
        await service.context(message(), target_user_id=2, identity_lookup=True)
        assert backend.kwargs["target_user_id"] == 2
        assert backend.kwargs["identity_lookup"] is True

    asyncio.run(run())


def test_negative_or_question_goal_is_not_persisted(tmp_path):
    async def run():
        for index, text in enumerate((
            "نمی‌خوام پزشکی بخونم",
            "فکر می‌کنی می‌خوام پزشکی بخونم؟",
            "قرار شد فردا جلسه بگذاریم",
        )):
            service = MemoryV3Service(str(tmp_path / f"negative-{index}.db"))
            current = message(text=text)
            await service.observe(current)
            state = await service.session_state(current)
            assert state["user_goal"] is None, text

    asyncio.run(run())


def test_reported_or_quoted_preference_is_not_personal_memory(tmp_path):
    async def run():
        for index, text in enumerate((
            "دوستم گفت ترجیح میدم مهاجرت کنم",
            "او نوشت: «ترجیح میدم جواب کوتاه باشه»",
        )):
            service = MemoryV3Service(str(tmp_path / f"reported-{index}.db"))
            await service.observe(message(text=text))
            assert service.count_items() == 0, text

    asyncio.run(run())


def test_direct_first_person_preference_and_goal_are_preserved(tmp_path):
    async def run():
        preference_service = MemoryV3Service(str(tmp_path / "preference.db"))
        await preference_service.observe(message(text="ترجیح میدم جواب‌ها کوتاه باشن"))
        context, _ = await preference_service.context(message(text="چی از من یادت هست؟"))
        assert "جواب‌ها کوتاه باشن" in context

        goal_service = MemoryV3Service(str(tmp_path / "goal.db"))
        goal_message = message(text="میخوام پایتون یاد بگیرم")
        await goal_service.observe(goal_message)
        state = await goal_service.session_state(goal_message)
        assert state["user_goal"] == "پایتون یاد بگیرم"

    asyncio.run(run())
