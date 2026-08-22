"""Low-cost, rule-based Telegram reactions for MTProto agents.

Reaction policy and reaction-summary parsing are deliberately pure helpers.  They
never call an LLM and do not retain the identities of people who reacted.
"""
from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, replace
from typing import Any, Iterable

from .automation import automation_disabled
from .config import ReactionsConfig, ZeroConfig
from .models import IncomingMessage
from .storage import ZeroStore

logger = logging.getLogger(__name__)

# Keep technical/safety exclusions ahead of any positive/funny signal.
_TECHNICAL_TERMS = (
    "ارور", "خطا", "error", "exception", "traceback", "debug", "باگ", "bug",
    "پایتون", "python", "کد", "code", "سرور", "server", "docker", "امنیت",
    "security", "database", "sql", "api", "deploy", "linux", "ssh",
)
_DISTRESS_TERMS = (
    "مرگ", "فوت", "مرده", "بیماری", "سرطان", "غم", "ناراحت", "افسرده",
    "خودکشی", "بحران", "عزاداری", "تسلیت", "حادثه", "مجروح",
)
_CONFLICT_TERMS = (
    "دعوا", "فحش", "توهین", "لعنت", "حرومزاده", "ابله", "احمق", "نفرت",
    "تهدید", "abuse", "fuck", "shit", "idiot",
)
# Complaint/sarcasm markers: thanks phrasing next to these reads as blame,
# not gratitude ("ممنون که هیچkdumتون کمک کردین"). Matched as word PREFIXES
# because they appear with suffixes ("هیچkdumتون").
_NEG_CONTEXT_STEMS = ("هیچکدوم", "هیچکی", "هیچکسم")
_NEG_CONTEXT_TERMS = ("بیخیال", "ولش کن", "افتضاح", "بدترین")


def _has_negative_context(text: str) -> bool:
    if _has_any(text, _NEG_CONTEXT_TERMS):
        return True
    return any(token.startswith(stem)
               for token in _TOKEN_RE.findall(text)
               for stem in _NEG_CONTEXT_STEMS)
_SENSITIVE_TERMS = (
    "سیاست", "سیاسی", "انتخابات", "جنگ", "تحریم", "رئیس جمهور", "مذهب",
    "دین", "قومیت", "نژاد", "شیعه", "سنی", "یهودی", "اسرائیل", "فلسطین",
)
_FUNNY_TERMS = ("خنده", "خنده‌دار", "جوک", "باحال", "😂", "🤣", "lol", "خخ", "هاها")
_APPROVE_TERMS = ("دمت گرم", "عالی", "درسته", "درست گفتی", "احسنت", "دمتگرم", "آفرین")
_BUG_TERMS = ("باگ", "bug", "خطا", "ارور", "exception")
_CRINGE_TERMS = ("کرینج", "عجیب", "چی بود", "وات", "👀", "🫠")
_POSITIVE_EMOJIS = frozenset({"👍", "❤️", "❤", "🔥", "👏", "🎉", "😍", "🥰"})
_FUNNY_EMOJIS = frozenset({"😂", "🤣", "😄", "😁", "😆", "😹"})
_NEGATIVE_EMOJIS = frozenset({"👎", "😡", "🤮", "💩", "😢", "😞", "😠"})
_REACTION_REQUEST_TERMS = ("ری‌اکشن", "ری اکشن", "واکنش", "reaction", "react")


def explicit_reaction_request(text: str) -> bool:
    low = _low(text)
    return any(term in low for term in _REACTION_REQUEST_TERMS) and any(term in low for term in ("بزن", "بذار", "بده", "بفرست", "زن", "کن", "send", "add"))


@dataclass(frozen=True, slots=True)
class ReactionContext:
    owner_id: int
    self_id: int
    enabled: bool
    chance_percent: int  # deprecated: no longer gates the react/silence decision
    random_value: float = 0.0
    allow_with_reply: bool = False


