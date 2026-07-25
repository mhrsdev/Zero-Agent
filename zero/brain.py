from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from dataclasses import replace
from datetime import datetime

from .config import ZeroConfig
from .memory_planner import plan_memory, render_experience, render_procedures, render_semantic, render_world
from .memory_context import compose_memory_context
from .group_context import GroupContext
from .proactive_followups import ProactiveFollowups
from .document_bundles import DocumentBundles
from .memory_v3 import MemoryV3Service
from .core.memory_service import MemoryService
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

logger = logging.getLogger('zero.brain')

# Robust regex: catches STICKER:funny, [STICKER:funny], STICKER: funny.
# Note: NO \s* before STICKER — that would eat the preceding space.
_STICKER_RE = re.compile(r'\[?STICKER\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\]?', re.IGNORECASE)
_LONG_REPLY_MARKERS = (
    'برنامه', 'برنامه‌ریزی', 'برنامه ریزی', 'درس', 'آموزش', 'توضیح', 'مراحل',
    'مرحله', 'لیست', 'فهرست', 'مقایسه', 'تحلیل', 'راهنما', 'کد', 'پروژه',
    'جزئیات', 'چطور', 'چگونه', 'بررسی', 'پیشنهاد', 'پلن', 'schedule', 'plan',
    'explain', 'how to', 'واژه', 'لغت', 'کلمه', 'معنی', 'مترادف', 'فرهنگ',
    'تعریف', 'دانشنامه', 'ویکی', 'ویکی‌پدیا', 'wikipedia',
    'سرچ عمیق', 'جستجوی عمیق', 'تحقیق عمیق', 'deep search', 'deepsearch', 'گزارش کامل',
)


def needs_long_reply(text: str) -> bool:
    normalized = (text or '').lower().replace('‌', ' ')
    return len(normalized) >= 180 or any(marker in normalized for marker in _LONG_REPLY_MARKERS)


def reply_char_limit(config: ZeroConfig, text: str) -> int:
    return 3900 if needs_long_reply(text) else config.policy.max_reply_chars


def reply_token_limit(text: str) -> int:
    return 2200 if needs_long_reply(text) else 700


def deterministic_market_tool_calls(text: str) -> list[dict]:
    low = (text or '').casefold().replace('ي', 'ی').replace('ك', 'ک')
    calls = []
    if re.search(r'دلار|usd|dollar', low):
        calls.append({'name': 'read_iran_market_price', 'arguments': {'asset': 'usd'}})
    if re.search(r'طلا|عیار|gold', low):
        calls.append({'name': 'read_iran_market_price', 'arguments': {'asset': '18ayar'}})
    if re.search(r'سکه(?:\s+امامی)?|coin', low):
        calls.append({'name': 'read_iran_market_price', 'arguments': {'asset': 'sekkeh'}})
    for markers, symbol in (
        (r'بیت\s*کوین|بیتکوین|bitcoin|btc', 'BTC'),
        (r'اتریوم|ethereum|eth', 'ETH'),
        (r'سولانا|solana|sol', 'SOL'),
    ):
        if re.search(markers, low):
            calls.append({'name': 'read_market_price', 'arguments': {'symbol': symbol, 'quote': 'USDT'}})
    return calls


_VALID_MOODS = frozenset(['funny', 'sad', 'love', 'angry', 'greeting', 'react',
                          'cool', 'shock', 'surprise', 'thinking', 'approve', 'fire',
                          'celebrate', 'pray', 'smirk', 'dead'])
_STICKER_WORDS_FA = ('استیکر', 'sticker', 'sticer')
_STICKER_WORDS_EN = ('sticker',)
_INTERNAL_SEARCH_STATUS_RE = re.compile(r'(?im)^\s*(?:WEB_STATUS|TG_STATUS|GOOGLE_GROUNDING_STATUS)\s*:\s*[^\r\n]*\r?\n?')
_UNSAFE_OUTPUT_RE = re.compile(
    r'(?:لاگ(?:‌|\s*)ها?\s*(?:رو|را)?\s*پاک|پاک\s*کردن\s*(?:لاگ|ردپا|مدرک)|'
    r'مدرک(?:ی|ها)?\s*(?:دستش|دستشان)\s*نیفت|داشتم\s+لاگ(?:‌|\s*)ها?\s*رو\s*چک)',
    re.I,
)


def sanitize_internal_search_status(text: str) -> str:
    return _INTERNAL_SEARCH_STATUS_RE.sub('', text or '').strip()


