"""Conservative, privacy-preserving social decision layer for Zero.

This module deliberately uses local rules and aggregate group state: it does not
profile members, infer sensitive traits, or call an LLM to decide whether to
speak.  Silence is the default when context is uncertain.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from .models import IncomingMessage
from .storage import ZeroStore

logger = logging.getLogger(__name__)

_NEGATIVE = re.compile(r'(?:ساکت شو|کمتر حرف بزن|زر نزن|خفه شو|زیادی حرف می.?زنی|لازم نیست جواب بدی|ول کن|اسپم نکن|باز شروع کرد|again\?|\bstop\b|shut up|enough)', re.I)
_POSITIVE = re.compile(r'(?:ممنون|مرسی|عالی بود|خوب گفتی|دمت گرم|درست گفتی|آفرین)', re.I)
_SAD = re.compile(r'(?:ناراحت|غم|گریه|افسرده|خراب شد|مرگ|فوت|تسلیت|سوگ|بیمارستان)', re.I)
_CONFLICT = re.compile(r'(?:دعوا|درگیری|فحش|توهین|تهدید|جنگ|نفرت|حرومزاده|احمق)', re.I)
_TECHNICAL = re.compile(r'(?:پایتون|python|کد|code|ارور|خطا|exception|traceback|api|html|sql|سرور|docker|linux|deploy|باگ|bug)', re.I)
_FUNNY = re.compile(r'(?:جوک|خنده.?دار|😂|🤣|خخ|هاها|lol)', re.I)
_HELP = re.compile(r'(?:کمک|چطور|چجوری|چگونه|\?|؟)', re.I)
_PROJECT = re.compile(r'(?:دارم .*?(?:می.?سازم|ساختم)|پروژه|ربات|اپلیکیشن|سایت)', re.I)
_NONZERO_MENTION = re.compile(r'@([\w_]{3,})')
_DIRECT_ZERO_CALL = re.compile(r'(^|[\s،,:;!?؟])(?:زیرو|zero|صفر)(?=$|[\s،,:;!?؟])', re.I)


@dataclass(frozen=True, slots=True)
class SocialDecision:
    confidence: float
    should_reply: bool
    should_react: bool
    should_wait: bool
    should_ask: bool
    should_search: bool
    should_ignore: bool
    reason: str
    emotion: str


def classify_emotion(text: str) -> str:
    text = text or ''
    if _CONFLICT.search(text):
        return 'conflict'
    if _SAD.search(text):
        return 'sad'
    if _TECHNICAL.search(text):
        return 'technical'
    if _FUNNY.search(text):
        return 'funny'
    if _HELP.search(text):
        return 'question'
    return 'neutral'


def parse_awareness_command(parts: list[str]) -> tuple[str, bool | None]:
    args = [part.casefold() for part in parts]
    if not args or args == ['status']:
        return 'status', None
    if len(args) == 1 and args[0] in {'on', 'off'}:
        return 'social_awareness_enabled', args[0] == 'on'
    names = {
        'curiosity': 'curiosity_enabled', 'delay': 'human_delay_enabled', 'silence': 'silence_engine_enabled',
        'emotion': 'emotion_awareness_enabled', 'reaction': 'reaction_awareness_enabled',
    }
    if len(args) == 2 and args[0] in names and args[1] in {'on', 'off'}:
        return names[args[0]], args[1] == 'on'
    raise ValueError('Usage: /zero awareness [status|on|off|curiosity on/off|delay on/off|silence on/off|emotion on/off|reaction on/off]')


class SocialAwareness:
    """Central social gate.  Defaults are conservative and DB settings override them."""

    def __init__(self, store: ZeroStore | None, *, random_value: float = 1.0):
        self.store = store
        self.random_value = random_value

    @staticmethod
    def _disabled() -> SocialDecision:
        return SocialDecision(1.0, True, False, False, False, False, False, 'disabled', 'neutral')

    def evaluate(self, message: IncomingMessage, *, state: dict[str, Any] | None = None) -> SocialDecision:
        text = message.text or ''
        lowered = text.casefold()
        emotion = classify_emotion(text)
        confidence = float((state or {}).get('social_confidence', 1.0))
        direct_to_other = bool(_NONZERO_MENTION.search(text)) and not message.mention_zero
        # A reply to anyone except Zero is a direct conversation, not an invitation.
        if direct_to_other or (message.reply_text and not message.reply_to_zero):
            return SocialDecision(confidence, False, False, True, False, False, True, 'conversation_in_progress', emotion)
        if emotion in {'sad', 'conflict'}:
            return SocialDecision(confidence, False, False, True, False, False, True, f'emotion_{emotion}', emotion)
        if emotion == 'funny' and not message.mention_zero and not message.reply_to_zero:
            return SocialDecision(confidence, False, True, False, False, False, True, 'reaction_preferred', emotion)
        explicit = message.mention_zero or message.reply_to_zero
        if explicit:
            return SocialDecision(confidence, True, False, True, False, False, False, 'direct_request', emotion)
        # A rare, bounded starter question for clearly interesting projects only.
        if _PROJECT.search(text) and emotion not in {'technical', 'question'} and self.random_value < 0.08:
            return SocialDecision(confidence, True, False, True, True, False, False, 'bounded_curiosity', emotion)
        return SocialDecision(confidence, False, False, False, False, False, True, 'not_addressed', emotion)

    async def _active_human_conversation(self, message: IncomingMessage, *, window_seconds: int = 180) -> bool:
        if not self.store or message.mention_zero or message.reply_to_zero or message.sender_is_bot:
            return False
        recent = await self.store.get_recent(message.chat_id, limit=8)
        cutoff = int(time.time()) - max(1, int(window_seconds))
        turns: list[int] = []
        current_seen = False
        for row in recent:
            role = str(row.get('role', '')).casefold()
            if role == 'assistant':
                turns.clear()
                current_seen = False
                continue
            if role != 'user' or int(row.get('created_at', 0) or 0) < cutoff:
                continue
            label = str(row.get('sender_label', '')).casefold().lstrip('@')
            if label.endswith('bot') or label.endswith('bot_'):
                continue
            sender_id = int(row.get('sender_id', 0) or 0)
            if not sender_id:
                continue
            row_message_id = int(row.get('telegram_message_id', 0) or 0)
            if message.message_id and row_message_id == int(message.message_id) and sender_id == int(message.sender_id):
                current_seen = True
            if not turns or turns[-1] != sender_id:
                turns.append(sender_id)
        if not current_seen and (not turns or turns[-1] != int(message.sender_id)):
            turns.append(int(message.sender_id))
        return len(turns) >= 2 and len(set(turns[-4:])) >= 2

    @staticmethod
    def allows_autonomous_interjection(message: IncomingMessage, decision: SocialDecision) -> bool:
        if (message.mention_zero or message.reply_to_zero or message.sender_is_bot or message.reply_text
                or message.media_type or message.is_forwarded or message.is_service_message):
            return False
        if decision.reason not in {'not_addressed', 'bounded_curiosity'}:
            return False
        if decision.emotion != 'neutral' or decision.should_react or decision.should_search:
            return False
        if decision.confidence < 0.65:
            return False
        return bool(_PROJECT.search(message.text or ''))

    async def enabled(self, key: str, default: bool = True) -> bool:
        if not self.store:
            return default
        raw = await self.store.get_setting(key)
        if raw is None or raw in {'', 'null', 'None'}:
            return default
        return raw.strip().lower() in {'1', 'true', 'yes', 'on'}

    async def decide(self, message: IncomingMessage) -> SocialDecision:
        if not await self.enabled('social_awareness_enabled', True):
            return self._disabled()
        state = await self.group_state(message.chat_id)
        short = await self.store.get_short_term_context(message.chat_id) if self.store else {}
        logger.info('SOCIAL_GROUP_CONTEXT trace_id=%s reason=aggregate_group_context confidence=%.2f chosen_action=observe reputation=%s short_topic=%s short_mood=%s', message.trace_id or '-', float(state.get('social_confidence', 1.0)), state.get('social_reputation', 0), short.get('active_topic', ''), short.get('mood', 'neutral'))
        decision = self.evaluate(message, state=state)
        explicit = bool(message.mention_zero or message.reply_to_zero)
        if not explicit and await self._active_human_conversation(message):
            decision = SocialDecision(
                decision.confidence, False, False, True, False, False, True,
                'active_human_conversation', decision.emotion,
            )
        if short and not explicit and short.get('mood') in {'conflict', 'sad'}:
            decision = SocialDecision(decision.confidence, False, False, True, False, False, True, f'short_{short.get("mood")}', short.get('mood'))
        elif short and not explicit and float(short.get('negative_feedback_score', 0) or 0) >= 3:
            decision = SocialDecision(decision.confidence, False, False, True, False, False, True, 'short_negative_feedback', decision.emotion)
        if decision.should_ask and not await self.enabled('curiosity_enabled', True):
            decision = SocialDecision(decision.confidence, False, False, False, False, False, True, 'curiosity_disabled', decision.emotion)
        if decision.should_wait and not await self.enabled('silence_engine_enabled', True):
            decision = SocialDecision(decision.confidence, decision.should_reply, decision.should_react, False, decision.should_ask, decision.should_search, decision.should_ignore, decision.reason, decision.emotion)
        logger.info('SOCIAL_%s trace_id=%s reason=%s confidence=%.2f chosen_action=%s emotion=%s',
                    'IGNORE' if decision.should_ignore else 'WAIT' if decision.should_wait else 'REPLY',
                    message.trace_id or '-', decision.reason, decision.confidence,
                    'reply' if decision.should_reply else 'react' if decision.should_react else 'ignore', decision.emotion)
        logger.info('SOCIAL_EMOTION trace_id=%s reason=%s confidence=%.2f chosen_action=%s', message.trace_id or '-', decision.emotion, decision.confidence, 'classify')
        return decision

    async def group_state(self, chat_id: int) -> dict[str, Any]:
        if not self.store:
            return {'social_reputation': 0, 'social_confidence': 1.0}
        return await self.store.get_social_group_state(chat_id)

    async def allow_action(self, chat_id: int, action: str, *, trace_id: str = '-') -> bool:
        if not await self.enabled('social_awareness_enabled', True):
            return True
        state = await self.group_state(chat_id)
        # Under sustained feedback, autonomous outreach is intentionally paused.
        if action in {'welcome', 'inactive_ping', 'leave_followup', 'starter', 'curiosity'} and float(state['social_confidence']) < 0.65:
            logger.info('SOCIAL_SKIP trace_id=%s reason=low_reputation confidence=%.2f chosen_action=%s', trace_id, state['social_confidence'], action)
            return False
        return True

    async def record_feedback(self, chat_id: int, sender_id: int, text: str) -> None:
        if not self.store:
            return
        kind = 'negative' if _NEGATIVE.search(text or '') else 'positive' if _POSITIVE.search(text or '') else ''
        if not kind:
            return
        await self.store.add_social_feedback_event(chat_id, sender_id, kind)
        distinct = await self.store.count_social_feedback_users(chat_id, kind, 86400)
        if kind == 'negative':
            # One rude member cannot steer Zero. Three distinct same-day signals can.
            state = await self.store.adjust_social_group_state(chat_id, reputation_delta=-1 if distinct >= 3 else 0, negative_delta=1 if distinct >= 3 else 0)
            logger.info('SOCIAL_FEEDBACK_NEGATIVE chat_id=%s sender_id=%s distinct_users=%s confidence=%.2f', chat_id, sender_id, distinct, state['social_confidence'])
        else:
            state = await self.store.adjust_social_group_state(chat_id, reputation_delta=1 if distinct >= 2 else 0, positive_delta=1, accepted_delta=1)
            logger.info('SOCIAL_FEEDBACK_POSITIVE chat_id=%s sender_id=%s distinct_users=%s confidence=%.2f', chat_id, sender_id, distinct, state['social_confidence'])

    async def record_reaction_feedback(self, chat_id: int, *, positive: int, negative: int) -> None:
        if not self.store:
            return
        if positive:
            await self.store.adjust_social_group_state(chat_id, positive_delta=1, accepted_delta=1)
        if negative >= 3:
            state = await self.store.adjust_social_group_state(chat_id, reputation_delta=-1, negative_delta=1, ignored_delta=1)
            logger.info('SOCIAL_REPUTATION_CHANGED chat_id=%s reason=negative_reactions confidence=%.2f', chat_id, state['social_confidence'])

    async def superseded_by_recent_human(self, message: IncomingMessage) -> bool:
        """After a human delay, avoid repeating a fresh member answer in the same thread."""
        if not self.store or message.reply_to_zero or message.mention_zero or _DIRECT_ZERO_CALL.search(message.text or ''):
            return False
        recent = await self.store.get_recent(message.chat_id, limit=3)
        if not recent:
            return False
        newest = recent[-1]
        if newest.get('role') != 'user' or str(newest.get('text', '')).strip() == (message.text or '').strip():
            return False
        # We only know aggregate chat history, never profile the answering member.
        logger.info('SOCIAL_WAIT trace_id=%s reason=answer_arrived_during_delay confidence=0.90 chosen_action=ignore', message.trace_id or '-')
        return True

    async def reflection(self, chat_id: int) -> dict[str, Any]:
        state = await self.group_state(chat_id)
        accepted = int(state['reply_acceptance_count'])
        ignored = int(state['ignored_reply_count'])
        total = accepted + ignored
        state['reply_acceptance_rate'] = round(accepted / total * 100, 1) if total else 0.0
        logger.info('SOCIAL_SELF_REFLECTION chat_id=%s acceptance_rate=%s positive=%s negative=%s confidence=%.2f', chat_id, state['reply_acceptance_rate'], state['positive_feedback_count'], state['negative_feedback_count'], state['social_confidence'])
        return state