@dataclass(frozen=True, slots=True)
class ReactionDecision:
    """Auditable result of a no-LLM policy decision."""

    should_react: bool
    emoji: str | None
    reason: str
    skipped_reason: str | None
    confidence: float
    rate_limited: bool

    @property
    def allowed(self) -> bool:
        """Compatibility alias for existing callers/tests."""
        return self.should_react


def parse_reaction_command(parts: list[str]) -> tuple[str, int | None]:
    """Validate panel subcommands before they mutate runtime DB settings."""
    if not parts:
        return "status", None
    action = parts[0].casefold()
    if action in {"status", "on", "off", "stats"} and len(parts) == 1:
        return action, None
    if action == "read" and len(parts) == 2 and parts[1].casefold() in {"on", "off"}:
        return f"read_{parts[1].casefold()}", None
    ranges = {"chance": (0, 100), "limit": (1, 10), "cooldown": (1, 86400)}
    if action in ranges and len(parts) == 2:
        try:
            value = int(parts[1])
        except ValueError as exc:
            raise ValueError(f"{action} باید عدد باشد.") from exc
        minimum, maximum = ranges[action]
        if not minimum <= value <= maximum:
            raise ValueError(f"{action} باید بین {minimum} و {maximum} باشد.")
        return action, value
    raise ValueError("Usage: /zero reactions [status|on|off|chance <0-100>|limit <1-10>|cooldown <seconds>|read on|read off|stats]")


_JOINERS = {"\u200c"}  # ZWNJ joins parts of one Persian word
_RUNS_RE = re.compile(r"(.)\1{2,}")  # informal elongation: "گرمممم"


def _low(text: str) -> str:
    # Strip Arabic tatweel so decorated words ("تبـریک") still match.
    return (text or "").casefold().replace("\u0640", "")


def _text_variants(text: str):
    """Yield the text plus a de-elongated form ("دمت گرمممم" -> "دمت گرم")."""
    yield text
    yield _RUNS_RE.sub(r"\1", text)


