"""Durable remember paths extracted from ZeroBrain."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import replace
from datetime import datetime
from typing import Any

from .memory import (
    detect_mood,
    detect_topics,
    extract_explicit_long_candidate,
    extract_medium_candidate,
    extract_nickname_correction,
    is_sensitive_memory_text,
    is_untrusted_memory_control_text,
    maybe_extract_memory,
)
from .memory_v3 import MemoryV3Item
from .models import IncomingMessage
from .turn_options import should_skip_private_memory

logger = logging.getLogger('zero.brain')


class BrainMemoryMixin:
    """Persist inbound messages and outbound replies. Mixed into ZeroBrain."""

    async def remember_message(self, message: IncomingMessage, role: str = 'user') -> None:
        # Bot messages remain an archive-only role; never let them enter human-memory paths.
        effective_role = 'bot' if role == 'user' and message.sender_is_bot else role
        if should_skip_private_memory(self.config, message):
            await self.store.append_recent(
                message.chat_id, message.sender_id, message.sender_label, effective_role, message.text,
                platform=message.platform, account_scope=message.account_scope,
                telegram_message_id=message.message_id or None,
                reply_to_message_id=message.reply_to_message_id,
                thread_id=message.thread_id, sender_username=message.sender_username,
                sender_display_name=message.sender_display_name, trace_id=message.trace_id,
            )
            return
        await self.store.append_recent(
            message.chat_id, message.sender_id, message.sender_label, effective_role, message.text,
            platform=message.platform, account_scope=message.account_scope,
            telegram_message_id=message.message_id or None,
            reply_to_message_id=message.reply_to_message_id,
            thread_id=message.thread_id, sender_username=message.sender_username,
            sender_display_name=message.sender_display_name, trace_id=message.trace_id,
        )
        try:
            await self._memory_for(message).record_message(message, role=effective_role)
        except (TenancyError, GroupStateError) as exc:
            logger.warning('MEMORY_TENANCY_SKIPPED trace_id=%s exception_type=%s', message.trace_id or '-', type(exc).__name__)
            return
        if role == 'user' and not message.sender_is_bot:
            await self.store.upsert_profile(
                message.chat_id, message.sender_id, message.sender_label,
                username=message.sender_username, display_name=message.sender_display_name,
            )
        if os.getenv('ZERO_GROUP_DOCUMENT_BUNDLING_ENABLED','false').lower()=='true' and role=='user' and not message.sender_is_bot:
            await self.document_bundles.observe(message)
        if os.getenv('ZERO_PROACTIVE_FOLLOWUP_ENABLED','false').lower()=='true' and role=='user' and not message.sender_is_bot:
            feedback=await self.proactive_followups.feedback.observe(message)
            if feedback.get('recorded'):
                await self.memory_v3.metric(message.trace_id or '-', 'proactive_feedback', {'feedback_type':feedback['feedback_type'],'feedback_recorded':True})
        # Recent rows are an archive/ephemeral turn buffer, not V1 memory injection.
        if not self.v1_memory_runtime_enabled:
            return
        if message.media_type and message.message_id:
            await self.store.record_media_context(message.chat_id, message.message_id, message.sender_id, message.media_type, message.media_caption, message.reply_to_message_id)
            logger.info('MEMORY_SHORT_DETAIL_UPDATED trace_id=%s chat_id=%s message_id=%s media_type=%s', message.trace_id or '-', message.chat_id, message.message_id, message.media_type)
        if is_untrusted_memory_control_text(message.text):
            logger.info('MEMORY_UNTRUSTED_TEXT_IGNORED chat_id=%s sender_id=%s message_id=%s trace_id=%s', message.chat_id, message.sender_id, message.message_id, message.trace_id or '-')
            logger.info('MEMORY_SHORT_SKIPPED trace_id=%s chat_id=%s message_id=%s reason=untrusted_text', message.trace_id or '-', message.chat_id, message.message_id)
            await self.store.memory_audit_event('MEMORY_SHORT_SKIPPED', 'short', message.chat_id, trace_id=message.trace_id or '-', details={'message_id': message.message_id, 'reason': 'untrusted_text'})
            return
        if role == 'user' and not message.sender_is_bot:
            for item in self.semantic_memory.extract_explicit(message.text or ''):
                try:
                    cid = self.semantic_memory.candidate(chat_id=message.chat_id, sender_id=message.sender_id, category=item['category'], key=item['key'], value=item['value'], confidence=item['confidence'], evidence_message_ids=[message.message_id], source_text=message.text or '')
                    if item['category'] in {'interest','preference','communication_style','project','skill','goal'}:
                        self.semantic_memory.approve(cid, reviewer_id=message.sender_id)
                except ValueError:
                    logger.info('SEMANTIC_MEMORY_CANDIDATE_REJECTED trace_id=%s chat_id=%s sender_id=%s', message.trace_id or '-', message.chat_id, message.sender_id)
        if message.sender_is_bot:
            logger.info('MEMORY_USER_LAYERS_SKIPPED trace_id=%s chat_id=%s sender_id=%s reason=bot', message.trace_id or '-', message.chat_id, message.sender_id)
            return
        logger.info('MEMORY_SHORT_UPDATE_ATTEMPT trace_id=%s chat_id=%s message_id=%s', message.trace_id or '-', message.chat_id, message.message_id)
        topics = detect_topics(message.text)
        mood = detect_mood(message.text)
        addressed = int(message.reply_to_zero or message.mention_zero)
        topic = topics[0] if topics else ''
        await self.store.merge_short_term_context(
            message.chat_id,
            sender_id=message.sender_id,
            message_id=message.message_id,
            topic=topic,
            addressed_to_zero=addressed,
            mood=mood,
            sensitivity='sensitive' if 'token' in (message.text or '').lower() else 'normal',
            question_unanswered=int(('?' in (message.text or '') or '؟' in (message.text or '')) and not message.reply_text),
            should_reply=addressed,
            should_react=int(not addressed and mood not in {'conflict', 'sad'}),
            should_wait=int(not addressed or mood in {'conflict', 'sad'}),
            should_ignore=int(not addressed),
            audit_details={'trace_id': message.trace_id or '-', 'message_id': message.message_id, 'active_topic': topic, 'mood': mood, 'addressed_to_zero': addressed, 'chosen_social_action': 'reply' if addressed else 'ignore'},
        )
        logger.info('MEMORY_SHORT_UPDATED trace_id=%s chat_id=%s message_id=%s active_topic=%s mood=%s addressed_to_zero=%s chosen_social_action=%s', message.trace_id or '-', message.chat_id, message.message_id, topic, mood, addressed, 'reply' if addressed else 'ignore')
        if message.message_id and message.message_id % 10 == 0:
            try:
                await self.store.update_daily_summary(message.chat_id)
            except Exception as exc:
                logger.warning('MEMORY_DAILY_SUMMARY_FAILED trace_id=%s chat_id=%s message_id=%s error=%s', message.trace_id or '-', message.chat_id, message.message_id, type(exc).__name__)
        extracted = maybe_extract_memory(message)
        correction = extract_nickname_correction(message.text)
        if correction:
            previous = await self.store.find_active_long_memory(message.chat_id, 'nickname', message.sender_id)
            if previous:
                await self.store.correct_long_memory(previous['memory_id'], correction, actor_user_id=message.sender_id, trace_id=message.trace_id or '-', reason='user_correction')
                logger.info('MEMORY_CORRECTED trace_id=%s chat_id=%s message_id=%s memory_id=%s', message.trace_id or '-', message.chat_id, message.message_id, previous['memory_id'])
        long_candidate = extract_explicit_long_candidate(message.text)
        if is_sensitive_memory_text(message.text) and ('یادت' in (message.text or '') or 'remember' in (message.text or '').lower()):
            logger.info('MEMORY_LONG_SKIPPED trace_id=%s chat_id=%s message_id=%s reason=sensitive', message.trace_id or '-', message.chat_id, message.message_id)
            await self.store.memory_audit_event('MEMORY_LONG_SKIPPED', 'long', message.chat_id, trace_id=message.trace_id or '-', details={'message_id': message.message_id, 'reason': 'sensitive'})
        if long_candidate:
            category, content = long_candidate
            try:
                memory_id = await self.store.add_long_memory(message.chat_id, category, content, created_by=message.sender_id, subject_user_id=message.sender_id, source_message_ids=[message.message_id], confidence=0.95)
                logger.info('MEMORY_LONG_CREATED trace_id=%s chat_id=%s message_id=%s memory_id=%s', message.trace_id or '-', message.chat_id, message.message_id, memory_id)
            except ValueError:
                logger.info('MEMORY_LONG_SKIPPED trace_id=%s chat_id=%s message_id=%s reason=sensitive', message.trace_id or '-', message.chat_id, message.message_id)
        medium_candidate = extract_medium_candidate(message.text)
        if medium_candidate:
            topic, summary, ttl = medium_candidate
            event_id = await self.store.add_medium_memory(message.chat_id, topic, summary, participants=[message.sender_id], source_message_ids=[message.message_id], importance=0.75 if topic in {'project','deadline'} else 0.65, confidence=0.82, ttl_seconds=ttl)
            logger.info('MEMORY_MEDIUM_CREATED trace_id=%s chat_id=%s message_id=%s event_id=%s topic=%s', message.trace_id or '-', message.chat_id, message.message_id, event_id, topic)
        await self.store.upsert_profile(
            message.chat_id, message.sender_id, message.sender_label,
            username=message.sender_username,
            display_name=message.sender_display_name,
            **extracted,
        )
        if not any(extracted.values()):
            return
        for topic in extracted['topics']:
            await self.store.add_medium_memory(message.chat_id, topic, f"موضوعی که کاربر صریحاً خواست به خاطر سپرده شود: {topic}", participants=[message.sender_id], source_message_ids=[message.message_id], importance=0.55, confidence=0.75)
        for project in extracted['projects']:
            await self.store.add_medium_memory(message.chat_id, 'project', project, participants=[message.sender_id], source_message_ids=[message.message_id], importance=0.7, confidence=0.8)

    async def remember_reply(self, message: IncomingMessage, reply_text: str, *, telegram_message_id: int | None = None) -> None:
        assistant_id = self.zero_user_id if self.zero_user_id is not None else 0
        await self.store.append_recent(
            message.chat_id, assistant_id, 'Zero', 'assistant', reply_text,
            platform=message.platform, account_scope=message.account_scope,
            telegram_message_id=telegram_message_id, reply_to_message_id=message.message_id or None,
            thread_id=message.thread_id, trace_id=message.trace_id,
        )
        await self.memory_v3.record_message(
            replace(
                message,
                sender_id=assistant_id,
                sender_label='Zero',
                text=reply_text,
                message_id=int(telegram_message_id or 0),
                reply_to_message_id=message.message_id or None,
            ),
            role='assistant',
        )
        if message.sender_id != self.config.owner_user_id:
            await self.store.add_rate_event(message.sender_id, 'reply', message.chat_id)
        if message.sender_is_bot:
            await self.store.add_rate_event(message.sender_id, 'bot_reply', message.chat_id)
            await self.store.add_rate_event(message.sender_id, 'nova_msg', message.chat_id)
            await self.store.add_rate_event(message.sender_id, 'bot_msg', message.chat_id)
        day = datetime.now().strftime('%Y-%m-%d')
        await self.store.incr_daily_stats(day, reply_count=1, api_calls=1, output_chars=len(reply_text), input_chars=len(message.text))
        # LLM replies are not trusted memory commands and are never persisted as memory.
