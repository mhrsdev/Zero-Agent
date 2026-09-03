"""Mute, interject, rate-limit and trigger gates extracted from ZeroBrain."""
from __future__ import annotations

import json
import logging
import random
import time

from .automation import automation_disabled
from .brain_media import is_media_followup_text, sticker_retry_feedback, user_requests_gif, user_requests_sticker
from .moderation import is_spammy
from .models import Decision, IncomingMessage
from .security import Intent, classify_intent, fixed_security_reply, looks_abusive
from .tenancy import GroupStateError, TenancyError
from .triggers import decide_reply, is_triggered

logger = logging.getLogger('zero.brain')


class BrainPolicyMixin:
    """Pre-reply policy. Mixed into ZeroBrain."""

    async def _mode(self) -> str:
        return await self.store.get_setting('mode', self.config.persona.default_mode) or self.config.persona.default_mode

    async def _muted_map(self) -> dict[str, int]:
        raw = await self.store.get_setting('muted_users', '{}')
        try:
            return json.loads(raw or '{}')
        except Exception:
            return {}

    async def _is_muted(self, sender_id: int) -> bool:
        muted = await self._muted_map()
        return int(muted.get(str(sender_id), 0) or 0) > int(time.time())

    async def _should_interject(self, message: IncomingMessage, social_decision) -> bool:
        policy = self._group_policy_for(message)
        if not policy.automation_interject:
            return False
        if policy.quiet_hours is not None and policy.quiet_hours.active():
            return False
        if await automation_disabled(self.store):
            return False
        if not self.social_awareness.allows_autonomous_interjection(message, social_decision):
            return False
        if not self.config.persona.allow_random_interject:
            return False
        if not await self.social_awareness.enabled('curiosity_enabled', True):
            return False
        if not await self.social_awareness.allow_action(message.chat_id, 'curiosity', trace_id=message.trace_id or '-'):
            return False
        last = float(await self.store.get_setting(f'last_interject_at:{int(message.chat_id)}', '0') or 0)
        if time.time() - last < self.config.persona.min_interject_gap_seconds:
            return False
        return random.random() < self.config.persona.interject_probability

    async def _check_nova_conversation_limit(self, message: IncomingMessage) -> tuple[bool, str]:
        if not message.sender_is_bot:
            return True, ''
        bot_count = await self.store.count_rate_events(message.sender_id, 'bot_reply', self.config.policy.bot_reply_cooldown_seconds, message.chat_id)
        if bot_count >= self.config.policy.bot_max_chain_turns:
            return False, 'bot_chain_limit'
        nova_count = await self.store.count_rate_events(message.sender_id, 'nova_msg', self.config.policy.nova_window_seconds, message.chat_id)
        if nova_count >= self.config.policy.nova_max_messages_per_window:
            return False, 'nova_window_limit'
        return True, ''

    async def _media_followup_info(self, message: IncomingMessage) -> dict | None:
        if not is_media_followup_text(message.text):
            return None
        rows = await self.store.get_recent_media_context(message.chat_id, message.text, limit=10)
        now = int(time.time())
        for row in rows:
            if int(row.get('sender_id') or 0) != int(message.sender_id):
                continue
            age = max(0, now - int(row.get('created_at') or now))
            if age <= 120 and (message.reply_to_message_id is None or int(message.reply_to_message_id) == int(row.get('message_id') or 0)):
                return {'media_message_id': int(row['message_id']), 'media_type': row.get('media_type', 'media'), 'age_seconds': age}
        return None

    async def _pre_check(self, message: IncomingMessage) -> tuple[Decision | None, str]:
        if self.tenancy is not None:
            try:
                self._memory_for(message)
            except (TenancyError, GroupStateError, ValueError):
                return Decision(False, 'tenancy_unresolved'), ''
        policy = self._group_policy_for(message)
        if not policy.enabled:
            return Decision(False, 'group_disabled'), ''
        if policy.observe_only:
            return Decision(False, 'observe_only'), ''
        topics = getattr(policy, 'forum_topics', None) or None
        if topics and message.thread_id is not None:
            tid = int(message.thread_id)
            allow = topics.get('allow') if isinstance(topics, dict) else None
            deny = topics.get('deny') if isinstance(topics, dict) else None
            if isinstance(allow, list) and allow and tid not in {int(x) for x in allow}:
                return Decision(False, 'forum_topic_denied'), ''
            if isinstance(deny, list) and tid in {int(x) for x in deny}:
                return Decision(False, 'forum_topic_denied'), ''
        if policy.reply_mode == 'silent':
            return Decision(False, 'group_silent'), ''
        addressed = bool(message.mention_zero or message.reply_to_zero)
        if policy.reply_mode == 'mention_only' and not addressed:
            return Decision(False, 'mention_only'), ''
        if await self._is_muted(message.sender_id):
            return Decision(False, 'muted'), ''
        recent_user_count = await self.store.count_rate_events(message.sender_id, 'reply', self.config.policy.user_window_seconds, message.chat_id)
        daily_user_count = await self.store.count_rate_events(message.sender_id, 'reply', 24 * 3600, message.chat_id)
        triggered = is_triggered(message, self.config, self.config.listener.account_username)
        media_followup = await self._media_followup_info(message)
        direct_sticker_request = user_requests_sticker(message.text)
        direct_gif_request = user_requests_gif(message.text)
        retry_media_request = sticker_retry_feedback(message.text) and bool(message.reply_to_zero or media_followup)
        if direct_sticker_request or direct_gif_request or retry_media_request:
            triggered = True
            logger.info(
                "MEDIA_INTENT_TRIGGERED trace_id=%s chat_id=%s sender_id=%s sticker=%s gif=%s retry=%s reply_to_zero=%s media_followup=%s",
                message.trace_id or "-", message.chat_id, message.sender_id,
                direct_sticker_request, direct_gif_request, retry_media_request,
                message.reply_to_zero, bool(media_followup),
            )
        media_followup_bypass = False
        if is_media_followup_text(message.text):
            if media_followup:
                allowed, used = await self.store.try_reserve_rate_event(message.sender_id, 'media_followup_bypass', 120, 1, chat_id=message.chat_id)
                if allowed:
                    media_followup_bypass = True
                    logger.info('MEDIA_FOLLOWUP_DETECTED trace_id=%s chat_id=%s sender_id=%s current_message_id=%s media_message_id=%s media_type=%s age_seconds=%s reason=semantic_recent_same_sender_chat', message.trace_id or '-', message.chat_id, message.sender_id, message.message_id, media_followup['media_message_id'], media_followup['media_type'], media_followup['age_seconds'])
                    logger.info('MEDIA_FOLLOWUP_SPAM_BYPASS_ALLOWED trace_id=%s chat_id=%s sender_id=%s current_message_id=%s media_message_id=%s media_type=%s age_seconds=%s reason=single_bypass_window', message.trace_id or '-', message.chat_id, message.sender_id, message.message_id, media_followup['media_message_id'], media_followup['media_type'], media_followup['age_seconds'])
                else:
                    logger.info('MEDIA_FOLLOWUP_SPAM_BYPASS_DENIED trace_id=%s chat_id=%s sender_id=%s current_message_id=%s media_message_id=%s media_type=%s age_seconds=%s reason=bypass_already_used', message.trace_id or '-', message.chat_id, message.sender_id, message.message_id, media_followup['media_message_id'], media_followup['media_type'], media_followup['age_seconds'])
            else:
                logger.info('MEDIA_FOLLOWUP_SPAM_BYPASS_DENIED trace_id=%s chat_id=%s sender_id=%s current_message_id=%s media_message_id=%s media_type=%s age_seconds=%s reason=no_recent_same_sender_media', message.trace_id or '-', message.chat_id, message.sender_id, message.message_id, '-', '-', -1)
        # Social eligibility is resolved below; randomness never runs before that gate.
        should_interject = False
        # The challenge exists only at the configured, real rate-limit boundary.
        # Owner and bot safeguards retain their existing bypass/separate policies.
        window_limit_reached = self.config.policy.user_max_replies_per_window > 0 and recent_user_count >= self.config.policy.user_max_replies_per_window
        daily_limit_reached = self.config.policy.user_max_replies_per_day > 0 and daily_user_count >= self.config.policy.user_max_replies_per_day
        hard_limit = (
            self.config.policy.anti_spam_enabled
            and not message.sender_is_bot
            and message.sender_id != self.config.owner_user_id
            and (window_limit_reached or daily_limit_reached)
        )
        bonus_used = False
        active_challenge = await self.store.get_limit_challenge_active(message.sender_id, message.chat_id) if hard_limit else None
        if hard_limit and (triggered or should_interject or active_challenge):
            logger.info('LIMIT_HIT user_id=%s chat_id=%s window_count=%s daily_count=%s',
                        message.sender_id, message.chat_id, recent_user_count, daily_user_count)
            game = await self.limit_challenges.handle_limit_hit(message.sender_id, message.chat_id, message.text)
            if game.kind == 'bonus_used':
                # Quota is consumed, then this message follows the ordinary reply path.
                bonus_used = True
            elif game.kind != 'disabled':
                return Decision(True, 'limit_challenge'), game.text
            else:
                return Decision(True, 'user_rate_limit'), 'یه کم صبر کن؛ لیمیت پیام‌هات پر شده.'
        social_decision = await self.social_awareness.decide(message)
        if not triggered:
            should_interject = await self._should_interject(message, social_decision)
        if social_decision.should_ignore and not triggered and not should_interject:
            return Decision(False, f'social_{social_decision.reason}'), ''
        spam_blocked = self.config.policy.anti_spam_enabled and is_spammy(message.text, recent_user_count)
        if message.sender_id == self.config.owner_user_id or bonus_used or media_followup_bypass:
            spam_blocked = False
        decision = decide_reply(message, triggered, should_interject, spam_blocked)
        if not decision.should_reply:
            return decision, ''
        intent = classify_intent(message.text, message.reply_text)
        logger.debug('INTENT classified=%s text=%r', intent.name, message.text[:80])
        if intent == Intent.DANGEROUS_SECRET_REQUEST:
            logger.info('SECURITY_BLOCKED intent=%s sender=%s', intent.name, message.sender_id)
            return Decision(True, 'security'), fixed_security_reply()
        if intent == Intent.DANGEROUS_EXECUTION_REQUEST:
            logger.info('SECURITY_BLOCKED intent=%s sender=%s', intent.name, message.sender_id)
            return Decision(True, 'security'), fixed_security_reply()
        if looks_abusive(message.text):
            await self.store.add_rate_event(message.sender_id, 'abuse', message.chat_id)
        if spam_blocked:
            return Decision(True, 'spam_soft_block'), 'کمتر اسپم کن که جواب بهتر بگیری 🙂'
        nova_ok, reason = await self._check_nova_conversation_limit(message)
        if not nova_ok:
            if reason == 'nova_window_limit':
                return Decision(True, 'nova_limit'), 'نوا جان، من فعلا لیمیت خوردم نمیتونم ادامه بدم. بیا بعدا ادامه بدیم '
            if (message.sender_label or '').lstrip('@').lower() == 'mynovachatbot':
                return Decision(True, 'bot_chain_silent'), ''
            return Decision(True, 'bot_chain_limit'), 'مگه شد اینقدر چت کردیم، الان مکث می‌کنم.'
        if message.sender_is_bot and self.config.policy.bot_msg_limit:
            label = (message.sender_label or '').lower()
            if label.endswith('bot') or label.endswith('bot_'):
                count = await self.store.count_rate_events(message.sender_id, 'bot_msg', self.config.policy.bot_msg_window_seconds, message.chat_id)
                if count >= self.config.policy.bot_msg_limit:
                    return Decision(True, 'bot_msg_limit'), f'یه کم صبر کن، الان {self.config.policy.bot_msg_limit} تا پیام داده بودم، بعداً ادامه میدم 😉'
        return decision, ''
