from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .storage import ZeroStore

logger = logging.getLogger('zero.limit_challenge')


STATIC_POOLS: dict[int, dict[str, Any]] = {
    2: {
        'items': [
            {'answers': ['دریچه'], 'hint': 'یک کلمه ۵ حرفی فارسی، ۳ نقطه دارد و معنی‌اش پنجره یا بازشوی کوچک است.'},
            {'answers': ['تایمر'], 'hint': 'یک کلمه ۵ حرفی فارسی است و معنی‌اش زمان‌سنج است.'},
            {'answers': ['مدرسه'], 'hint': 'یک کلمه ۵ حرفی فارسی، با «م» شروع می‌شود و جای درس خواندن است.'},
        ],
    },
    3: {
        'items': [
            {'answers': ['چاله'], 'question': 'چیستان: آن چیست که هرچه بیشتر از آن برداری، بزرگ‌تر می‌شود؟'},
            {'answers': ['سایه'], 'question': 'چیستان: همیشه همراه تو هستم، ولی در تاریکی گم می‌شوم. من چیست؟'},
            {'answers': ['نام'], 'question': 'چیستان: مال تو هستم، اما دیگران بیشتر از خودت از من استفاده می‌کنند. من چیست؟'},
        ],
    },
    5: {
        'items': [
            {'answers': ['html', 'hypertext markup language'], 'question': 'سؤال فنی: HTML مخفف چیست؟'},
            {'answers': ['guido van rossum', 'گیدو ون روسوم', 'گیدو فان روسوم'], 'question': 'سؤال فنی: Python را چه کسی ساخت؟'},
            {'answers': ['application programming interface', 'رابط برنامه نویسی کاربردی', 'رابط برنامه‌نویسی کاربردی'], 'question': 'سؤال فنی: API یعنی چه؟'},
        ],
    },
}


@dataclass(frozen=True)
class LimitChallengeResult:
    kind: str
    text: str
    stage: int | None = None
    reward: int = 0
    bonus_quota: int = 0
    answer_for_test: str = ''


def normalize_answer(value: str) -> str:
    value = (value or '').strip().lower()
    value = value.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789'))
    value = value.replace('ي', 'ی').replace('ك', 'ک').replace('‌', ' ')
    return re.sub(r'[^\w\s]', '', value, flags=re.UNICODE).strip()


def answer_hash(answers: list[str]) -> str:
    payload = '|'.join(sorted(normalize_answer(answer) for answer in answers))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


