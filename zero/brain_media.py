"""Sticker/GIF send path extracted from ZeroBrain. Behavior is unchanged."""
from __future__ import annotations

import logging
import random
import re
import time

from .gifs.decision import GifSendOutcome
from .gifs.service import GifService
from .stickers.decision import CANONICAL_MOODS, StickerIntent, StickerSendOutcome, normalize_mood

logger = logging.getLogger('zero.brain')

# Robust regex: catches STICKER:funny, [STICKER:funny], STICKER: funny.
# Note: NO \s* before STICKER — that would eat the preceding space.
_STICKER_RE = re.compile(r'\[?STICKER\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\]?', re.IGNORECASE)
_VALID_MOODS = CANONICAL_MOODS
_STICKER_WORDS_FA = ('استیکر', 'sticker', 'sticer')
_STICKER_WORDS_EN = ('sticker',)
_UNSAFE_OUTPUT_RE = re.compile(
    r'(?:لاگ(?:‌|\s*)ها?\s*(?:رو|را)?\s*پاک|پاک\s*کردن\s*(?:لاگ|ردپا|مدرک)|'
    r'مدرک(?:ی|ها)?\s*(?:دستش|دستشان)\s*نیفت|داشتم\s+لاگ(?:‌|\s*)ها?\s*رو\s*چک)',
    re.I,
)


def sanitize_mood(mood: str) -> str | None:
    return normalize_mood(mood, default="react")


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


def user_requests_gif(user_text: str) -> bool:
    low = normalize_sticker_text(user_text)
    if not low:
        return False
    has_gif = bool(re.search(r"(?:^|\s)(?:گیف|gif)(?:\s|$)", low))
    if not has_gif:
        return False
    verbs = (
        "بفرست", "بده", "بذار", "بزن", "میخوام", "می‌خوام", "دیگه",
        "send", "please", "another", "want",
    )
    return any(verb in low for verb in verbs) or low in {"گیف", "gif"}


def gif_negative_feedback(user_text: str) -> bool:
    low = normalize_sticker_text(user_text)
    return any(phrase in low for phrase in (
        "گیف نده", "گیف نفرست", "gif نده", "gif نفرست", "بس کن گیف", "اسپم نکن",
    ))


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
    low = normalize_sticker_text(user_text)
    mood_terms = (
        ('celebrate', ('تولد', 'جشن', 'تبریک', 'مبارک', 'party', 'celebrate')),
        ('funny', ('خنده', 'باحال', 'بامزه', 'funny', 'خنده دار', 'میخند')),
        ('sad', ('ناراحت', 'غم', 'گریه', 'sad', 'غمگین')),
        ('love', ('عشق', 'دوستت', 'قلب', 'love', '❤️')),
        ('angry', ('عصبانی', 'angry', 'قهر', 'اعصاب')),
        ('greeting', ('سلام', 'خداحافظ', 'greeting', 'hello', 'bye', ' hi')),
        ('shock', ('تعجب', 'شگفت', 'wow', 'عجب', 'شوکه')),
        ('approve', ('تایید', 'قبول', 'اوکی', 'آفرین', 'approve')),
        ('disapprove', ('رد', 'مخالف', 'نپسند', 'disapprove')),
        ('thinking', ('فکر', 'متفکر', 'thinking')),
        ('pray', ('دعا', 'الهی', 'pray')),
        ('cool', ('خفن', 'کول', 'cool')),
    )
    for mood, terms in mood_terms:
        if any(term in low for term in terms):
            return mood
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



