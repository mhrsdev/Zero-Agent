from __future__ import annotations

from typing import Any

import asyncio
import json
import logging
import os
import random
import re
import time
from dataclasses import replace
from datetime import datetime

from .automation import automation_disabled
from .brain_generate import (
    BrainGenerateMixin,
    build_live_market_disclosure,
    deterministic_market_tool_calls,
    is_telegram_search_request,
    needs_long_reply,
    parse_search_command,
    reply_char_limit,
    reply_token_limit,
    sanitize_internal_search_status,
)
from .brain_media import (
    BrainMediaMixin,
    detect_mood_from_user,
    gif_negative_feedback,
    is_media_followup_text,
    normalize_sticker_text,
    sanitize_mood,
    sanitize_outgoing_text,
    sticker_context_allowed,
    sticker_negative_feedback,
    sticker_retry_feedback,
    user_requests_gif,
    user_requests_sticker,
)
from .brain_policy import BrainPolicyMixin
from .brain_reply import BrainReplyMixin
from .brain_memory import BrainMemoryMixin
from .spend import add_spend, budget_limit, estimate_usd, spent_today
from .turn_options import parse_owner_memory_command, think_prefix
from .config import ZeroConfig
from .debug_trace import emit_reply_trace
from .group_policy import GroupPolicy, load_group_policy
from .memory_planner import plan_memory, render_experience, render_procedures, render_semantic, render_world
from .memory_context import compose_memory_context
from .group_context import GroupContext
from .proactive_followups import ProactiveFollowups
from .document_bundles import DocumentBundles
from .memory_v3 import MemoryV3Item, MemoryV3Service
from .core.memory_service import MemoryService
from .tenancy import GroupStateError, Scope, TenancyError, TenancyRegistry
from .memory_v3.retrieval_planner import metadata as planner_metadata, parse as parse_retrieval_plan, prompt as planner_prompt, window as planner_window
from .market_prices import BinancePriceClient, NavasanPriceClient, NobitexPriceClient, PriceAPIError, TGJUWebPriceClient
from .semantic_memory import SemanticUserMemory
from .experience_memory import ExperienceMemory
from .procedural_memory import ProceduralMemory
from .knowledge import KnowledgePolicy
from .world_model import WorldModel
from .memory import (
    build_group_summary,
    detect_topics,
    is_untrusted_memory_control_text,
    is_sensitive_memory_text,
    maybe_extract_memory,
    detect_mood,
    extract_medium_candidate,
    extract_explicit_long_candidate,
    extract_nickname_correction,
)
from .moderation import is_spammy
from .models import Decision, IncomingMessage
from .prompts import build_reply_prompt, build_starter_prompt, build_summary_prompt, build_summary_merge_prompt
from .router import IndependentRouter
from .security import Intent, classify_intent, looks_abusive, fixed_security_reply
from .storage import ZeroStore
from .limit_challenge import LimitChallengeService
from .social_awareness import SocialAwareness
from .social_plus import SocialAwarenessPlus
from .triggers import decide_reply, is_triggered, strip_trigger
from .web import HybridWeb, build_search_query, is_current_price_or_market_query, is_deep_search_request, needs_web_search
from .web_search.truth import build_news_fallback, build_numeric_fallback, numeric_fallback_eligible, sanitize_source_display, source_link
from .vision import VisionProcessor, is_image_media, is_gif_media, is_video_media, is_sticker_media
from .stickers.observer import StickerObserver
from .stickers.decision import CANONICAL_MOODS, StickerIntent, StickerSendOutcome, normalize_mood
from .gifs.decision import GifSendOutcome
from .gifs.service import GifService

logger = logging.getLogger('zero.brain')

# Wall-clock ceiling for one web-search pipeline run. Only the deep path was
# bounded before; a normal search could run for the sum of every provider
# budget (grounding 45s plus serial local providers at 12s each) while holding
# the listener's message lock, so one slow query froze replies in every group.
_SEARCH_TIMEOUT_SECONDS = 45.0
_DEEP_SEARCH_TIMEOUT_SECONDS = 45.0