class LimitChallengeService:
    """DB-backed, no-LLM bonus quota game triggered only by a real limit hit."""

    def __init__(self, store: ZeroStore, *, rng: random.Random | None = None,
                 active_timeout_seconds: int = 180, clock: Callable[[], int] | None = None):
        self.store = store
        self.rng = rng or random.SystemRandom()
        self.active_timeout_seconds = active_timeout_seconds
        # Injectable wall clock so timeout behaviour is deterministic in tests.
        self.clock = clock or (lambda: int(time.time()))
        # Deliberately stays zero: static pools are the fallback and the only generator.
        self.llm_generation_calls = 0

    async def enabled(self) -> bool:
        raw = await self.store.get_setting('limit_challenge_enabled', 'true')
        return str(raw).lower() not in {'false', '0', 'off', 'no', 'null', 'none'}

    async def _progress(self, user_id: int, chat_id: int) -> dict[str, Any]:
        today = datetime.now().strftime('%Y-%m-%d')
        progress = await self.store.get_limit_challenge_progress(user_id, chat_id)
        if not progress:
            return await self.store.upsert_limit_challenge_progress(user_id, chat_id, day_key=today)
        reset_daily = (await self.store.get_setting('limit_challenge_reset_daily', 'true')).lower() != 'false'
        if not progress.get('day_key'):
            return await self.store.upsert_limit_challenge_progress(user_id, chat_id, day_key=today)
        if reset_daily and progress.get('day_key') != today:
            await self.store.close_limit_challenge_active(user_id, chat_id, 'reset')
            return await self.store.upsert_limit_challenge_progress(
                user_id, chat_id, current_stage=1, completed_stages=[], reward_step=0,
                bonus_quota=0, daily_completed_count=0, day_key=today,
            )
        return progress

    async def ensure_templates(self, stage: int) -> None:
        if stage not in STATIC_POOLS:
            return
        created = await self.store.ensure_limit_challenge_template(stage, 'static-v1', 'static_fallback_pool', STATIC_POOLS[stage])
        if created:
            # This is a DB cache generation, never a model call.
            logger.info('LIMIT_CHALLENGE_TEMPLATE_GENERATED stage=%s source=static_fallback llm=false', stage)

    async def _offer_stage(self, user_id: int, chat_id: int, stage: int) -> LimitChallengeResult:
        if stage == 1:
            a, b, c = (self.rng.randint(2, 9) for _ in range(3))
            question = f'🧩 مرحله ۱ — {a} + {b} × {c} = ؟\nجواب را تا ۳ دقیقه بفرست. +۵ پیام جایزه'
            answers = [str(a + b * c)]
        elif stage in STATIC_POOLS:
            await self.ensure_templates(stage)
            templates = await self.store.list_limit_challenge_templates(stage)
            if not templates:  # Defensive: no LLM, no crash.
                pool = STATIC_POOLS[stage]
                template_id = 'memory-fallback'
            else:
                selected_template = self.rng.choice(templates)
                pool = json.loads(selected_template['template_json'])
                template_id = selected_template['template_id']
                await self.store.mark_limit_challenge_template_used(stage, template_id)
                logger.info('LIMIT_CHALLENGE_TEMPLATE_REUSED stage=%s template_id=%s', stage, template_id)
            item = self.rng.choice(pool['items'])
            answers = list(item['answers'])
            if stage == 2:
                question = f'🧩 مرحله ۲ — حدس کلمه\n{item["hint"]}\nجواب را تا ۳ دقیقه بفرست. +۴ پیام جایزه'
            elif stage == 3:
                question = f'🧩 مرحله ۳\n{item["question"]}\nجواب را تا ۳ دقیقه بفرست. +۳ پیام جایزه'
            else:
                question = f'🧩 مرحله ۵\n{item["question"]}\nجواب را تا ۳ دقیقه بفرست. +۱ پیام جایزه'
        else:
            raise ValueError(f'unsupported challenge stage {stage}')

        now = self.clock()
        await self.store.create_limit_challenge_active(
            user_id, chat_id, stage=stage, challenge_id=uuid.uuid4().hex,
            question=question, answer=json.dumps(answers, ensure_ascii=False),
            answer_hash=answer_hash(answers), expires_at=now + self.active_timeout_seconds,
        )
        await self.store.upsert_limit_challenge_progress(user_id, chat_id, last_challenge_at=now)
        logger.info('LIMIT_CHALLENGE_OFFERED user_id=%s chat_id=%s stage=%s', user_id, chat_id, stage)
        return LimitChallengeResult('offered', question, stage=stage, answer_for_test=answers[0])

    async def _complete_stage(self, user_id: int, chat_id: int, progress: dict[str, Any], stage: int, reward: int) -> LimitChallengeResult:
        completed = json.loads(progress.get('completed_stages_json', '[]'))
        if stage not in completed:
            completed.append(stage)
        next_stage = stage + 1
        bonus = int(progress.get('bonus_quota', 0)) + reward
        updated = await self.store.upsert_limit_challenge_progress(
            user_id, chat_id, current_stage=next_stage, completed_stages=completed,
            reward_step=len(completed), bonus_quota=bonus,
            daily_completed_count=int(progress.get('daily_completed_count', 0)) + 1,
        )
        logger.info('LIMIT_CHALLENGE_STAGE_COMPLETED user_id=%s chat_id=%s stage=%s reward=%s bonus_quota=%s', user_id, chat_id, stage, reward, bonus)
        return LimitChallengeResult('correct', f'✅ درست بود! +{reward} پیام جایزه گرفتی.', stage=stage, reward=reward, bonus_quota=updated['bonus_quota'])

    async def _roll_dice(self, user_id: int, chat_id: int, progress: dict[str, Any]) -> LimitChallengeResult:
        dice = self.rng.randint(1, 6)
        reward = dice if dice % 2 == 0 else 0
        result = await self._complete_stage(user_id, chat_id, progress, 4, reward)
        text = (f'🎲 تاس افتاد روی {dice}، چون زوج بود +{dice} پیام گرفتی.' if reward
                else f'🎲 تاس افتاد روی {dice}، این بار هیچی. زندگی همینه، بی‌رحم و بی‌منطق.')
        return LimitChallengeResult('dice', text, stage=4, reward=reward, bonus_quota=result.bonus_quota)

    async def handle_limit_hit(self, user_id: int, chat_id: int, text: str) -> LimitChallengeResult:
        if not await self.enabled():
            logger.info('LIMIT_CHALLENGE_SKIPPED user_id=%s chat_id=%s reason=disabled', user_id, chat_id)
            return LimitChallengeResult('disabled', '')
        progress = await self._progress(user_id, chat_id)
        bonus = int(progress['bonus_quota'])
        if bonus > 0:
            updated = await self.store.upsert_limit_challenge_progress(user_id, chat_id, bonus_quota=bonus - 1)
            logger.info('LIMIT_BONUS_USED user_id=%s chat_id=%s remaining=%s', user_id, chat_id, updated['bonus_quota'])
            return LimitChallengeResult('bonus_used', '🎁 یک پیام از جایزه‌ات مصرف شد.', bonus_quota=updated['bonus_quota'])

        if await self.store.expire_limit_challenge_active(user_id, chat_id, now=self.clock()):
            logger.info('LIMIT_CHALLENGE_EXPIRED user_id=%s chat_id=%s', user_id, chat_id)
        active = await self.store.get_limit_challenge_active(user_id, chat_id)
        if active:
            try:
                answers = json.loads(active['answer'])
            except (TypeError, json.JSONDecodeError):
                answers = []
            if normalize_answer(text) in {normalize_answer(answer) for answer in answers}:
                await self.store.close_limit_challenge_active(user_id, chat_id, 'completed')
                logger.info('LIMIT_CHALLENGE_ANSWER_CORRECT user_id=%s chat_id=%s stage=%s', user_id, chat_id, active['stage'])
                rewards = {1: 5, 2: 4, 3: 3, 5: 1}
                return await self._complete_stage(user_id, chat_id, progress, int(active['stage']), rewards[int(active['stage'])])
            attempts, failed = await self.store.record_limit_challenge_wrong_answer(user_id, chat_id)
            logger.info('LIMIT_CHALLENGE_ANSWER_WRONG user_id=%s chat_id=%s stage=%s attempts=%s', user_id, chat_id, active['stage'], attempts)
            if failed:
                return LimitChallengeResult('failed', '❌ دو بار تلاش کردی؛ این مرحله باز هم سر جاشه. دفعه بعد دوباره امتحان کن.', stage=int(active['stage']))
            return LimitChallengeResult('wrong', '❌ نه، یک تلاش دیگر داری.', stage=int(active['stage']))

        stage = int(progress['current_stage'])
        if stage > 5:
            logger.info('LIMIT_CHALLENGE_NO_MORE_REWARDS user_id=%s chat_id=%s', user_id, chat_id)
            return LimitChallengeResult('no_more_rewards', 'امروز همه چالش‌ها رو رفتی، دیگه جایزه‌ای نمونده.')
        if stage == 4:
            return await self._roll_dice(user_id, chat_id, progress)
        return await self._offer_stage(user_id, chat_id, stage)