def _edit_distance_le1(a: str, b: str) -> bool:
    """True when a and b differ by at most one insert/delete/substitute."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = edits = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(a) == len(b):
            i += 1
        j += 1
    return True


_TOKEN_RE = re.compile(r"[\w\u200c]+")


def _has_positive_fuzzy(text: str, terms: tuple[str, ...]) -> bool:
    """One-edit fuzzy match for POSITIVE terms only (len>=4).

    Catches single-character typos such as "ممننون" ~ "ممنون". Safety lists
    never use this: a missed distress term is acceptable, a false hit is not.
    """
    for token in _TOKEN_RE.findall(text):
        if len(token) < 4:
            continue
        for term in terms:
            if len(term) >= 4 and abs(len(term) - len(token)) <= 1 \
                    and _edit_distance_le1(token, term):
                return True
    return False


def _has_term(text: str, term: str) -> bool:
    """Boundary-aware containment: a term must not sit inside a larger word.

    Prevents false hits like "وای" inside "هوای". ZWNJ counts as a joiner,
    so compound terms such as "خنده‌دار" still match.
    """
    start = 0
    while True:
        idx = text.find(term, start)
        if idx < 0:
            return False
        end = idx + len(term)
        prev_ch = text[idx - 1] if idx > 0 else ""
        next_ch = text[end] if end < len(text) else ""
        if (not prev_ch or prev_ch in _JOINERS or not prev_ch.isalnum()) and \
           (not next_ch or next_ch in _JOINERS or not next_ch.isalnum()):
            return True
        start = idx + 1


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(_has_term(variant, term) for variant in _text_variants(text)
               for term in terms)


_JOKE_REQUEST_VERBS = ("بگو", "بده", "بکو", "بگید", "کن", "send", "tell")


def _is_joke_request(text: str) -> bool:
    """A request FOR a joke ("جوک بگو") is not funny content to react to."""
    return _has_any(text, ("جوک", "joke")) and _has_any(text, _JOKE_REQUEST_VERBS)


def _skip(reason: str, *, rate_limited: bool = False) -> ReactionDecision:
    return ReactionDecision(False, None, "skip", reason, 0.0, rate_limited)


def choose_reaction(message: IncomingMessage, context: ReactionContext) -> str | None:
    """Select a contextual emoji with bounded variety; never calls the LLM."""
    text = _low(message.text)
    r = max(0.0, min(0.9999, float(context.random_value)))
    if message.media_type == 'image':
        return ('❤️', '😍', '👀', '🥰', '🔥')[int(r * 5)]
    if _has_any(text, _BUG_TERMS) and not _has_any(text, ("traceback", "python", "debug", "code", "api", "server")):
        return ('🤦', '😅', '🫠')[int(r * 3)]
    if _has_any(text, _FUNNY_TERMS):
        return ('🤣', '😂', '😹', '😆')[int(r * 4)]
    if _has_any(text, _APPROVE_TERMS) or _has_any(text, ("دقیقاً", "موافقم", "همینه", "صحیح")):
        return ('👍', '👏', '🔥', '💯')[int(r * 4)]
    if _has_any(text, ("مرسی", "ممنون", "دوستت دارم", "عاشق", "❤️", "❤")):
        return ('❤️', '🥰', '😍', '🤍')[int(r * 4)]
    if _has_any(text, ("تبریک", "مبارک", "موفق شد", "بردیم", "جشن")):
        return ('🎉', '🥳', '🙌', '🔥')[int(r * 4)]
    if _has_any(text, _CRINGE_TERMS):
        return ('🫠', '👀', '🤦', '😬')[int(r * 4)]
    if ('?' in text or '؟' in text) and not _has_any(text, _TECHNICAL_TERMS):
        return '👀' if r < 0.4 else '🤔'
    if _has_any(text, ("جدی؟", "وای", "باورم نمیشه", "عجب", "واقعاً")):
        return '😳' if r < 0.5 else '🤯'
    if _has_any(text, ("خسته", "سخته", "موفق باشی", "دمت گرم")):
        return ('💪', '🤝', '🙏', '✨')[int(r * 4)]
    return None


def choose_with_reply_emoji(message: IncomingMessage, context: ReactionContext) -> str | None:
    """Emoji for the controlled react+reply mode: short emotional approvals only.

    Never funny/cringe/question/surprise faces -- those next to a reply read as
    noise or mockery. Bounded variety applies to the emoji choice only; whether
    to act at all was already decided deterministically by ``should_react``.
    """
    text = _low(message.text)
    r = max(0.0, min(0.9999, float(context.random_value)))
    if _has_any(text, ("تبریک", "مبارک", "موفق شد", "بردیم", "جشن")):
        return ('🎉', '🥳', '🙌')[int(r * 3)]
    if _has_any(text, _APPROVE_TERMS) or _has_any(text, ("دقیقاً", "موافقم", "همینه", "صحیح")):
        return ('👍', '👏', '🔥')[int(r * 3)]
    if _has_any(text, ("مرسی", "ممنون", "دوستت دارم", "❤️", "❤")):
        return ('❤️', '🥰', '🤍')[int(r * 3)]
    return None


def should_react(
    message: IncomingMessage,
    context: ReactionContext,
    *,
    reply_pending: bool = False,
) -> ReactionDecision:
    """Make a conservative, deterministic policy decision without network/LLM calls.

    ``reply_pending`` marks messages the bot is about to answer; it enables the
    opt-in react+reply mode (short emotional approvals only).
    """
    if not context.enabled:
        return _skip("disabled")
    if message.sender_id == context.self_id:
        return _skip("self_message")
    if message.sender_is_bot:
        return _skip("bot_sender")
    text = _low(message.text)
    if not text.strip():
        # A bare image with no caption and no reply target carries too little
        # evidence for a confident, useful reaction: stay silent.
        if message.media_type == "image" and not (message.media_caption or "").strip() \
                and not (message.reply_text or "").strip():
            return _skip("insufficient_media_evidence")
        # Caption text is real signal: run the normal text pipeline on it.
        if message.media_type == "image" and (message.media_caption or "").strip():
            text = _low(message.media_caption)
        else:
            return _skip("ambiguous")
    if _is_joke_request(text):
        return _skip("content_request")
    if _has_negative_context(text):
        return _skip("negative_context")
    # Any technical/serious topic stays silent: a facepalm on someone's bug
    # report reads as mockery, and the reply pipeline handles real requests.
    if _has_any(text, _TECHNICAL_TERMS):
        return _skip("technical_or_serious")
    if _has_any(text, _DISTRESS_TERMS):
        return _skip("distress_or_crisis")
    if _has_any(text, _CONFLICT_TERMS):
        return _skip("conflict_or_abuse")
    if _has_any(text, _SENSITIVE_TERMS):
        return _skip("sensitive_topic")
    if explicit_reaction_request(text):
        # Explicit user command outranks the contextual heuristics but never
        # the safety checks above (no celebratory emoji on a condolence).
        target_text = message.reply_text or message.text
        target = replace(message, text=target_text, reply_text='')
        emoji = choose_reaction(target, context)
        if emoji is None:
            return _skip("no_contextual_signal")
        return ReactionDecision(True, emoji, "explicit_reaction_contextual", None, 1.0, False)
    addressed = message.reply_to_zero or message.mention_zero
    if reply_pending and addressed:
        # Opt-in react+reply mode: short emotional approvals only, never
        # funny/cringe faces next to a reply. Rate limits and duplicate guards
        # are enforced by ReactionService exactly as for standalone reactions.
        if not context.allow_with_reply:
            return _skip("with_reply_disabled")
        emoji = choose_with_reply_emoji(message, context)
        if emoji is None:
            return _skip("addressed_reply_expected")
        return ReactionDecision(True, emoji, "with_reply_approval", None, 0.7, False)
    if addressed:
        # The bot is being addressed; the reply itself is the action and an
        # extra emoji would only add noise.
        return _skip("addressed_reply_expected")
    if _has_positive_fuzzy(
        text, _APPROVE_TERMS + ("ممنون", "مرسی", "تبریک", "مبارک"),
    ) and choose_reaction(message, context) is None:
        # Typo'd praise with no direct rule hit: acknowledge conservatively.
        return ReactionDecision(True, "👍", "approval_typo", None, 0.7, False)
    emoji = choose_reaction(message, context)
    if emoji is None:
        return _skip("ambiguous")
    reason = "funny" if emoji in {"😂", "🤣"} else "approval" if emoji == "👍" else "cringe_or_surprise"
    return ReactionDecision(True, emoji, reason, None, 0.92, False)


def _emoji_from_reaction(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    emoji = getattr(value, "emoticon", None)
    if isinstance(emoji, str):
        return emoji
    reaction = getattr(value, "reaction", None)
    if reaction is not None:
        return _emoji_from_reaction(reaction)
    if isinstance(value, dict):
        for key in ("emoticon", "reaction", "emoji"):
            if key in value:
                return _emoji_from_reaction(value[key])
    return None


def summarize_reactions(reactions: Any) -> dict[str, Any]:
    """Return aggregate reaction signals without collecting reactor identities."""
    rows: Iterable[Any]
    if reactions is None:
        rows = ()
    elif isinstance(reactions, dict):
        rows = reactions.get("results", reactions.get("reactions", ()))
    else:
        rows = getattr(reactions, "results", reactions)
    counts: dict[str, int] = {}
    for row in rows or ():
        emoji = _emoji_from_reaction(row)
        count = row.get("count", 1) if isinstance(row, dict) else getattr(row, "count", 1)
        if emoji:
            try:
                counts[emoji] = counts.get(emoji, 0) + max(0, int(count))
            except (TypeError, ValueError):
                continue
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    score = lambda choices: sum(count for emoji, count in counts.items() if emoji in choices)
    return {
        "total_reactions": sum(counts.values()),
        "top_emojis": [emoji for emoji, _ in ordered[:5]],
        "positive_score": score(_POSITIVE_EMOJIS),
        "funny_score": score(_FUNNY_EMOJIS),
        "negative_score": score(_NEGATIVE_EMOJIS),
    }


class ReactionService:
    """Stateful sender/reader: DB settings, rate limits, and MTProto requests."""

    def __init__(self, config: ZeroConfig, store: ZeroStore, client: Any, self_id: int, social_awareness: Any | None = None):
        self.config = config
        self.store = store
        self.client = client
        self.self_id = self_id
        self.social_awareness = social_awareness

    async def _setting_bool(self, key: str, default: bool) -> bool:
        value = await self.store.get_setting(key)
        if value is None or value in ("", "null", "None"):
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    async def _setting_int(self, key: str, default: int, minimum: int, maximum: int) -> int:
        value = await self.store.get_setting(key)
        try:
            return max(minimum, min(maximum, int(value))) if value is not None else default
        except (TypeError, ValueError):
            return default

    async def status(self) -> dict[str, int | bool]:
        cfg: ReactionsConfig = self.config.reactions
        return {
            "enabled": await self._setting_bool("reactions_enabled", cfg.enabled),
            "chance_percent": await self._setting_int("reactions_chance", cfg.chance_percent, 0, 100),
            "hourly_limit": await self._setting_int("reactions_limit", cfg.max_per_hour, 1, 10),
            "user_cooldown_seconds": await self._setting_int("reactions_cooldown_seconds", cfg.user_cooldown_seconds, 1, 86400),
            "global_cooldown_seconds": await self._setting_int("reactions_global_cooldown_seconds", cfg.global_cooldown_seconds, 60, 120),
            "read_enabled": await self._setting_bool("reactions_read_enabled", cfg.read_enabled),
        }

    async def stats(self) -> dict[str, int | bool]:
        status = await self.status()
        return {
            **status,
            "sent_last_hour": await self.store.count_rate_events(0, "reaction_sent", 3600),
            "rate_limited_last_hour": await self.store.count_rate_events(0, "reaction_rate_limited", 3600),
        }

    async def maybe_react(
        self, event: Any, message: IncomingMessage, *, reply_pending: bool = False,
    ) -> ReactionDecision:
        status = await self.status()
        trace = message.trace_id or "-"
        kill = await automation_disabled(self.store)
        if kill:
            logger.info("AUTOMATION_KILLED trace_id=%s component=reaction reason=%s", trace, kill)
            return _skip("kill_switch")
        message_id = int(getattr(event, "id", 0) or 0)
        explicit_request = explicit_reaction_request(message.text)
        if explicit_request and message.reply_to_message_id:
            message_id = int(message.reply_to_message_id)
        chat_id = int(getattr(event, "chat_id", message.chat_id) or message.chat_id)
        logger.info("REACTION_ENABLED_CHECK trace_id=%s message_id=%s enabled=%s chance=%s limit=%s read_enabled=%s", trace, message_id, status["enabled"], status["chance_percent"], status["hourly_limit"], status["read_enabled"])
        allow_with_reply = await self._setting_bool("reactions_with_reply_enabled", False)
        context = ReactionContext(
            owner_id=self.config.owner_user_id,
            self_id=self.self_id,
            enabled=bool(status["enabled"]),
            chance_percent=int(status["chance_percent"]),
            random_value=random.random(),
            allow_with_reply=allow_with_reply,
        )
        if (self.social_awareness
                and await self.social_awareness.enabled('social_awareness_enabled', True)
                and await self.social_awareness.enabled('reaction_awareness_enabled', True)):
            social = await self.social_awareness.decide(message)
            if not social.should_react and not explicit_request and social.reason not in {'not_addressed', 'reaction_preferred'}:
                skipped = _skip(f'social_{social.reason}')
                logger.info('SOCIAL_SKIP trace_id=%s reason=%s confidence=%.2f chosen_action=reaction', trace, social.reason, social.confidence)
                return skipped
        decision = should_react(message, context, reply_pending=reply_pending)
        logger.info("REACTION_DECISION trace_id=%s message_id=%s sender_id=%s should_react=%s emoji=%s reason=%s skipped_reason=%s confidence=%.2f", trace, message_id, message.sender_id, decision.should_react, decision.emoji, decision.reason, decision.skipped_reason, decision.confidence)
        if not decision.should_react:
            logger.info("REACTION_SKIPPED trace_id=%s message_id=%s sender_id=%s skipped_reason=%s rate_limited=false", trace, message_id, message.sender_id, decision.skipped_reason)
            return decision

        duplicate_kind = f"reaction_message:{chat_id}:{message_id}"
        hourly = await self.store.count_rate_events(0, "reaction_sent", 3600)
        per_user = await self.store.count_rate_events(message.sender_id, "reaction_user", int(status["user_cooldown_seconds"]))
        global_recent = await self.store.count_rate_events(0, "reaction_global", int(status["global_cooldown_seconds"]))
        duplicate = await self.store.count_rate_events(0, duplicate_kind, 86400 * 30)
        limited_reason = ""
        if duplicate:
            limited_reason = "duplicate_message"
        elif hourly >= int(status["hourly_limit"]):
            limited_reason = "hourly_rate_limit"
        elif per_user:
            limited_reason = "user_cooldown"
        elif global_recent:
            limited_reason = "global_cooldown"
        if limited_reason:
            limited = _skip(limited_reason, rate_limited=True)
            await self.store.add_rate_event(0, "reaction_rate_limited")
            logger.info("REACTION_RATE_LIMITED trace_id=%s message_id=%s sender_id=%s skipped_reason=%s", trace, message_id, message.sender_id, limited_reason)
            logger.info("REACTION_SKIPPED trace_id=%s message_id=%s sender_id=%s skipped_reason=%s rate_limited=true", trace, message_id, message.sender_id, limited_reason)
            return limited

        try:
            from telethon import functions, types
            peer = await event.get_input_chat()
            await self.client(functions.messages.SendReactionRequest(
                peer=peer, msg_id=message_id,
                reaction=[types.ReactionEmoji(emoticon=str(decision.emoji))],
                big=False, add_to_recent=False,
            ))
            await self.store.add_rate_event(0, "reaction_sent")
            await self.store.add_rate_event(0, "reaction_global")
            await self.store.add_rate_event(message.sender_id, "reaction_user")
            await self.store.add_rate_event(0, duplicate_kind)
            logger.info("REACTION_SENT trace_id=%s message_id=%s sender_id=%s emoji=%s reason=%s", trace, message_id, message.sender_id, decision.emoji, decision.reason)
            logger.info("SMART_REACTION_SENT trace_id=%s message_id=%s emoji=%s reason=%s", trace, message_id, decision.emoji, decision.reason)
            return decision
        except Exception as exc:
            logger.warning("REACTION_FAILED trace_id=%s message_id=%s sender_id=%s emoji=%s exception_type=%s exception_message=%s", trace, message_id, message.sender_id, decision.emoji, type(exc).__name__, str(exc)[:160])
            return ReactionDecision(False, None, "send_failed", "send_failed", 0.0, False)

    async def read_reactions(self, *, trace_id: str, message_id: int, reactions: Any) -> dict[str, Any] | None:
        """Log a privacy-preserving aggregate and update a matching Zero sticker score."""
        status = await self.status()
        if not status["read_enabled"]:
            return None
        summary = summarize_reactions(reactions)
        top = ",".join(summary["top_emojis"]) or "-"
        logger.info("REACTION_READ trace_id=%s message_id=%s total_reactions=%s top_emojis=%s positive_score=%s funny_score=%s negative_score=%s", trace_id or "-", message_id, summary["total_reactions"], top, summary["positive_score"], summary["funny_score"], summary["negative_score"])
        if summary["total_reactions"]:
            delta = int(summary["positive_score"]) + int(summary["funny_score"]) - int(summary["negative_score"])
            if delta:
                await self.store.adjust_sticker_reaction_score_by_message(message_id, delta)
        return summary