class BrainMediaMixin:
    """Sticker and GIF sending. Mixed into ZeroBrain; uses store/config/client on self."""

    async def _send_sticker_async(
        self,
        chat_id: int,
        mood: str,
        direct_request: bool = False,
        retry_request: bool = False,
    ) -> StickerSendOutcome:
        async with self._sticker_send_lock:
            return await self._send_sticker_once(chat_id, mood, direct_request, retry_request)

    async def _send_sticker_once(
        self,
        chat_id: int,
        mood: str,
        direct_request: bool = False,
        retry_request: bool = False,
    ) -> StickerSendOutcome:
        canonical_mood = normalize_mood(mood, default="react") or "react"
        intent = StickerIntent(
            mood=canonical_mood,
            direct_request=direct_request,
            retry_request=retry_request,
            allow_generic_fallback=direct_request and canonical_mood == "react",
        )

        candidate_count = 0
        send_probability = 1.0
        random_sample: float | None = None
        confidence_threshold = 0.0

        def finish(
            reason: str,
            *,
            candidate_id: int | None = None,
            relevance_score: float = 0.0,
            fallback_level: str = "none",
            transport: str = "not_attempted",
        ) -> StickerSendOutcome:
            outcome = StickerSendOutcome(
                reason=reason,
                mood=canonical_mood,
                direct_request=direct_request,
                candidate_id=candidate_id,
                relevance_score=relevance_score,
                fallback_level=fallback_level,
                transport=transport,
                candidate_count=candidate_count,
                send_probability=send_probability,
                random_sample=random_sample,
                confidence_threshold=confidence_threshold,
            )
            logger.info(
                "STICKER_DECISION chat_id=%s mood=%s direct=%s reason=%s "
                "candidate_id=%s candidates=%s relevance=%.2f threshold=%.2f "
                "probability=%.3f sample=%s fallback=%s transport=%s",
                chat_id, canonical_mood, direct_request, reason, candidate_id,
                candidate_count, relevance_score, confidence_threshold,
                send_probability, random_sample, fallback_level, transport,
            )
            return outcome

        try:
            cfg = self.config.stickers
            enabled_raw = await self.store.get_setting("stickers_enabled", "")
            persisted_disabled = str(enabled_raw).casefold() in {"false", "0", "off"}
            if not cfg.enabled or persisted_disabled:
                return finish("disabled")
            if not cfg.auto_enabled and not direct_request:
                return finish("auto_disabled")

            trigger_type = "retry" if retry_request else ("direct" if direct_request else "auto")
            policy = await self.store.get_sticker_send_policy(
                chat_id, trigger_type=trigger_type
            )
            now = int(time.time())
            feedback_raw = await self.store.get_setting(
                f"sticker_negative_until:{chat_id}", "0"
            )
            feedback_until = int(feedback_raw or 0)
            if not direct_request and feedback_until > now:
                return finish("negative_feedback")
            hourly_limit = (
                cfg.direct_limit_per_hour if direct_request else cfg.limit_per_hour
            )
            if policy["sent_last_hour"] >= hourly_limit:
                return finish("hourly_limit")
            if (direct_request and not retry_request and policy["last_sent_at"]
                    and now - policy["last_sent_at"] < cfg.direct_cooldown_seconds):
                return finish("direct_cooldown")
            cooldown = cfg.cooldown_seconds * (2 if feedback_until > now else 1)
            if (not direct_request and policy["last_sent_at"]
                    and now - policy["last_sent_at"] < cooldown):
                return finish("cooldown")
            if (not direct_request
                    and policy["messages_since_last"] < cfg.min_messages_between):
                return finish("min_messages_between")
            if not direct_request:
                chance_raw = await self.store.get_setting("stickers_send_chance", "")
                try:
                    send_probability = float(chance_raw) if str(chance_raw) != "" else float(cfg.send_chance)
                except (TypeError, ValueError):
                    send_probability = float(cfg.send_chance)
                send_probability = max(0.0, min(1.0, send_probability))
                rng = getattr(self, "_sticker_rng", random)
                random_sample = float(rng.random())
                if random_sample > send_probability:
                    return finish("chance_rejected")

            from .stickers.library import StickerLibrary
            from .stickers.sender import StickerSender
            from .stickers.models import StickerCandidate

            rng = getattr(self, "_sticker_rng", random)
            library = StickerLibrary(self.config, self.store, cfg, rng=rng)
            excluded_doc_ids = (
                set(await self.store.get_recent_sticker_doc_ids(
                    chat_id, int(getattr(cfg, "repeat_window", 20))
                )) if (retry_request or not direct_request) else set()
            )
            exact_pool = await library.search(
                mood=canonical_mood, limit=200, min_quality=0.45
            )
            candidate_count = sum(
                1 for item in exact_pool if item.doc_id not in excluded_doc_ids
            )
            if exact_pool and candidate_count == 0 and excluded_doc_ids:
                return finish("repeat_window")
            candidate = await library.get_random_sticker(
                mood=canonical_mood, min_quality=0.45, chat_id=chat_id,
                exclude_doc_ids=excluded_doc_ids,
            )
            relevance_score = (
                0.70 + 0.30 * float(candidate.quality_score) if candidate else 0.0
            )
            confidence_threshold = float(cfg.min_relevance_score)
            fallback_level = "exact"
            if candidate is None and intent.allow_generic_fallback:
                generic_pool = await library.search(mood="", limit=200, min_quality=0.45)
                candidate_count = sum(
                    1 for item in generic_pool if item.doc_id not in excluded_doc_ids
                )
                candidate = await library.get_random_sticker(
                    mood="", min_quality=0.45, chat_id=chat_id,
                    exclude_doc_ids=excluded_doc_ids,
                )
                relevance_score = (
                    0.45 + 0.15 * float(candidate.quality_score) if candidate else 0.0
                )
                confidence_threshold = float(cfg.generic_min_relevance_score)
                fallback_level = "generic_direct"
            if candidate is None:
                return finish("no_relevant_candidate")
            if relevance_score < confidence_threshold:
                return finish(
                    "below_confidence", candidate_id=candidate.doc_id,
                    relevance_score=relevance_score, fallback_level=fallback_level,
                )
            if not self._client:
                return finish(
                    "no_client", candidate_id=candidate.doc_id,
                    relevance_score=relevance_score, fallback_level=fallback_level,
                )

            sender = StickerSender(self.config, self.store, client=self._client)
            candidate_obj = StickerCandidate(
                sticker=candidate,
                score=candidate.quality_score,
                match_reason=f"mood:{canonical_mood}",
                relevance_score=relevance_score,
                fallback_level=fallback_level,
            )
            if candidate.is_video and not candidate.stickerset_id and not candidate.stickerset_short_name:
                sent = await sender.send_media(chat_id, candidate_obj)
            else:
                sent = await sender.send_sticker(chat_id, candidate_obj)
            if not sent:
                await self.store.record_sticker_send_failure(candidate.doc_id)
                return finish(
                    "transport_failed", candidate_id=candidate.doc_id,
                    relevance_score=relevance_score, fallback_level=fallback_level,
                    transport="failed",
                )

            await self.store.add_rate_event(0, "sticker_sent", chat_id)
            await self.store.record_sticker_send(
                candidate.doc_id, chat_id, trigger_type=trigger_type
            )
            return finish(
                "sent", candidate_id=candidate.doc_id,
                relevance_score=relevance_score, fallback_level=fallback_level,
                transport="sent",
            )
        except Exception as exc:
            logger.warning(
                "STICKER_SEND_EXCEPTION mood=%s error=%s",
                canonical_mood, type(exc).__name__,
            )
            return finish("transport_exception", transport=type(exc).__name__)

    async def _send_gif_async(
        self,
        chat_id: int,
        mood: str,
        direct_request: bool = False,
        retry_request: bool = False,
    ) -> GifSendOutcome:
        async with self._gif_send_lock:
            return await self._send_gif_once(
                chat_id,
                mood,
                direct_request=direct_request,
                retry_request=retry_request,
            )

    async def _send_gif_once(
        self,
        chat_id: int,
        mood: str,
        direct_request: bool = False,
        retry_request: bool = False,
    ) -> GifSendOutcome:
        return await GifService(
            self.config,
            self.store,
            self._client,
            rng=getattr(self, "_gif_rng", None),
        ).send(
            chat_id,
            mood,
            direct_request=direct_request,
            retry_request=retry_request,
        )

    async def _has_sticker_for_mood(self, mood: str) -> bool:
        try:
            from .stickers.library import StickerLibrary
            lib = StickerLibrary(self.config, self.store, self.config.stickers)
            return (await lib.get_random_sticker(mood=mood, min_quality=0.30)) is not None
        except Exception:
            return False

    async def _maybe_reply_with_sticker(self, text: str, chat_id: int, user_text: str) -> str:
        """Sanitize control markers and route sticker/GIF requests by media type."""
        cleaned, mood = sanitize_outgoing_text(text)
        low_user_text = normalize_sticker_text(user_text)
        direct_sticker = user_requests_sticker(user_text)
        direct_gif = user_requests_gif(user_text)
        generic_retry = sticker_retry_feedback(user_text)
        latest_media = None
        if generic_retry and not (direct_sticker or direct_gif):
            latest_media = await self.store.get_latest_media_send_type(chat_id)
            direct_gif = latest_media == "gif"
            direct_sticker = latest_media == "sticker"
            logger.info(
                "MEDIA_RETRY_REQUEST_DETECTED chat_id=%s latest_media=%s",
                chat_id, latest_media or "none",
            )

        if gif_negative_feedback(user_text):
            await self.store.set_setting(
                f"gif_negative_until:{chat_id}", str(int(time.time()) + 3600)
            )
            logger.info("GIF_NEGATIVE_FEEDBACK_APPLIED chat_id=%s", chat_id)
            return cleaned

        if direct_gif and not direct_sticker:
            gif_mood = detect_mood_from_user(user_text)
            outcome = await self._send_gif_async(
                chat_id,
                gif_mood,
                direct_request=True,
                retry_request=generic_retry,
            )
            if outcome.sent:
                return cleaned or "😉"
            if outcome.reason in {"no_relevant_candidate", "repeat_window", "below_relevance_threshold"}:
                return cleaned or "هنوز GIF مرتبطی برای این حال‌وهوا یاد نگرفتم 😅"
            return cleaned or "فعلاً نتونستم GIF بفرستم 😅"

        direct_request = direct_sticker
        retry_request = generic_retry and direct_sticker
        if retry_request:
            logger.info("STICKER_RETRY_REQUEST_DETECTED chat_id=%s", chat_id)
        reaction_request = (
            not direct_request
            and any(term in low_user_text for term in (
                "ری‌اکشن", "ری اکشن", "ریاکشن", "واکنش", "reaction", "react"
            ))
            and any(term in low_user_text for term in (
                "بزن", "بذار", "بده", "بفرست", "زن", "کن", "send", "add"
            ))
        )
        if reaction_request:
            logger.info("STICKER_AUTO_SKIPPED chat_id=%s reason=reaction_request", chat_id)
            return cleaned

        if sticker_negative_feedback(user_text):
            await self.store.set_setting(
                f"sticker_negative_until:{chat_id}", str(int(time.time()) + 3600)
            )
            logger.info("STICKER_NEGATIVE_FEEDBACK_APPLIED chat_id=%s", chat_id)
            return cleaned
        if direct_request and mood is None:
            mood = detect_mood_from_user(user_text)
            logger.info(
                "STICKER_DIRECT_REQUEST_DETECTED chat_id=%s mood=%s marker_present=false retry=%s",
                chat_id, mood, retry_request,
            )
        outcome = None
        if mood and self.config.stickers.enabled:
            if not direct_request and (not sticker_context_allowed(user_text) or cleaned.strip()):
                logger.info(
                    "STICKER_AUTO_SKIPPED chat_id=%s reason=context_or_text_response",
                    chat_id,
                )
                return cleaned
            outcome = await self._send_sticker_async(
                chat_id,
                mood,
                direct_request=direct_request,
                retry_request=retry_request,
            )

        if mood and _empty_when_marker_was_present(text, cleaned):
            if outcome is not None and bool(getattr(outcome, "sent", False)):
                return "😉"
            if outcome is not None and getattr(outcome, "reason", "") in {
                "no_relevant_candidate", "repeat_window", "below_relevance_threshold"
            }:
                return "هنوز استیکر مناسبی برای این حال‌وهوا یاد نگرفتم 😅"
            if direct_request:
                return "فعلاً نتونستم استیکر بفرستم 😅"
            return cleaned or "😄"
        return cleaned
