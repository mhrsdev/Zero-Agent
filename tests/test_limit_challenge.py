import asyncio
import random
from pathlib import Path

from zero.limit_challenge import LimitChallengeService
from zero.storage import ZeroStore


def test_staged_limit_challenge_progression_and_bonus_consumption(tmp_path: Path):
    async def scenario():
        store = ZeroStore(str(tmp_path / "zero.db"))
        game = LimitChallengeService(store, rng=random.Random(7))
        user_id, chat_id = 100, -200

        first = await game.handle_limit_hit(user_id, chat_id, "")
        assert first.kind == "offered" and first.stage == 1
        assert "×" in first.text
        assert (await store.get_limit_challenge_active(user_id, chat_id))["stage"] == 1

        correct = await game.handle_limit_hit(user_id, chat_id, first.answer_for_test)
        assert correct.kind == "correct" and correct.reward == 5
        progress = await store.get_limit_challenge_progress(user_id, chat_id)
        assert progress["current_stage"] == 2 and progress["bonus_quota"] == 5

        for remaining in range(4, -1, -1):
            result = await game.handle_limit_hit(user_id, chat_id, "normal message")
            assert result.kind == "bonus_used"
            assert result.bonus_quota == remaining

        second = await game.handle_limit_hit(user_id, chat_id, "")
        assert second.kind == "offered" and second.stage == 2
        assert "۵ حرفی" in second.text
        await game.handle_limit_hit(user_id, chat_id, second.answer_for_test)
        assert (await store.get_limit_challenge_progress(user_id, chat_id))["bonus_quota"] == 4

        for _ in range(4):
            await game.handle_limit_hit(user_id, chat_id, "x")
        third = await game.handle_limit_hit(user_id, chat_id, "")
        assert third.kind == "offered" and third.stage == 3
        await game.handle_limit_hit(user_id, chat_id, third.answer_for_test)
        assert (await store.get_limit_challenge_progress(user_id, chat_id))["bonus_quota"] == 3

        for _ in range(3):
            await game.handle_limit_hit(user_id, chat_id, "x")
        fourth = await game.handle_limit_hit(user_id, chat_id, "")
        assert fourth.kind == "dice" and fourth.stage == 4
        assert (await store.get_limit_challenge_progress(user_id, chat_id))["current_stage"] == 5

        fifth = await game.handle_limit_hit(user_id, chat_id, "")
        assert fifth.kind == "offered" and fifth.stage == 5
        done = await game.handle_limit_hit(user_id, chat_id, fifth.answer_for_test)
        assert done.kind == "correct" and done.reward == 1
        progress = await store.get_limit_challenge_progress(user_id, chat_id)
        assert progress["current_stage"] == 6
        assert (await game.handle_limit_hit(user_id, chat_id, "" )).kind == "bonus_used"
        assert (await game.handle_limit_hit(user_id, chat_id, "" )).kind == "no_more_rewards"

    asyncio.run(scenario())


def test_stage_two_is_hidden_until_stage_one_is_completed(tmp_path: Path):
    async def scenario():
        store = ZeroStore(str(tmp_path / "zero.db"))
        game = LimitChallengeService(store, rng=random.Random(1))
        offered = await game.handle_limit_hit(1, 2, "")
        assert offered.stage == 1
        assert "دریچه" not in offered.text
        assert (await store.get_limit_challenge_progress(1, 2))["current_stage"] == 1
    asyncio.run(scenario())


def test_wrong_answers_max_two_attempts_and_timeout(tmp_path: Path):
    # Deterministic clock: the previous version relied on real elapsed time
    # staying under the 1s timeout between calls, which made the test flaky
    # under load (a slow CI runner let the challenge expire mid-scenario).
    async def scenario():
        store = ZeroStore(str(tmp_path / "zero.db"))
        # Start from the real epoch so the store's own real-time expiry
        # bookkeeping stays consistent with the injected service clock.
        import time as _time
        now = [int(_time.time())]
        game = LimitChallengeService(
            store, rng=random.Random(1), active_timeout_seconds=30,
            clock=lambda: now[0],
        )
        offered = await game.handle_limit_hit(1, 2, "")
        assert offered.kind == "offered"
        assert (await game.handle_limit_hit(1, 2, "wrong")).kind == "wrong"
        final_wrong = await game.handle_limit_hit(1, 2, "still wrong")
        assert final_wrong.kind == "failed"
        assert (await store.get_limit_challenge_progress(1, 2))["current_stage"] == 1

        # A fresh offer after failure...
        again = await game.handle_limit_hit(1, 2, "")
        assert again.kind == "offered" and again.stage == 1
        # ...expires only when the injected clock passes its deadline.
        now[0] += 31
        expired = await game.handle_limit_hit(1, 2, "anything")
        assert expired.kind == "offered" and expired.stage == 1
    asyncio.run(scenario())


def test_templates_are_generated_once_then_reused_without_llm(tmp_path: Path):
    async def scenario():
        store = ZeroStore(str(tmp_path / "zero.db"))
        game = LimitChallengeService(store, rng=random.Random(4))
        await game.ensure_templates(2)
        first = await store.list_limit_challenge_templates(2)
        await game.ensure_templates(2)
        second = await store.list_limit_challenge_templates(2)
        assert len(first) == len(second) == 1
        assert second[0]["usage_count"] == 0
        assert game.llm_generation_calls == 0
        await game._offer_stage(1, 2, 2)
        assert (await store.list_limit_challenge_templates(2))[0]["usage_count"] == 1
    asyncio.run(scenario())


def test_dice_rewards_even_and_odd_without_llm(tmp_path: Path):
    async def scenario():
        store = ZeroStore(str(tmp_path / "zero.db"))
        game = LimitChallengeService(store, rng=random.Random(1))
        await store.upsert_limit_challenge_progress(1, 2, current_stage=4)
        even = await game.handle_limit_hit(1, 2, "")
        assert even.kind == "dice" and even.reward in {0, 2, 4, 6}
        await store.upsert_limit_challenge_progress(3, 4, current_stage=4)
        game.rng = type("OddRng", (), {"randint": staticmethod(lambda _a, _b: 3)})()
        odd = await game.handle_limit_hit(3, 4, "")
        assert odd.reward == 0
        assert game.llm_generation_calls == 0
    asyncio.run(scenario())


def test_only_one_active_challenge_per_user(tmp_path: Path):
    async def scenario():
        store = ZeroStore(str(tmp_path / "zero.db"))
        game = LimitChallengeService(store, rng=random.Random(1))
        first = await game.handle_limit_hit(1, 2, "")
        second = await game.handle_limit_hit(1, 2, "")
        assert first.kind == "offered"
        assert second.kind == "wrong"
        assert (await store.count_limit_challenge_active(1, 2)) == 1
    asyncio.run(scenario())