def parse_search_command(text: str) -> tuple[str, str] | None:
    """Return the query only when /search is the first exact token."""
    deep = re.match(r'^/deep(?:_|-)?search(?:\s+(.*))?$', text or '', re.IGNORECASE | re.DOTALL)
    if deep:
        return 'deep', (deep.group(1) or '').strip()
    match = re.match(r'^/search(?:\s+(.*))?$', text or '', re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return 'web', (match.group(1) or '').strip()


def is_telegram_search_request(text: str) -> bool:
    """Route explicit Telegram/channel searches to the TG-search client."""
    command = parse_search_command(text)
    if command and command[0] in {'telegram', 'combined'}:
        return True
    low = (text or '').lower()
    asks_for_search = any(x in low for x in ('سرچ', 'جستجو', 'بگرد', 'search', 'find', 'look up'))
    targets_telegram = 'تلگرام' in low or 'کانال' in low or 'telegram' in low or 'channel' in low
    return asks_for_search and targets_telegram


def build_live_market_disclosure(title: str, url: str, searched_at_utc: str) -> str:
    """Deterministic provenance footer for volatile market answers."""
    return f'\n\nمنبع: {source_link(url)}\nزمان جستجو: {searched_at_utc}\nقیمت‌ها نوسانی‌اند و ممکنه تا همین الان تغییر کرده باشن.'


def sanitize_mood(mood: str) -> str | None:
    m = (mood or '').lower().strip()
    if m == 'reaction':
        return 'react'
    if m in _VALID_MOODS:
        return m
    return 'react'  # safe fallback


def sanitize_outgoing_text(text: str) -> tuple[str, str | None]:
    """Remove any internal STICKER:xxx marker from LLM output.
    
    Returns (cleaned_text, mood_or_None).
    Always returns non-raw text — marker is NEVER leaked.
    """
    if not text:
        return text, None
    if _UNSAFE_OUTPUT_RE.search(text):
        # Keep the playful persona, but never emit advice to erase logs,
        # hide evidence, or claim unverified log access.
        if re.search(r'داشتم\s+لاگ(?:‌|\s*)ها?\s*رو\s*چک', text, re.I):
            return 'من به لاگ‌های شخصی یا سیستم دیگران دسترسی ندارم؛ فقط بر اساس اطلاعات همین گفتگو می‌تونم نظر بدم.', None
        return 'من نمی‌تونم برای پاک‌کردن لاگ، مخفی‌کردن ردپا یا دورزدن بررسی‌ها راهنمایی بدم.', None
    match = _STICKER_RE.search(text)
    if not match:
        return text, None
    raw_mood = match.group(1)
    mood = sanitize_mood(raw_mood)
    cleaned = _STICKER_RE.sub('', text)
    cleaned = ' '.join(cleaned.split()).strip()
    return cleaned, mood


def normalize_sticker_text(text: str) -> str:
    value=(text or '').casefold().replace('ي','ی').replace('ى','ی').replace('ك','ک').replace('‌',' ')
    value=re.sub(r'[\u064b-\u065f]', '', value)
    return re.sub(r'\s+', ' ', value).strip()


def user_requests_sticker(user_text: str) -> bool:
    low=normalize_sticker_text(user_text)
    if not low: return False
    explicit=(
        'استیکر بفرست','استیکر بده','استیکر بذار','استیکر بزن','استیکر می‌خوام',
        'استیکر میخوام','استیکرشو بفرست','استیکرو بفرست','یه استیکر','یک استیکر',
        'sticker send','send sticker','sticker please','sticker بفرست','استیکری بفرست','واکنش استیکری','ری‌اکشن استیکری',
    )
    if any(p in low for p in explicit): return True
    if re.search(r'(?:^|\s)استیکر(?:ها)?$',low): return True
    return bool(re.search(r'(^|\s)استیکر(ها)?(\s|$)',low) and any(v in low for v in ('بفرست','بده','بذار','بزن','میخوام','می‌خوام','دیگه')))


def sticker_negative_feedback(user_text: str) -> bool:
    low=normalize_sticker_text(user_text)
    return any(p in low for p in ('بس کن','استیکر نده','اسپم نکن','ساکت','کمتر استیکر بفرست'))


def sticker_retry_feedback(user_text: str) -> bool:
    low=normalize_sticker_text(user_text)
    return any(p in low for p in ('یکی دیگه','یه دونه دیگه','عوضش کن','این نه','این نیست','این برای اینجا نبود','بهترشو','بهترش رو'))


def sticker_context_allowed(user_text: str, *, direct_request: bool = False) -> bool:
    if direct_request:
        return True
    low = (user_text or '').lower()
    forbidden = ('فنی', 'کد', 'python', 'api', 'ارور', 'خطا', 'خبر', 'قیمت', 'سرچ', 'جستجو', 'دیباگ', 'سیاست', 'مرگ', 'دعوا')
    if any(item in low for item in forbidden):
        return False
    allowed = ('😂', '🤣', 'خخ', 'شوخی', 'جوک', 'باحال', 'تبریک', 'مبارک', 'سلام', 'خداحافظ', 'lol', 'haha', 'دمت گرم')
    return any(item in low for item in allowed)


def detect_mood_from_user(user_text: str) -> str:
    low = (user_text or '').lower()
    if any(w in low for w in ('خنده', 'باحال', 'بامزه', 'funny', 'خنده‌دار', 'میخند')):
        return 'funny'
    if any(w in low for w in ('ناراحت', 'غم', 'گریه', 'sad', 'غمگین')):
        return 'sad'
    if any(w in low for w in ('عشق', 'دوست', 'قلب', 'love', '❤️')):
        return 'love'
    if any(w in low for w in ('عصبانی', 'angry', 'قهر', 'اعصاب')):
        return 'angry'
    if any(w in low for w in ('سلام', 'خداحافظ', 'greeting', 'hello', 'bye', 'hi')):
        return 'greeting'
    if any(w in low for w in ('تعجب', 'شگفت', 'wow', 'عجب')):
        return 'shock'
    return 'react'


def _empty_when_marker_was_present(text_before: str, text_after: str) -> bool:
    return (text_before != text_after) and len(text_after.strip()) < 3


def is_media_followup_text(text: str) -> bool:
    low = (text or '').strip().lower()
    patterns = (
        'چی فرستادم', 'چه فرستادم', 'این چی بود', 'اینو دیدی', 'این عکس چی',
        'این gif چی', 'این استیکر چی', 'درباره این بگو', 'اینو توضیح بده',
        'این تصویر چی', 'چی فرستادی', 'چه چیزی فرستادم',
    )
    return any(p in low for p in patterns)


class ZeroBrain:
    def __init__(self, config: ZeroConfig, store: ZeroStore, router: IndependentRouter, client=None, knowledge=None):
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
        # Read-only compatibility alias for legacy test callers; production uses memory_v3.
        self.memory_v2 = self.memory_v3
        self.group_context = GroupContext(store, router)
        self.proactive_followups = ProactiveFollowups(store, router, client=client)
        self.document_bundles = DocumentBundles(store)
        # Existing V1 layers stay available during V3 migration.  The legacy
        # V2 test/compatibility mode deliberately preserves its old cutover semantics.
        self.v1_memory_runtime_enabled = not (self.memory_v3._legacy_compat and self.memory_v3.enabled and not self.memory_v3.shadow)
        self.web = HybridWeb(config, store)
        vision_keys = getattr(router, 'gemini_keys', getattr(router, 'keys', []))
        self.vision = VisionProcessor(config, vision_keys, store)
        self.sticker_observer = StickerObserver(config, store, client=client, vision=self.vision)
        self.limit_challenges = LimitChallengeService(store)
        self.social_awareness = SocialAwareness(store)
        self.social_plus = SocialAwarenessPlus(store)
        self._client = client
        self.zero_user_id: int | None = None
        # One-shot flag per conversation turn to avoid duplicate sticker sends
        # (create_task may race with a slow _send_sticker_async).
        self._turn_sticker_marker = set()  # mood strings queued this turn
        self._sticker_send_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # STICKER SENDING
    # ------------------------------------------------------------------
    async def _send_sticker_async(self, chat_id: int, mood: str, direct_request: bool = False) -> None:
        async with self._sticker_send_lock:
            await self._send_sticker_once(chat_id, mood, direct_request)

    async def _send_sticker_once(self, chat_id: int, mood: str, direct_request: bool = False) -> None:
        try:
            cfg = self.config.stickers
            if not cfg.enabled or (not cfg.auto_enabled and not direct_request):
                logger.info('STICKER_AUTO_SKIPPED chat_id=%s reason=auto_disabled', chat_id)
                return
            policy = await self.store.get_sticker_send_policy(chat_id)
            now = int(time.time())
            feedback_raw = await self.store.get_setting(f'sticker_negative_until:{chat_id}', '0')
            feedback_until = int(feedback_raw or 0)
            if not direct_request and feedback_until > now:
                logger.info('STICKER_AUTO_SKIPPED chat_id=%s reason=negative_feedback', chat_id)
                return
            if policy['sent_last_hour'] >= cfg.limit_per_hour:
                logger.info('STICKER_RATE_LIMITED chat_id=%s reason=hourly_limit count=%s', chat_id, policy['sent_last_hour'])
                return
            cooldown = cfg.cooldown_seconds * (2 if feedback_until > now else 1)
            if not direct_request and policy['last_sent_at'] and now - policy['last_sent_at'] < cooldown:
                logger.info('STICKER_COOLDOWN_ACTIVE chat_id=%s remaining=%s', chat_id, cooldown - (now - policy['last_sent_at']))
                return
            if not direct_request and policy['messages_since_last'] < cfg.min_messages_between:
                logger.info('STICKER_AUTO_SKIPPED chat_id=%s reason=min_messages_between count=%s', chat_id, policy['messages_since_last'])
                return

            from .stickers.library import StickerLibrary
            from .stickers.sender import StickerSender
            from .stickers.models import StickerCandidate

            library = StickerLibrary(self.config, self.store, cfg)
            candidate = await library.get_random_sticker(mood=mood, min_quality=0.45, chat_id=chat_id)
            if candidate is None and mood != 'react':
                candidate = await library.get_random_sticker(mood='react', min_quality=0.40, chat_id=chat_id)
            if candidate is None:
                candidate = await library.get_random_sticker(mood='', min_quality=0.40, chat_id=chat_id)
                if candidate:
                    logger.info('STICKER_SELECTION_FALLBACK chat_id=%s mood=%s fallback=any_safe', chat_id, mood)
            if candidate is None:
                logger.info('STICKER_AUTO_SKIPPED chat_id=%s reason=library_empty', chat_id)
                return
            if not self._client:
                logger.info('STICKER_AUTO_SKIPPED chat_id=%s reason=no_client', chat_id)
                return
            sender = StickerSender(self.config, self.store, client=self._client)
            candidate_obj = StickerCandidate(sticker=candidate, score=candidate.quality_score)
            if candidate.is_video and not candidate.stickerset_id and not candidate.stickerset_short_name:
                ok = await sender.send_media(chat_id, candidate_obj)
            else:
                ok = await sender.send_sticker(chat_id, candidate_obj)
            if ok:
                await self.store.add_rate_event(0, 'sticker_sent', chat_id)
                await self.store.record_sticker_send(candidate.doc_id, chat_id)
                logger.info('STICKER_AUTO_SENT chat_id=%s mood=%s doc_id=%s direct_request=%s', chat_id, mood, candidate.doc_id, direct_request)
            else:
                await self.store.record_sticker_send_failure(candidate.doc_id)
                logger.warning('STICKER_SEND_FAILED mood=%s doc_id=%d', mood, candidate.doc_id)
        except Exception as e:
            logger.warning('STICKER_SEND_EXCEPTION mood=%s err=%s', mood, e)

    async def _has_sticker_for_mood(self, mood: str) -> bool:
        try:
            from .stickers.library import StickerLibrary
            lib = StickerLibrary(self.config, self.store, self.config.stickers)
            return (await lib.get_random_sticker(mood=mood, min_quality=0.30)) is not None
        except Exception:
            return False

    async def _maybe_reply_with_sticker(self, text: str, chat_id: int, user_text: str) -> str:
        """Apply sanitize_outgoing_text; send sticker if applicable.
        
        This is the ONLY function that should touch outgoing reply text.
        Guarantees: no STICKER:xxx marker leaks.
        """
        cleaned, mood = sanitize_outgoing_text(text)

        low_user_text = normalize_sticker_text(user_text)
        direct_request = user_requests_sticker(user_text)
        retry_request = sticker_retry_feedback(user_text)
        if retry_request:
            direct_request = True
            logger.info('STICKER_RETRY_REQUEST_DETECTED chat_id=%s', chat_id)
        reaction_request = (not direct_request) and any(term in low_user_text for term in ('ری‌اکشن','ری اکشن','ریاکشن','واکنش','reaction','react')) and any(term in low_user_text for term in ('بزن','بذار','بده','بفرست','زن','کن','send','add'))
        if reaction_request:
            logger.info('STICKER_AUTO_SKIPPED chat_id=%s reason=reaction_request', chat_id)
            return cleaned

        if sticker_negative_feedback(user_text):
            await self.store.set_setting(f'sticker_negative_until:{chat_id}', str(int(time.time()) + 3600))
            logger.info('STICKER_NEGATIVE_FEEDBACK_APPLIED chat_id=%s', chat_id)
            return cleaned
        # Deterministic fallback: an explicit request or a correction must not
        # depend on the LLM emitting STICKER:xxx.
        if direct_request and mood is None:
            mood = detect_mood_from_user(user_text)
            logger.info('STICKER_DIRECT_REQUEST_DETECTED chat_id=%s mood=%s marker_present=false retry=%s', chat_id, mood, retry_request)
        if mood and self.config.stickers.enabled:
            if not direct_request and (not sticker_context_allowed(user_text) or cleaned.strip()):
                logger.info('STICKER_AUTO_SKIPPED chat_id=%s reason=context_or_text_response', chat_id)
                return cleaned
            asyncio.create_task(self._send_sticker_async(chat_id, mood, direct_request=direct_request))

        if mood and _empty_when_marker_was_present(text, cleaned):
            # After stripping the marker the text would have been near-empty.
            # Decide what to actually output.
            if self.config.stickers.enabled and await self._has_sticker_for_mood(mood):
                return '😉'  # sticker is coming
            # Library empty — tell user explicitly, NO raw marker
            return 'هنوز استیکر مناسبی یاد نگرفتم 😅'

        return cleaned

    # ------------------------------------------------------------------
    # UTILITIES
    # ------------------------------------------------------------------
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

    async def _should_interject(self, message: IncomingMessage) -> bool:
        direct_other = bool(re.search(r'(^|\s)@[A-Za-z0-9_]{3,}', message.text or '')) and not message.mention_zero
        if not self.config.persona.allow_random_interject or message.sender_is_bot or direct_other or (message.reply_text and not message.reply_to_zero):
            return False
        last = float(await self.store.get_setting('last_interject_at', '0') or 0)
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
        if await self._is_muted(message.sender_id):
            return Decision(False, 'muted'), ''
        recent_user_count = await self.store.count_rate_events(message.sender_id, 'reply', self.config.policy.user_window_seconds, message.chat_id)
        daily_user_count = await self.store.count_rate_events(message.sender_id, 'reply', 24 * 3600, message.chat_id)
        triggered = is_triggered(message, self.config, self.config.listener.account_username)
        media_followup = await self._media_followup_info(message)
        direct_sticker_request = user_requests_sticker(message.text)
        retry_sticker_request = sticker_retry_feedback(message.text) and bool(message.reply_to_zero or media_followup)
        if direct_sticker_request or retry_sticker_request:
            triggered = True
            logger.info('STICKER_INTENT_TRIGGERED trace_id=%s chat_id=%s sender_id=%s direct=%s retry=%s reply_to_zero=%s media_followup=%s', message.trace_id or '-', message.chat_id, message.sender_id, direct_sticker_request, retry_sticker_request, message.reply_to_zero, bool(media_followup))
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
        # Only exact search commands can turn Web Search into a trigger.
        should_interject = (not triggered) and await self._should_interject(message)
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
        if social_decision.should_ignore and not triggered and not should_interject and social_decision.emotion not in {'sad', 'conflict'}:
            return Decision(False, f'social_{social_decision.reason}'), ''
        if social_decision.should_ask:
            should_interject = True
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
        return None, ''

    # ------------------------------------------------------------------
    # MAIN REPLY PATH (shared entry for all output paths)
    # ------------------------------------------------------------------
    async def _generate_with_knowledge_tool(self, message: IncomingMessage, prompt: str, chat_id: int, evidence: dict | None = None) -> str:
        complete_with_tools = getattr(self.router, 'complete_with_tools', None)
        if not complete_with_tools:
            return await self._generate_and_sanitize(message, prompt, chat_id)
        tools = [
            {'name': 'read_knowledge', 'description': 'Read relevant source-backed public facts and news from Zero Knowledge Memory. Use only when needed; do not use for greetings or casual conversation.', 'parameters': {'type': 'object', 'properties': {'query': {'type': 'string', 'description': 'The focused subject to look up.'}, 'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 5}}, 'required': ['query']}},
            {'name': 'read_market_price', 'description': 'Read a current public Binance Spot crypto price. Use for cryptocurrency price/rate requests. Never invent a number; the result includes source, unit, market type, and timestamp.', 'parameters': {'type': 'object', 'properties': {'symbol': {'type': 'string', 'description': 'Base crypto asset, e.g. BTC, ETH, BNB, SOL.'}, 'quote': {'type': 'string', 'description': 'Quote asset, normally USDT.'}}, 'required': ['symbol']}},
            {'name': 'read_iran_market_price', 'description': 'Read current Iran dollar, gold, and coin rates from Navasan. Symbols: usd, 18ayar, sekkeh. Never invent a number; result includes unit, source, change, and timestamp.', 'parameters': {'type': 'object', 'properties': {'asset': {'type': 'string', 'enum': ['usd', '18ayar', 'sekkeh']}}, 'required': ['asset']}},
            {'name': 'read_usdt_toman_price', 'description': 'Read the current USDT/Toman order book from Nobitex. Returns best ask (buy), best bid (sell), average, unit Toman, source, market type, and timestamp. Never invent a number.', 'parameters': {'type': 'object', 'properties': {}}},
        ]
        result = await complete_with_tools(prompt, tools, max_output_tokens=reply_token_limit(message.text or ''))
        calls = result.metadata.get('tool_calls', []) if result.metadata else []
        if not calls:
            calls = deterministic_market_tool_calls(message.text) if is_current_price_or_market_query(message.text) else []
        if not calls:
            raw = sanitize_internal_search_status(result.text or '')
            if not raw:
                raw = 'فعلاً نتونستم پاسخ مناسبی آماده کنم؛ یک لحظه بعد دوباره بپرس.'
            return await self._maybe_reply_with_sticker(raw, chat_id=chat_id, user_text=message.text or '')
        blocks = []
        for call in calls[:3]:
            name, args = call.get('name'), call.get('arguments') or {}
            if name == 'read_knowledge' and self.knowledge:
                query = str(args.get('query') or message.text or '')[:500]
                try: limit = max(1, min(5, int(args.get('max_results', 3))))
                except (TypeError, ValueError): limit = 3
                value = await self.knowledge.retrieval_context(query, policy=KnowledgePolicy(max_items=limit, context_token_budget=900))
                value = value or 'No relevant Knowledge Memory items were found. Do not invent facts.'
                blocks.append(f'[TOOL_RESULT read_knowledge]\n{value}\n[/TOOL_RESULT]')
                logger.info('KNOWLEDGE_TOOL_EXECUTED trace_id=%s query_chars=%s result_chars=%s', message.trace_id or '-', len(query), len(value))
            elif name == 'read_market_price':
                symbol = str(args.get('symbol') or args.get('asset') or '')[:20]
                quote = str(args.get('quote') or 'USDT')[:20]
                try:
                    value = await self.market_prices.get_spot_price(symbol, quote)
                    encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
                except PriceAPIError as exc:
                    encoded = json.dumps({'error': str(exc), 'source': 'Binance Spot API'}, ensure_ascii=False)
                blocks.append(f'[TOOL_RESULT read_market_price]\n{encoded}\n[/TOOL_RESULT]')
                logger.info('MARKET_PRICE_TOOL_EXECUTED trace_id=%s symbol=%s', message.trace_id or '-', symbol.upper())
            elif name == 'read_iran_market_price':
                asset = str(args.get('asset') or '')[:40].lower()
                try:
                    value = await self.navasan_prices.get_price(asset)
                    encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
                except PriceAPIError as exc:
                    if asset in {'18ayar', 'sekkeh'}:
                        try:
                            value = await self.tgju_prices.get_price(asset)
                            encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
                            logger.info('MARKET_WEB_FALLBACK_USED trace_id=%s asset=%s source=TGJU', message.trace_id or '-', asset)
                        except PriceAPIError as web_exc:
                            encoded = json.dumps({'error': str(exc), 'web_fallback_error': str(web_exc), 'source': 'Navasan API + TGJU'}, ensure_ascii=False)
                    else:
                        encoded = json.dumps({'error': str(exc), 'source': 'Navasan API'}, ensure_ascii=False)
                blocks.append(f'[TOOL_RESULT read_iran_market_price]\n{encoded}\n[/TOOL_RESULT]')
                logger.info('IRAN_MARKET_PRICE_TOOL_EXECUTED trace_id=%s asset=%s', message.trace_id or '-', asset)
            elif name == 'read_usdt_toman_price':
                try:
                    value = await self.nobitex_prices.get_usdt_toman()
                    encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
                except PriceAPIError as exc:
                    encoded = json.dumps({'error': str(exc), 'source': 'Nobitex API'}, ensure_ascii=False)
                blocks.append(f'[TOOL_RESULT read_usdt_toman_price]\n{encoded}\n[/TOOL_RESULT]')
                logger.info('USDT_TOMAN_PRICE_TOOL_EXECUTED trace_id=%s', message.trace_id or '-')
        if not blocks:
            return await self._generate_and_sanitize(message, prompt, chat_id)
        if evidence is not None:
            evidence['trusted_text'] = '\n\n'.join(blocks)
        final_prompt = prompt + '\n\n' + '\n\n'.join(blocks) + '\nUse tool results when relevant. If a result has an error, report that honestly. Return the final natural reply only; never invent a price.'
        return await self._generate_and_sanitize(message, final_prompt, chat_id)

    async def _generate_and_sanitize(self, message: IncomingMessage, prompt: str, chat_id: int) -> str:
        result = await self.router.complete(prompt, max_output_tokens=reply_token_limit(message.text or ''))
        raw = sanitize_internal_search_status(result.text or '')
        try:
            await self.memory_v3.observe(message, raw)
            if os.getenv('ZERO_PROACTIVE_FOLLOWUP_ENABLED','false').lower()=='true':
                outcome=await self.proactive_followups.consider(message)
                await self.memory_v3.metric(message.trace_id or '-', 'proactive_followup', outcome)
        except Exception as exc:
            logger.warning('MEMORY_V2_WRITE_FAILED trace_id=%s exception_type=%s', message.trace_id or '-', type(exc).__name__)
        return await self._maybe_reply_with_sticker(raw, chat_id=chat_id, user_text=message.text or '')

    def _memory_target(self, message: IncomingMessage) -> tuple[int, str]:
        text=(message.text or '').casefold()
        if message.resolved_target_user_id and re.search(r'@\w+.*(?:کیه|کی هست|میشناسی)|(?:کیه|کی هست).*@\w+',text): return message.resolved_target_user_id,'mentioned_user'
        if message.reply_sender_id and not message.reply_sender_is_bot and re.search(r'این کیه|این شخص|کیه',text): return message.reply_sender_id,'reply_target'
        return message.sender_id,'speaker'

    async def _planned_memory_context(self, message: IncomingMessage, trace_id: str) -> tuple[str, dict]:
        """Planner may request evidence; executor enforces chat scope and bounded reads."""
        if os.getenv('ZERO_MEMORY_V3_PLANNER_ENABLED', os.getenv('ZERO_MEMORY_V2_PLANNER_ENABLED', 'false')).lower() != 'true': return '', {'used':False}
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

    async def _handle_no_media(self, message: IncomingMessage, decision: Decision, intent: Intent) -> tuple[Decision, str]:
        recent = await self.store.get_recent(message.chat_id, limit=100)
        if self.v1_memory_runtime_enabled:
            logger.info('MEMORY_RETRIEVAL_STARTED trace_id=%s chat_id=%s query_terms=%s', message.trace_id or '-', message.chat_id, len(re.findall(r'[\wآ-ی‌]{3,}', (message.text or '').lower())))
            layered = await self.store.retrieve_layered_memory(message.chat_id, message.text, sender_id=message.sender_id, short_limit=1, medium_limit=4, long_limit=6)
            social_plus_rows = await self.social_plus.context(message.chat_id, message.text)
            media_rows = [row for row in await self.store.get_recent_media_context(message.chat_id, message.text, limit=5) if int(row.get('sender_id') or 0) == int(message.sender_id)]
        else:
            layered = {'short': [], 'medium': [], 'long': []}; social_plus_rows = []; media_rows = []
            logger.info('MEMORY_V1_RUNTIME_DISABLED trace_id=%s', message.trace_id or '-')
        followup_info = await self._media_followup_info(message)
        if followup_info:
            logger.info('MEMORY_MEDIA_CONTEXT_RETRIEVED trace_id=%s chat_id=%s sender_id=%s media_message_id=%s media_type=%s age_seconds=%s reason=validated_followup', message.trace_id or '-', message.chat_id, message.sender_id, followup_info['media_message_id'], followup_info['media_type'], followup_info['age_seconds'])
            matching = next((r for r in media_rows if int(r.get('message_id') or 0) == followup_info['media_message_id']), None)
            if matching and not (matching.get('summary') or '').strip():
                return decision, f"یک {followup_info['media_type']} فرستادی، ولی محتوایش را دقیق تحلیل نکردم."
        if media_rows:
            logger.info('MEMORY_MEDIA_CONTEXT_RETRIEVED trace_id=%s chat_id=%s count=%s', message.trace_id or '-', message.chat_id, len(media_rows))
        logger.info('MEMORY_RETRIEVED trace_id=%s chat_id=%s short_count=%s medium_count=%s long_count=%s', message.trace_id or '-', message.chat_id, len(layered['short']), len(layered['medium']), len(layered['long']))
        group_summary = build_group_summary(recent, [])
        memory_lines = []
        for row in media_rows:
            detail = row.get('summary') or f"یک {row.get('media_type','media')} فرستاده شد؛ تحلیل دقیق ثبت نشده است"
            memory_lines.append(f"[SHORT_MEDIA_CONTEXT] message_id={row.get('message_id')} type={row.get('media_type')} caption={row.get('caption','')[:160]} detail={detail[:240]}")
        for row in layered['short']:
            memory_lines.append(f"[SHORT_CONTEXT] topic={row.get('active_topic','')} mood={row.get('mood','neutral')} should_reply={row.get('should_reply',0)}")
        for row in social_plus_rows[:6]:
            if row.get('kind') == 'thread':
                memory_lines.append(f"[SOCIAL_THREAD] topic={row.get('topic','')} summary={row.get('summary','')[:240]}")
            elif row.get('kind') == 'inside_joke':
                memory_lines.append(f"[INSIDE_JOKE] phrase={row.get('phrase','')[:80]}")
        for row in layered['medium']:
            memory_lines.append(f"[MEDIUM_MEMORY] {row.get('topic','')}: {row.get('summary','')}")
        for row in layered['long']:
            memory_lines.append(f"[LONG_MEMORY] [{row.get('category','')}] {row.get('content','')}")
        # Defense in depth: storage resolves conflicts, but prompt construction must
        # never expose duplicate keys or two active values for the same memory key.
        deduped = []
        seen_keys = set()
        for line in memory_lines:
            normalized = re.sub(r'\s+', ' ', line.casefold()).strip()
            tag = re.match(r'\[([^]]+)\]', normalized)
            body = normalized[tag.end():].strip() if tag else normalized
            if tag and tag.group(1) == 'long_memory':
                category = re.match(r'\[([^]]+)\]', body)
                key = ('long_memory', category.group(1).strip() if category else body)
            elif tag and tag.group(1) in {'medium_memory', 'social_thread', 'inside_joke'}:
                key = (tag.group(1), re.split(r':|=', body, maxsplit=1)[0].strip())
            else:
                key = (tag.group(1) if tag else 'raw', normalized)
            if key in seen_keys:
                logger.info('MEMORY_CONTEXT_DEDUPED trace_id=%s chat_id=%s key=%s', message.trace_id or '-', message.chat_id, key[0])
                continue
            seen_keys.add(key)
            deduped.append(line)
        memory_lines = deduped
        short_lines = [x for x in memory_lines if x.startswith(('[SHORT_', '[SOCIAL_', '[INSIDE_'))]
        medium_lines = [x for x in memory_lines if x.startswith('[MEDIUM_')]
        long_lines = [x for x in memory_lines if x.startswith('[LONG_')]
        budget = {'short': 10000, 'medium': 8000, 'long': 7000}
        before = len(memory_lines)
        def trim_lines(lines, limit):
            out, used = [], 0
            for line in lines:
                if used + len(line) > limit: break
                out.append(line); used += len(line)
            return out
        memory_lines = trim_lines(short_lines, budget['short']) + trim_lines(medium_lines, budget['medium']) + trim_lines(long_lines, budget['long'])
        dropped = before - len(memory_lines)
        if dropped:
            logger.info('MEMORY_CONTEXT_TRIMMED trace_id=%s chat_id=%s dropped_count=%s', message.trace_id or '-', message.chat_id, dropped)
        logger.info('MEMORY_BUDGET_APPLIED trace_id=%s chat_id=%s short_tokens=%s medium_tokens=%s long_tokens=%s total_memory_tokens=%s dropped_count=%s final_context_ratio=%.3f', message.trace_id or '-', message.chat_id, len(' '.join(short_lines))//4, len(' '.join(medium_lines))//4, len(' '.join(long_lines))//4, len(' '.join(memory_lines))//4, dropped, min(1.0, len(' '.join(memory_lines)) / 10400))
        layered_memory_context = '\n'.join(memory_lines)
        logger.info('MEMORY_CONTEXT_BUILT trace_id=%s chat_id=%s lines=%s', message.trace_id or '-', message.chat_id, len(memory_lines))
        mode = await self._mode()
        clean_user_text = strip_trigger(message.text, self.config.listener.account_username)
        search_command = parse_search_command(message.text)
        if search_command:
            clean_user_text = search_command[1]
        trace_id = message.trace_id or '-'
        web_context = ''
        memory_context = layered_memory_context
        telegram_context = ''
        live_market_disclosure = ''
        market_searched_at = ''
        web_outcome = None
        memory_plan=plan_memory(clean_user_text)
        logger.info('MEMORY_RETRIEVAL_PLAN trace_id=%s plan=%s chat_id=%s sender_id=%s',trace_id,memory_plan,message.chat_id,message.sender_id)
        if memory_plan=='debug':
            debug_context = render_experience(self.experience_memory.retrieve(clean_user_text,debug=True,limit=3)) + '\n' + render_procedures([self.procedural_memory.retrieve('debug workflow')] if self.procedural_memory.retrieve('debug workflow') else [])
            memory_context = '\n'.join(x for x in (memory_context, debug_context) if x)
            logger.info('EXPERIENCE_MEMORY_RETRIEVED trace_id=%s debug=true',trace_id)
        elif memory_plan=='world':
            world_context = render_world(self.world_model.resolve_query(clean_user_text))
            memory_context = '\n'.join(x for x in (memory_context, world_context) if x)
            logger.info('WORLD_MODEL_RETRIEVED trace_id=%s',trace_id)
        if self.v1_memory_runtime_enabled:
            memory_context, memory_meta = await compose_memory_context(
                store=self.store, semantic_memory=self.semantic_memory, message=message,
                recent=recent, layered=layered,
                extra_lines=[line for line in memory_lines if line.startswith(('[SHORT_MEDIA_CONTEXT]', '[SOCIAL_THREAD]', '[INSIDE_JOKE]'))],
                v3_memory=self.memory_v3,
            )
        else:
            memory_context, memory_meta = '', {'chars': 0, 'reply_chain_depth': 0, 'target_ids': [], 'ambiguous': False}
        if self.v1_memory_runtime_enabled and memory_plan == 'debug':
            memory_context = '\n'.join(x for x in (memory_context, debug_context) if x)
        elif self.v1_memory_runtime_enabled and memory_plan == 'world':
            memory_context = '\n'.join(x for x in (memory_context, world_context) if x)
        # V2 always evaluates in shadow when enabled by environment; only active mode
        # replaces V1 context, preventing dual memory injection.
        target_user_id, target_kind = self._memory_target(message)
        identity_lookup=target_kind != 'speaker' and bool(re.search(r'کیه|کی هست|میشناسی|who is|who are',message.text or '',re.I))
        planned_context, planner_meta = await self._planned_memory_context(message,trace_id)
        if planner_meta.get('used'):
            v2_context, v2_meta = planned_context, {'selected':planner_meta.get('selected_count',0),'tokens':len(planned_context)//4,'ids':[],'temporal_rejected':0}
        else:
            v2_context, v2_meta = await self.memory_v3.context(message, target_user_id=target_user_id, identity_lookup=identity_lookup)
        if v2_context and target_kind != 'speaker': v2_context = f'Retrieval target: {target_kind}; do not attribute these facts to the current speaker.\n' + v2_context
        await self.memory_v3.metric(trace_id, 'shadow' if self.memory_v3.shadow else 'active', {
            'v1_memory_tokens': len(memory_context) // 4, 'v2_selected_items': v2_meta.get('selected', 0),
            'v2_selected_tokens': v2_meta.get('tokens', 0), 'temporal_rejected':v2_meta.get('temporal_rejected',0), 'target_kind':target_kind, 'target_is_speaker':target_kind=='speaker', 'planner_used':planner_meta.get('used',False), 'planner_success':planner_meta.get('success',False), 'plan_version':1 if planner_meta.get('used') else 0, 'operation':planner_meta.get('operation',''), 'actor_count':planner_meta.get('actors',0), 'subject_count':planner_meta.get('subjects',0), 'unresolved_entity_count':planner_meta.get('unresolved',0), 'time_filter_kind':planner_meta.get('time',''), 'evidence_mode':planner_meta.get('evidence_mode',''), 'candidate_count':planner_meta.get('candidate_count',0), 'planner_fallback_reason':planner_meta.get('fallback',''), 'planner_latency_ms':planner_meta.get('latency_ms',0), 'context_total_tokens': (len(memory_context) + len(v2_context)) // 4,
        })
        # V3 is a second, scoped source during migration; never discard existing
        # contextual layers before their migrated equivalents are verified.
        if self.memory_v3.enabled and not self.memory_v3.shadow and v2_context:
            memory_context = '\n\n'.join(part for part in (memory_context, v2_context) if part)
        logger.info(
            'MEMORY_CONTEXT_COMPOSED trace_id=%s chat_id=%s sender_id=%s chars=%s reply_chain_depth=%s target_ids=%s ambiguity=%s',
            trace_id, message.chat_id, message.sender_id, memory_meta['chars'],
            memory_meta['reply_chain_depth'], memory_meta['target_ids'], memory_meta['ambiguous'],
        )
        search_mode, search_text = search_command or ('', clean_user_text)
        deep_search = search_mode == 'deep' or is_deep_search_request(clean_user_text)
        is_telegram_request = is_telegram_search_request(clean_user_text)
        web_enabled = await self.web.is_tool_enabled()
        natural_web_intent = bool(search_text and needs_web_search(search_text))
        market_tool_intent = bool(search_text and is_current_price_or_market_query(search_text))
        web_intent = bool(search_text and (search_command or natural_web_intent) and not market_tool_intent)
        logger.info('WEB_SEARCH_ENABLED_CHECK trace_id=%s enabled=%s intent=%s natural=%s market_tool=%s mode=%s', trace_id, web_enabled, web_intent, natural_web_intent, market_tool_intent, search_mode or 'natural')
        if not web_enabled:
            logger.info('WEB_SEARCH_SKIPPED trace_id=%s reason=disabled', trace_id)
            if web_intent:
                return decision, 'وب‌سرچ فعلاً فعال نیست.'
        elif not web_intent:
            logger.info('WEB_SEARCH_SKIPPED trace_id=%s reason=intent_not_detected', trace_id)
        else:
            if deep_search:
                user_limit = 12 if message.sender_id == self.config.owner_user_id else 3
                allowed, used = await self.store.try_reserve_rate_event(message.sender_id, 'deep_search', 3600, user_limit)
                if not allowed:
                    logger.info('DEEP_SEARCH_RATE_LIMIT trace_id=%s sender_id=%s used=%s limit=%s', trace_id, message.sender_id, used, user_limit)
                    return Decision(True, 'deep_search_rate_limit'), 'سهمیهٔ سرچ عمیق این ساعتت تموم شده؛ کمی بعد دوباره امتحان کن.'
                global_allowed, global_used = await self.store.try_reserve_rate_event(0, 'deep_search_global', 3600, 30)
                if not global_allowed:
                    logger.info('DEEP_SEARCH_GLOBAL_LIMIT trace_id=%s used=%s limit=30', trace_id, global_used)
                    return Decision(True, 'deep_search_global_limit'), 'ظرفیت سرچ عمیق فعلاً پر شده؛ کمی بعد دوباره امتحان کن.'
            try:
                run_search = self.web.run(
                    search_text,
                    reply_text=message.reply_text,
                    recent_messages=recent,
                    trace_id=trace_id,
                    chat_id=message.chat_id,
                    sender_id=message.sender_id,
                    message_id=message.message_id,
                    thread_id=message.thread_id,
                    reply_to_message_id=message.reply_to_message_id,
                    search_session_id=f'{message.chat_id}:{message.sender_id}:{message.thread_id or message.reply_to_message_id or "root"}',
                    force_search=bool(search_command),
                    deep=deep_search,
                )
                web_outcome = await asyncio.wait_for(run_search, timeout=45.0) if deep_search else await run_search
            except asyncio.TimeoutError:
                logger.warning('DEEP_SEARCH_TIMEOUT trace_id=%s timeout_seconds=45', trace_id)
                return decision, 'سرچ عمیق به سقف زمانی رسید و نتیجهٔ قابل‌اعتماد کامل نشد؛ کمی بعد دوباره امتحان کن.'
            except Exception as exc:
                logger.warning('WEB_SEARCH_FAILED trace_id=%s exception_type=%s', trace_id, type(exc).__name__)
                return decision, 'جستجو در این نوبت کامل نشد؛ کمی بعد دوباره امتحان کن.'
            logger.info('WEB_OUTCOME trace_id=%s result_count=%s all_providers_failed=%s no_results=%s context_chars=%s', trace_id, len(web_outcome.results), web_outcome.all_providers_failed, web_outcome.no_results, len(web_outcome.context or ''))
            if web_outcome.clarification_required:
                reply = 'چی رو بگردم؟ موضوع، اسم، قیمت یا خبری که می‌خوای بررسی کنم رو هم بگو.'
                self.web.mark_response_sent(trace_id=trace_id, result_count=0, guarded=True)
                logger.info('WEB_REQUEST_CONTEXT_CLEARED trace_id=%s source_count=0', trace_id)
                return decision, reply
            if not web_outcome.intent.supported:
                self.web.mark_response_sent(trace_id=trace_id, result_count=0, guarded=True)
                logger.info('WEB_INTERNAL_STATUS_SUPPRESSED trace_id=%s reason=unsupported_intent', trace_id)
                return decision, ''
            if web_outcome.results:
                web_context = web_outcome.context
                if web_outcome.intent.category == 'current_price_or_market_query':
                    market_searched_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                    searched_at_utc = market_searched_at
                    live_market_disclosure = build_live_market_disclosure(
                        web_outcome.results[0].title,
                        web_outcome.results[0].url,
                        searched_at_utc,
                    )
            elif web_outcome.all_providers_failed:
                self.web.mark_response_sent(trace_id=trace_id, result_count=0, guarded=True)
                logger.info('WEB_INTERNAL_STATUS_SUPPRESSED trace_id=%s reason=providers_failed', trace_id)
                return decision, 'فعلاً Google Search در دسترس نیست؛ کمی بعد دوباره امتحان کن.'
            elif web_outcome.no_results:
                self.web.mark_response_sent(trace_id=trace_id, result_count=0, guarded=True)
                logger.info('WEB_INTERNAL_STATUS_SUPPRESSED trace_id=%s reason=no_results', trace_id)
                return decision, 'برای این جستجو نتیجه‌ای پیدا نکردم.'

        from .telegram_search import TelegramSearchClient
        tg = TelegramSearchClient(self.config, self.store, web=self.web)
        tg_enabled = await tg.is_tool_enabled()
        tg_intent = is_telegram_request
        logger.info('TG_SEARCH_ENABLED_CHECK trace_id=%s enabled=%s intent=%s', trace_id, tg_enabled, tg_intent)
        if not tg_enabled:
            logger.info('TG_SEARCH_ARCHIVED trace_id=%s reason=legacy_telegram_search_disabled', trace_id)
            if tg_intent:
                logger.info('TG_INTERNAL_STATUS_SUPPRESSED trace_id=%s reason=archived', trace_id)
                return decision, ''
            logger.info('TG_SEARCH_SKIPPED trace_id=%s reason=disabled', trace_id)
        elif not tg_intent:
            logger.info('TG_SEARCH_SKIPPED trace_id=%s reason=intent_not_detected', trace_id)
        else:
            from .telegram_search import TelegramSearchIntentDetector, TelegramSearchRequest, TelegramSearchContextBuilder
            tg_intent = TelegramSearchIntentDetector().detect(search_text)
            if search_mode in {'telegram', 'combined'}:
                tg_intent = tg_intent.__class__('telegram_message_search', search_text, confidence=1.0)
            tg_query = build_search_query(tg_intent.query or search_text, reply_text=message.reply_text, recent_messages=recent)
            logger.info('TG_SEARCH_INTENT_DETECTED trace_id=%s intent=%s query_hash=%s', trace_id, tg_intent.name, __import__('hashlib').sha256(tg_query.encode()).hexdigest()[:16])
            if tg_intent.name == 'unsupported_private_access':
                telegram_context = 'Telegram private/invite-only access is unsupported without explicit owner policy.'
            elif not tg_query.strip():
                logger.warning('TG_SEARCH_NO_RESULTS trace_id=%s reason=empty_query', trace_id)
            else:
                try:
                    req = TelegramSearchRequest(trace_id=trace_id, chat_id=message.chat_id, sender_id=message.sender_id, search_session_id=f'{message.chat_id}:{message.sender_id}:{message.reply_to_message_id or "root"}', thread_id=message.thread_id, reply_to_message_id=message.reply_to_message_id, query=tg_query, intent=tg_intent.name, limit=5, inspect_target=tg_intent.target, allow_web_fallback=True)
                    logger.info('TG_SEARCH_ROUTED trace_id=%s intent=%s', trace_id, tg_intent.name)
                    outcomes = await tg.search_request(req)
                    telegram_context = TelegramSearchContextBuilder().build(outcomes, limit=5, max_chars=6000)
                    result_count = sum(len(x.results) for x in outcomes)
                    logger.info('TG_SEARCH_CONTEXT_BUILT trace_id=%s result_count=%d', trace_id, result_count)
                    if not result_count:
                        telegram_context = 'در محدوده قابل‌دسترسی این حساب و منابع عمومی نتیجه‌ای پیدا نکردم.'
                        logger.info('TG_SEARCH_NO_RESULTS trace_id=%s', trace_id)
                except Exception as e:
                    telegram_context = 'جستجوی Telegram در این نوبت در دسترس نبود؛ نتیجه‌ای ساخته نمی‌شود.'
                    logger.warning('TG_SEARCH_PROVIDER_FAILED trace_id=%s exception_type=%s', trace_id, type(e).__name__)

        doc_context=''; bundle_suppressed=set(); bundle_entries=[]
        if os.getenv('ZERO_GROUP_DOCUMENT_BUNDLING_ENABLED','false').lower()=='true':
            bundle_suppressed=self.document_bundles.active_part_ids(message.chat_id); bundle_entries=self.document_bundles.live_entries(message.chat_id)
            doc_context, _, _ = self.document_bundles.reference(message)
            if doc_context: memory_context='\n'.join(x for x in (memory_context,doc_context) if x)
        live_group_context=''; group_summary=''
        if os.getenv('ZERO_HYBRID_GROUP_CONTEXT_ENABLED','false').lower()=='true' and message.chat_id<0:
            try:
                live_group_context, summary_payload, group_meta=await self.group_context.build(message,bundle_suppressed)
                if bundle_entries: live_group_context='\n'.join([*bundle_entries,live_group_context])
                group_summary=json.dumps(summary_payload,ensure_ascii=False)
                await self.memory_v3.metric(trace_id,'group_context',group_meta)
            except Exception:
                live_group_context=''; group_summary=''
        prompt = build_reply_prompt(
            self.config, mode=mode, sender_label=message.sender_label,
            user_text=clean_user_text, reply_text=message.reply_text,
            recent=live_group_context.splitlines(), group_summary=group_summary,
            web_context=web_context, telegram_context=telegram_context, memory_context=memory_context,
            chat_id=message.chat_id, sender_id=message.sender_id, message_id=message.message_id, sender_is_bot=message.sender_is_bot, thread_id=message.thread_id, reply_to_message_id=message.reply_to_message_id,
            reply_sender_id=message.reply_sender_id, reply_sender_label=message.reply_sender_label, reply_sender_is_bot=message.reply_sender_is_bot,
            deep_research=deep_search,
        )
        tool_evidence: dict = {}
        reply_text = await self._generate_with_knowledge_tool(message, prompt, chat_id=message.chat_id, evidence=tool_evidence)
        raw_link_requested = bool(re.search(r'(?:لینک\s+خام|url\s+خام|raw\s+(?:link|url))', clean_user_text, re.I))
        if web_outcome and 'WEB_STATUS:' in (reply_text or ''):
            status_reply = 'فعلاً سرویس‌های جست‌وجو پاسخ ندادند؛ کمی بعد دوباره امتحان کن.' if web_outcome.all_providers_failed else 'برای این جست‌وجو نتیجه قابل‌اعتمادی پیدا نکردم.'
            logger.info('WEB_INTERNAL_STATUS_SUPPRESSED trace_id=%s status=present', trace_id)
            reply_text = status_reply
        if web_outcome and web_outcome.results:
            fallback_eligible = numeric_fallback_eligible(web_outcome.intent.category)
            logger.info('WEB_FALLBACK_ELIGIBILITY trace_id=%s query_hash=%s intent=%s category=%s eligible=%s reason=category_policy', trace_id, __import__('hashlib').sha256(web_outcome.plan.query.encode()).hexdigest()[:16], web_outcome.intent.kind.value, web_outcome.intent.category, fallback_eligible)
            logger.info('LLM_RESPONSE_BEFORE_GUARD trace_id=%s chars=%d', trace_id, len(reply_text or ''))
            guard = self.web.guard_answer(reply_text, web_outcome.results, trace_id=trace_id, trusted_text=tool_evidence.get('trusted_text', ''))
            logger.info('TRUTHFULNESS_GUARD_DECISION trace_id=%s reason=%s accepted=%s', trace_id, guard.reason or 'none', guard.allowed)
            guard_fallback_used = False
            if not guard.allowed:
                news_fallback = build_news_fallback(web_outcome.results, market_searched_at) if web_outcome.intent.category in {'latest_news', 'research_analysis'} else ''
                fallback = news_fallback or (build_numeric_fallback(web_outcome.results, market_searched_at) if fallback_eligible else '')
                if fallback:
                    reply_text = fallback
                    guard_fallback_used = True
                    logger.info('TRUTHFULNESS_GUARD_FALLBACK_USED trace_id=%s used=true', trace_id)
                else:
                    if not fallback_eligible: logger.info('WEB_FALLBACK_REJECTED trace_id=%s reason=category_not_numeric_or_news', trace_id)
                    if web_outcome.intent.category in {'current_price_or_market_query', 'market_rate', 'exchange_rate', 'numeric_live_value'}:
                        reply_text = 'قیمت قابل‌تأییدی از منابع زنده دریافت نکردم؛ برای جلوگیری از اعلام عدد اشتباه، این نوبت قیمت نمی‌فرستم.'
                    else:
                        seen=set(); source_items=[]
                        for item in web_outcome.results[:3]:
                            key=item.url.rstrip('/').lower()
                            if key in seen: continue
                            seen.add(key); source_items.append(source_link(item.url, item.publisher))
                        logger.info('WEB_SOURCE_DEDUPED trace_id=%s before=%d after=%d', trace_id, min(3,len(web_outcome.results)), len(source_items))
                        reply_text = 'پاسخ تولیدشده ادعای بدون پشتوانه داشت و ارسال نشد. منابع واقعی:\n' + '\n'.join(f'• {x}' for x in source_items)
                    logger.info('TRUTHFULNESS_GUARD_FALLBACK_USED trace_id=%s used=false', trace_id)
            if live_market_disclosure and not guard_fallback_used:
                reply_text = f'{reply_text.rstrip()}{live_market_disclosure}'
            reply_text = sanitize_source_display(reply_text, web_outcome.results, raw_link_requested)
            if deep_search:
                seen_domains: set[str] = set()
                deep_sources: list[str] = []
                for item in web_outcome.results:
                    domain = item.url.split('://', 1)[-1].split('/', 1)[0].lower().removeprefix('www.')
                    if domain in seen_domains:
                        continue
                    seen_domains.add(domain)
                    deep_sources.append(source_link(item.url, item.publisher))
                    if len(deep_sources) >= 15:
                        break
                if deep_sources:
                    appendix = '\n\nمنابع بررسی‌شده:\n' + '\n'.join(f'• {source}' for source in deep_sources)
                    body_limit = max(0, reply_char_limit(self.config, message.text) - len(appendix))
                    reply_text = reply_text[:body_limit].rstrip() + appendix
                    logger.info('DEEP_SEARCH_SOURCE_APPENDIX trace_id=%s unique_domains=%d', trace_id, len(deep_sources))
            logger.info('WEB_SOURCE_DEDUPED trace_id=%s source_count=%d unique_source_count=%d', trace_id, len(web_outcome.results), len({item.url.rstrip('/').lower() for item in web_outcome.results}))
            self.web.mark_response_sent(
                trace_id=trace_id,
                result_count=len(web_outcome.results),
                guarded=guard.allowed or guard_fallback_used,
            )
        reply_text = sanitize_source_display(reply_text, [], raw_link_requested)
        if web_intent:
            logger.info('WEB_REQUEST_CONTEXT_CLEARED trace_id=%s query_hash=%s source_count=%d', trace_id, __import__('hashlib').sha256((web_outcome.plan.query if web_outcome else clean_user_text).encode()).hexdigest()[:16], len(web_outcome.results) if web_outcome else 0)
        return decision, reply_text

    async def maybe_reply(self, message: IncomingMessage) -> tuple[Decision, str]:
        early_decision, early_text = await self._pre_check(message)
        if early_decision is not None:
            return early_decision, early_text
        search_command = parse_search_command(message.text)
        if search_command and not search_command[1]:
            command = '/deepsearch' if search_command[0] == 'deep' else '/search'
            return Decision(True, 'search_usage'), f'بعد از {command} موضوع جستجو رو بنویس.'
        intent = classify_intent(message.text, message.reply_text)
        return await self._handle_no_media(message, early_decision or Decision(True, 'triggered'), intent)

    async def maybe_reply_with_media(self, message: IncomingMessage, event) -> tuple[Decision, str]:
        if not self.config.vision.enabled:
            return await self.maybe_reply(message)

        media_event = event
        has_image = is_image_media(media_event)
        has_gif = is_gif_media(media_event)
        has_video = is_video_media(media_event)
        has_sticker = is_sticker_media(media_event)

        if not (has_image or has_gif or has_sticker) and event.is_reply:
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
        if early_decision is not None:
            return early_decision, early_text
        clean_user_text = strip_trigger(message.text, self.config.listener.account_username)
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
            layered = await self.store.retrieve_layered_memory(message.chat_id, clean_user_text, sender_id=message.sender_id, short_limit=1, medium_limit=4, long_limit=6)
            memory_context, memory_meta = await compose_memory_context(
                store=self.store, semantic_memory=self.semantic_memory, message=message,
                recent=recent, layered=layered,
            )
            logger.info('MEMORY_CONTEXT_COMPOSED trace_id=%s path=vision chars=%s reply_chain_depth=%s target_ids=%s ambiguity=%s', message.trace_id or '-', memory_meta['chars'], memory_meta['reply_chain_depth'], memory_meta['target_ids'], memory_meta['ambiguous'])
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

        decision = Decision(True, 'vision_unavailable')
        if has_gif or has_video:
            return decision, 'این GIF/ویدئو رو نتونستم درست بررسی کنم؛ محتوای حدسی نمی‌گم. دوباره بفرست یا یه فریم واضح ازش بفرست.'
        if has_sticker:
            return decision, 'این استیکر رو نتونستم درست بخونم؛ اگه منظورت تحلیلشه، دوباره بفرست.'
        return decision, 'این تصویر رو نتونستم درست بررسی کنم؛ پاسخ حدسی نمی‌دم. دوباره بفرست.'

    async def remember_message(self, message: IncomingMessage, role: str = 'user') -> None:
        # Bot messages remain an archive-only role; never let them enter human-memory paths.
        effective_role = 'bot' if role == 'user' and message.sender_is_bot else role
        await self.store.append_recent(
            message.chat_id, message.sender_id, message.sender_label, effective_role, message.text,
            platform=message.platform, account_scope=message.account_scope,
            telegram_message_id=message.message_id or None,
            reply_to_message_id=message.reply_to_message_id,
            thread_id=message.thread_id, sender_username=message.sender_username,
            sender_display_name=message.sender_display_name, trace_id=message.trace_id,
        )
        await self.memory_v3.record_message(message, role=effective_role)
        if os.getenv('ZERO_GROUP_DOCUMENT_BUNDLING_ENABLED','false').lower()=='true' and role=='user' and not message.sender_is_bot:
            await self.document_bundles.observe(message)
        if os.getenv('ZERO_PROACTIVE_FOLLOWUP_ENABLED','false').lower()=='true' and role=='user' and not message.sender_is_bot:
            feedback=await self.proactive_followups.feedback.observe(message)
            if feedback.get('recorded'):
                await self.memory_v3.metric(message.trace_id or '-', 'proactive_feedback', {'feedback_type':feedback['feedback_type'],'feedback_recorded':True})
        # Recent rows are an archive/ephemeral turn buffer, not V1 memory injection.
        if not self.v1_memory_runtime_enabled:
            return
        if role == 'user' and not message.sender_is_bot:
            await self.store.upsert_profile(
                message.chat_id, message.sender_id, message.sender_label,
                username=message.sender_username, display_name=message.sender_display_name,
            )
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

    async def build_monthly_group_memory(self, chat_id: int) -> dict[str, Any]:
        if not self.v1_memory_runtime_enabled:
            return {'status': 'v1_memory_disabled'}
        since = int(time.time()) - 30 * 86400
        summary = await self.build_daily_summary(chat_id, since_ts=since)
        period = await self.store.build_period_summary(chat_id, days=30, label='monthly_group')
        if not summary or 'پاسخ‌گویی در دسترس نیست' in summary or 'PROVIDERS_FAILED' in summary:
            return await self.store.update_monthly_group_memory(chat_id, actor_user_id=self.config.owner_user_id)
        try:
            memory_id = await self.store.add_long_memory(chat_id, 'group_monthly_summary', summary, created_by=self.config.owner_user_id, subject_user_id=None, source_message_ids=period.get('source_message_ids', []), confidence=.95)
        except ValueError:
            logger.warning('MONTHLY_GROUP_SUMMARY_REJECTED chat_id=%s reason=unsafe_summary_fallback', chat_id)
            return await self.store.update_monthly_group_memory(chat_id, actor_user_id=self.config.owner_user_id)
        return {'memory_id': memory_id, 'summary': summary, **period}

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
            asyncio.create_task(self._send_sticker_async(chat_id, mood))
        return cleaned
