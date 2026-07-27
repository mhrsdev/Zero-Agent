"""Low-cost, rule-based Telegram reactions for MTProto agents.

Reaction policy and reaction-summary parsing are deliberately pure helpers.  They
never call an LLM and do not retain the identities of people who reacted.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, replace
from typing import Any, Iterable

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
    chance_percent: int
    random_value: float = 0.0


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


def _low(text: str) -> str:
    return (text or "").casefold()


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


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


def should_react(message: IncomingMessage, context: ReactionContext) -> ReactionDecision:
    """Make a conservative, deterministic policy decision without network/LLM calls."""
    if not context.enabled:
        return _skip("disabled")
    if message.sender_id == context.self_id:
        return _skip("self_message")
    if message.sender_is_bot:
        return _skip("bot_sender")
    text = _low(message.text)
    if not text.strip():
        return _skip("ambiguous")
    if explicit_reaction_request(text):
        target_text = message.reply_text or message.text
        target = replace(message, text=target_text, reply_text='')
        emoji = choose_reaction(target, context)
        if emoji is None:
            return _skip("no_contextual_signal")
        reason = "explicit_reaction_contextual"
        return ReactionDecision(True, emoji, reason, None, 1.0, False)
    if _has_any(text, _TECHNICAL_TERMS) and (not _has_any(text, _BUG_TERMS) or _has_any(text, ("traceback", "python", "debug", "code", "api", "server"))):
        return _skip("technical_or_serious")
    if _has_any(text, _DISTRESS_TERMS):
        return _skip("distress_or_crisis")
    if _has_any(text, _CONFLICT_TERMS):
        return _skip("conflict_or_abuse")
    if _has_any(text, _SENSITIVE_TERMS):
        return _skip("sensitive_topic")
    emoji = choose_reaction(message, context)
    if emoji is None:
        return _skip("ambiguous")
    if context.chance_percent <= 0 or context.random_value >= context.chance_percent / 100:
        return _skip("chance_not_met")
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

    async def maybe_react(self, event: Any, message: IncomingMessage) -> ReactionDecision:
        status = await self.status()
        trace = message.trace_id or "-"
        message_id = int(getattr(event, "id", 0) or 0)
        explicit_request = explicit_reaction_request(message.text)
        if explicit_request and message.reply_to_message_id:
            message_id = int(message.reply_to_message_id)
        chat_id = int(getattr(event, "chat_id", message.chat_id) or message.chat_id)
        logger.info("REACTION_ENABLED_CHECK trace_id=%s message_id=%s enabled=%s chance=%s limit=%s read_enabled=%s", trace, message_id, status["enabled"], status["chance_percent"], status["hourly_limit"], status["read_enabled"])
        context = ReactionContext(
            owner_id=self.config.owner_user_id,
            self_id=self.self_id,
            enabled=bool(status["enabled"]),
            chance_percent=int(status["chance_percent"]),
            random_value=random.random(),
        )
        if (self.social_awareness
                and await self.social_awareness.enabled('social_awareness_enabled', True)
                and await self.social_awareness.enabled('reaction_awareness_enabled', True)):
            social = await self.social_awareness.decide(message)
            if not social.should_react and not explicit_request and social.reason not in {'not_addressed', 'reaction_preferred'}:
                skipped = _skip(f'social_{social.reason}')
                logger.info('SOCIAL_SKIP trace_id=%s reason=%s confidence=%.2f chosen_action=reaction', trace, social.reason, social.confidence)
                return skipped
        decision = should_react(message, context)
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