__all__ = [
    'ZeroBrain',
    'needs_long_reply',
    'reply_char_limit',
    'reply_token_limit',
    'deterministic_market_tool_calls',
    'sanitize_internal_search_status',
    'parse_search_command',
    'is_telegram_search_request',
    'build_live_market_disclosure',
    'sanitize_mood',
    'sanitize_outgoing_text',
    'normalize_sticker_text',
    'user_requests_sticker',
    'user_requests_gif',
    'gif_negative_feedback',
    'sticker_negative_feedback',
    'sticker_retry_feedback',
    'sticker_context_allowed',
    'detect_mood_from_user',
    'is_media_followup_text',
]


class ZeroBrain(BrainPolicyMixin, BrainMediaMixin, BrainGenerateMixin, BrainReplyMixin, BrainMemoryMixin):
    def __init__(self, config: ZeroConfig, store: ZeroStore, router: IndependentRouter, client=None, knowledge=None, sticker_rng=None, gif_rng=None, *, tenancy: TenancyRegistry | None = None, installation_id: str | None = None):
        self.config = config
        self.store = store
        self.router = router
        self.knowledge = knowledge
        self.market_prices = BinancePriceClient()
        self.navasan_prices = NavasanPriceClient()
        self.tgju_prices = TGJUWebPriceClient()
        self.nobitex_prices = NobitexPriceClient()
        self.semantic_memory = SemanticUserMemory(store.db_path)
        self.experience_memory = ExperienceMemory(store.db_path)
        self.procedural_memory = ProceduralMemory(store.db_path)
        self.world_model = WorldModel(store.db_path)
        v3_path = os.getenv('ZERO_MEMORY_V3_DB') or os.path.join(os.path.dirname(str(store.db_path)), 'zero-memory-v3.db')
        self.memory_v3 = MemoryV3Service(v3_path)
        self.memory = MemoryService(self.memory_v3)
        self.tenancy = tenancy
        self.installation_id = installation_id or os.getenv("ZERO_INSTALLATION_ID", "local")

        self.group_context = GroupContext(store, router)
        self.proactive_followups = ProactiveFollowups(store, router, client=client)
        self.document_bundles = DocumentBundles(store)
        # V1 is migration/archive-only. Normal runtime retrieval and writes are V3-only.
        self.v1_memory_runtime_enabled = False
        self.web = HybridWeb(config, store)
        # Direct Gemini Vision must never consume an aggregate cross-provider key pool.
        vision_keys = list(getattr(router, "gemini_keys", []) or [])
        self.vision = VisionProcessor(config, vision_keys, store)
        self.sticker_observer = StickerObserver(config, store, client=client, vision=self.vision)
        self.limit_challenges = LimitChallengeService(store)
        self.social_awareness = SocialAwareness(store)
        self.social_plus = SocialAwarenessPlus(store)
        self._client = client
        self.zero_user_id: int | None = None
        self._sticker_send_lock = asyncio.Lock()
        self._sticker_rng = sticker_rng or random.Random()
        self._gif_send_lock = asyncio.Lock()
        self._gif_rng = gif_rng or random.Random()
        # Handles for sends started concurrently from a synchronous return path.
        # A previous `_turn_sticker_marker` set claimed to de-duplicate those
        # sends but was never read or written; `_sticker_send_lock` is the real
        # serialisation. What was actually missing is a reference: without one
        # the event loop keeps only a weak reference to a task that is not
        # currently executing, so the send can be collected mid-flight, and a
        # failure is never reported.
        self._background: set[asyncio.Task] = set()

    def _spawn(self, coro, name: str) -> asyncio.Task:
        """Start ``coro`` concurrently, keeping a handle and logging failures."""
        task = asyncio.ensure_future(coro)
        task.set_name(name)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        task.add_done_callback(self._log_background_failure)
        return task

    @staticmethod
    def _log_background_failure(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning('BACKGROUND_SEND_FAILED task=%s exception_type=%s', task.get_name(), type(exc).__name__)

    def _scope_for(self, message: IncomingMessage) -> Scope:
        if self.tenancy is None:
            raise TenancyError("tenancy registry is not bound")
        installation = message.context.installation_id if message.context is not None else self.installation_id
        return self.tenancy.resolve_scope(
            installation,
            platform_chat_id=int(message.chat_id),
            user_id=int(message.sender_id or 0) or None,
            thread_id=message.thread_id,
            request_id=message.trace_id or "",
            trace_id=message.trace_id or "-",
        )

    def _memory_for(self, message: IncomingMessage) -> MemoryService:
        """Bind MemoryService to this request's tenant. Unbound when tenancy is unset."""
        if self.tenancy is None:
            return self.memory
        scope = self._scope_for(message)
        self.tenancy.require_serving(scope)
        return self.memory.bind(scope, self.tenancy)

    def _group_policy_for(self, message: IncomingMessage) -> GroupPolicy:
        if self.tenancy is None:
            return GroupPolicy()
        try:
            return load_group_policy(self.tenancy, self._scope_for(message))
        except TenancyError:
            return GroupPolicy()

    def _apply_prompt_budget(self, memory_context: str) -> str:
        budget = int(getattr(self.config.memory, "prompt_token_budget", 0) or 0)
        if budget <= 0 or not memory_context:
            return memory_context
        max_chars = budget * 4
        if len(memory_context) <= max_chars:
            return memory_context
        return memory_context[:max_chars]

    def _memory_target(self, message: IncomingMessage) -> tuple[int, str]:
        text=(message.text or '').casefold()
        if message.resolved_target_user_id and re.search(r'@\w+.*(?:کیه|کی هست|میشناسی)|(?:کیه|کی هست).*@\w+',text): return message.resolved_target_user_id,'mentioned_user'
        if message.reply_sender_id and not message.reply_sender_is_bot and re.search(r'این کیه|این شخص|کیه',text): return message.reply_sender_id,'reply_target'
        return message.sender_id,'speaker'

    async def _planned_memory_context(self, message: IncomingMessage, trace_id: str) -> tuple[str, dict]:
        """Planner may request evidence; executor enforces chat scope and bounded reads."""
        if os.getenv('ZERO_MEMORY_V3_PLANNER_ENABLED', 'false').lower() != 'true': return '', {'used':False}
        meta=planner_metadata(message); started=time.monotonic(); result=await self.router.complete(planner_prompt(message,meta),max_output_tokens=180); latency_ms=int((time.monotonic()-started)*1000)
        plan=parse_retrieval_plan(result.text,set(meta['references']))
        if not plan:
            return 'Historical retrieval was unavailable for this turn; do not invent historical claims.', {'used':True,'success':False,'fallback':'invalid_plan','latency_ms':latency_ms}
        if not plan.needs_memory: return '', {'used':True,'success':True,'operation':plan.operation,'actors':0,'subjects':0,'latency_ms':latency_ms}
        async def resolve(ref):
            if ref=='self': return message.sender_id
            if ref=='reply_target': return message.reply_sender_id
            if ref.startswith('mention:'):
                try:return message.resolved_mention_user_ids[int(ref.split(':',1)[1])]
                except (ValueError,IndexError): return None
            ids=await self.store.find_users_by_identity(message.chat_id,ref)
            return ids[0] if len(ids)==1 else None
        actors=[x for x in [await resolve(r) for r in plan.actors] if x]
        subjects=[x for x in [await resolve(r) for r in plan.subjects] if x]
        base={'used':True,'success':True,'operation':plan.operation,'actors':len(actors),'subjects':len(subjects),'time':plan.time_kind,'evidence_mode':plan.evidence_mode,'unresolved':len(plan.actors)+len(plan.subjects)-len(actors)-len(subjects),'latency_ms':latency_ms}
        if plan.operation in {'find_statements','find_events','find_decisions'}:
            if not actors: return 'No historical evidence was retrieved because the requested actor was not resolved; ask a short clarification instead of guessing.', {**base,'success':False,'fallback':'unresolved_actor'}
            bounds=planner_window(plan.time_kind)
            if not bounds: return 'No historical evidence was retrieved because the requested time range was not precise enough.', {**base,'success':False,'fallback':'no_time_window'}
            rows=await self.store.get_recent_since(message.chat_id,since_ts=int(bounds[0]),limit=80)
            rows=[r for r in rows if r.get('role')=='user' and int(r.get('sender_id') or 0) in actors]
            lines=[]
            for r in rows[:8]:
                text=self.memory_v3.sanitize(str(r.get('text') or ''))
                if text: lines.append(f"- evidence_type=historical_message occurred_at={int(r.get('created_at') or 0)} actor=resolved scope=current_chat content={text[:360]}")
            if not lines: return 'No matching historical evidence was found in the current chat; do not invent a summary.', {**base,'candidate_count':0,'selected_count':0}
            return 'Historical evidence — reference only:\n'+'\n'.join(lines), {**base,'candidate_count':len(rows),'selected_count':len(lines)}
        return '', {**base,'candidate_count':0,'selected_count':0}

    async def maybe_reply(self, message: IncomingMessage) -> tuple[Decision, str]:
        started = time.monotonic()
        early_decision, early_text = await self._pre_check(message)
        if early_decision is not None and not early_decision.continue_generation:
            emit_reply_trace(self.config, {
                'trace_id': message.trace_id or '-',
                'chat_id': message.chat_id,
                'thread_id': message.thread_id,
                'sender_id': message.sender_id,
                'reason': early_decision.reason,
                'should_reply': early_decision.should_reply,
                'latency_ms': int((time.monotonic() - started) * 1000),
            })
            return early_decision, early_text
        owner_cmd = parse_owner_memory_command(message.text or '')
        if owner_cmd and int(message.sender_id or 0) == int(self.config.owner_user_id or 0):
            kind, target = owner_cmd
            if kind == 'off':
                if self.tenancy is not None:
                    try:
                        self.tenancy.set_setting(self._scope_for(message), 'memory_enabled', False, actor_id=int(message.sender_id))
                    except Exception:
                        pass
                return Decision(True, 'memory_off'), 'حافظه برای این گروه خاموش شد.'
            target_id = message.resolved_target_user_id or message.reply_sender_id
            if target_id is None:
                ident = (target or '').strip().lstrip('@')
                if ident.isdigit():
                    target_id = int(ident)
                elif ident:
                    found = await self.store.find_users_by_identity(message.chat_id, ident)
                    target_id = found[0] if len(found) == 1 else None
            if not target_id:
                return Decision(True, 'memory_forget'), 'بگو کدام کاربر را فراموش کنم (ریپلای یا @یوزرنیم).'
            try:
                removed = await self._memory_for(message).forget_user(message.chat_id, int(target_id))
            except Exception:
                removed = await self.memory_v3.forget_user(message.chat_id, int(target_id))
            return Decision(True, 'memory_forget'), f'{int(removed)} مورد حافظه برای آن کاربر حذف شد.'
        search_command = parse_search_command(message.text)
        if search_command and not search_command[1]:
            command = '/deepsearch' if search_command[0] == 'deep' else '/search'
            return Decision(True, 'search_usage'), f'بعد از {command} موضوع جستجو رو بنویس.'
        intent = classify_intent(message.text, message.reply_text)
        policy = self._group_policy_for(message)
        limit = budget_limit(self.config, policy)
        if limit > 0:
            spent = await spent_today(self.store, message.chat_id)
            if spent >= limit:
                return Decision(True, 'budget_exhausted'), 'بودجه امروز تمام شد.'
        try:
            decision, answer = await self._handle_no_media(
                message,
                early_decision or Decision(True, 'triggered', continue_generation=True),
                intent,
            )
        except (TenancyError, GroupStateError):
            return Decision(False, 'tenancy_unresolved'), ''
        emit_reply_trace(self.config, {
            'trace_id': message.trace_id or '-',
            'chat_id': message.chat_id,
            'thread_id': message.thread_id,
            'sender_id': message.sender_id,
            'reason': decision.reason,
            'should_reply': decision.should_reply,
            'provider': getattr(self.router, 'last_route', {}).get('provider') if hasattr(self.router, 'last_route') else '',
            'latency_ms': int((time.monotonic() - started) * 1000),
            'answer_chars': len(answer or ''),
        })
        prefix = think_prefix(self.config)
        if prefix and answer:
            answer = prefix + answer
        return decision, answer

    async def maybe_reply_with_media(self, message: IncomingMessage, event) -> tuple[Decision, str]:
        if not self.config.vision.enabled:
            return await self.maybe_reply(message)

        media_event = event
        has_image = is_image_media(media_event)
        has_gif = is_gif_media(media_event)
        has_video = is_video_media(media_event)
        has_sticker = is_sticker_media(media_event)

        if not (has_image or has_gif or has_video or has_sticker) and event.is_reply:
            replied = await event.get_reply_message()
            if replied and (is_image_media(replied) or is_gif_media(replied) or is_video_media(replied) or is_sticker_media(replied)):
                media_event = replied
                has_image = is_image_media(media_event)
                has_gif = is_gif_media(media_event)
                has_video = is_video_media(media_event)
                has_sticker = is_sticker_media(media_event)

        if not (has_image or has_gif or has_video or has_sticker):
            return await self.maybe_reply(message)

        early_decision, early_text = await self._pre_check(message)
        if early_decision is not None and not early_decision.continue_generation:
            return early_decision, early_text
        clean_user_text = strip_trigger(message.text, self.config.listener.account_username)
        vision_reason = "unavailable"
        if hasattr(self.vision, "process_outcome"):
            vision_outcome = await self.vision.process_outcome(
                media_event, question=clean_user_text
            )
            vision_result = vision_outcome.text if vision_outcome.ok else None
            vision_reason = vision_outcome.reason
            logger.info(
                "VISION_DECISION trace_id=%s reason=%s media_mime=%s frames=%s",
                message.trace_id or "-", vision_reason,
                vision_outcome.media_type or "unknown", vision_outcome.frame_count,
            )
        else:
            vision_result = await self.vision.process(media_event, question=clean_user_text)
        if vision_result:
            recent = await self.store.get_recent(message.chat_id, limit=100)
            has_link_context = bool(
                re.search(r'https?://\S+', clean_user_text or '')
                or re.search(r'https?://\S+', message.reply_text or '')
                or any(re.search(r'https?://\S+', str(row.get('text', '') or '')) for row in recent[-12:])
            )
            asks_about_link = bool(re.search(r'(?:قیمت|چنده|مشخصات|اطلاعات|این|اون|همین|چیست|چیه|؟|\?)', clean_user_text or ''))
            if has_link_context and asks_about_link:
                enriched = replace(message, text=f'{clean_user_text}\n\n[Vision Analysis: {vision_result}]')
                return await self._handle_no_media(enriched, Decision(True, 'vision_web'), classify_intent(enriched.text, enriched.reply_text))
            memory_context, memory_meta = await self._memory_for(message).context(message)
            memory_context = self._apply_prompt_budget(memory_context)
            logger.info('MEMORY_CONTEXT_COMPOSED trace_id=%s path=vision chars=%s selected=%s', message.trace_id or '-', len(memory_context), memory_meta.get('selected', 0))
            mode = await self._mode()
            prompt = build_reply_prompt(
                self.config, mode=mode, sender_label=message.sender_label,
                user_text=clean_user_text + f"\n\n[Vision Analysis: {vision_result}]",
                reply_text=message.reply_text, recent=[], group_summary='', web_context='', memory_context=memory_context,
                chat_id=message.chat_id, sender_id=message.sender_id, message_id=message.message_id, thread_id=message.thread_id, reply_to_message_id=message.reply_to_message_id,
                reply_sender_id=message.reply_sender_id, reply_sender_label=message.reply_sender_label, reply_sender_is_bot=message.reply_sender_is_bot)
            # VISION PATH — must also sanitize!
            reply_text = await self._generate_and_sanitize(message, prompt, chat_id=message.chat_id)
            return Decision(True, 'vision'), reply_text

        decision = Decision(True, f"vision_{vision_reason}")
        if has_gif or has_video:
            return decision, 'این GIF/ویدئو رو نتونستم درست بررسی کنم؛ محتوای حدسی نمی‌گم. دوباره بفرست یا یه فریم واضح ازش بفرست.'
        if has_sticker:
            return decision, 'این استیکر رو نتونستم درست بخونم؛ اگه منظورت تحلیلشه، دوباره بفرست.'
        return decision, 'این تصویر رو نتونستم درست بررسی کنم؛ پاسخ حدسی نمی‌دم. دوباره بفرست.'

    async def build_monthly_group_memory(self, chat_id: int) -> dict[str, Any]:
        since = int(time.time()) - 30 * 86400
        summary = await self.build_daily_summary(chat_id, since_ts=since)
        period = await self.store.build_period_summary(chat_id, days=30, label='monthly_group')
        if not summary or 'پاسخ‌گویی در دسترس نیست' in summary or 'PROVIDERS_FAILED' in summary:
            return {'status': 'summary_unavailable', **period}
        item_id = await self.memory.put(MemoryV3Item.group(
            chat_id=chat_id, content=summary, kind='group_monthly_summary',
            importance=.85, confidence=.95,
            source_message_ids=tuple(period.get('source_message_ids', [])),
            metadata={'period': '30d', 'source_count': period.get('source_count', 0)},
        ))
        return {'memory_id': item_id, 'summary': summary, **period}

    async def build_daily_summary(self, chat_id: int, *, since_ts: int | None = None) -> str:
        recent = (
            await self.store.get_recent_since(chat_id, since_ts=since_ts, limit=5000)
            if since_ts is not None else await self.store.get_recent(chat_id, limit=120)
        )
        if since_ts is not None and len(recent) >= 5000:
            logger.warning('GROUP_SUMMARY_WINDOW_TRUNCATED chat_id=%s since_ts=%s hard_limit=5000', chat_id, since_ts)
        layered = await self.store.retrieve_layered_memory(chat_id, 'daily summary', short_limit=1, medium_limit=20, long_limit=20)
        memory_items = layered['medium'] + layered['long']
        chunks: list[list[dict]] = []
        current: list[dict] = []
        current_chars = 0
        for row in recent:
            row_chars = len(str(row.get('text', ''))) + len(str(row.get('sender_label', ''))) + 80
            if current and current_chars + row_chars > 12_000:
                chunks.append(current); current, current_chars = [], 0
            current.append(row); current_chars += row_chars
        if current:
            chunks.append(current)
        logger.info('GROUP_SUMMARY_INPUT chat_id=%s since_ts=%s message_count=%s chunk_count=%s', chat_id, since_ts or 0, len(recent), len(chunks))
        partials: list[str] = []
        for index, chunk in enumerate(chunks):
            prompt = build_summary_prompt(self.config, recent=chunk, memory_items=memory_items if len(chunks) == 1 else [])
            result = await self.router.complete(prompt, max_output_tokens=900)
            failed = result.provider in {'fallback', 'structured_failure'} or bool((result.metadata or {}).get('error'))
            if result.text and not failed:
                partial, _ = sanitize_outgoing_text(sanitize_internal_search_status(result.text))
                if partial:
                    partials.append(partial)
            logger.info('GROUP_SUMMARY_CHUNK chat_id=%s chunk=%s/%s provider=%s produced=%s failed=%s', chat_id, index + 1, len(chunks), result.provider, bool(result.text), failed)
        if not partials:
            return ''
        if len(partials) == 1:
            text = partials[0]
        else:
            merged = await self.router.complete(build_summary_merge_prompt(self.config, partials=partials, memory_items=memory_items), max_output_tokens=900)
            merge_failed = merged.provider in {'fallback', 'structured_failure'} or bool((merged.metadata or {}).get('error'))
            text = '' if merge_failed else (merged.text or '')
        # Safety: summaries shouldn't have markers, but sanitize anyway
        cleaned, _ = sanitize_outgoing_text(sanitize_internal_search_status(text))
        return cleaned

    async def maybe_starter(self, chat_id: int) -> str:
        recent = await self.store.get_recent(chat_id, limit=60)
        layered = await self.store.retrieve_layered_memory(chat_id, 'starter', short_limit=1, medium_limit=4, long_limit=6)
        group_summary = build_group_summary(recent, [])
        mode = await self._mode()
        prompt = build_starter_prompt(self.config, mode=mode, recent=recent, group_summary=group_summary)
        result = await self.router.complete(prompt, max_output_tokens=300)
        text = result.text or ''
        # Starters could have STICKER:xxx if model hallucinates — sanitize defensively
        cleaned, mood = sanitize_outgoing_text(sanitize_internal_search_status(text))
        if mood and self.config.stickers.enabled:
            self._spawn(self._send_sticker_async(chat_id, mood), 'starter_sticker')
        return cleaned
