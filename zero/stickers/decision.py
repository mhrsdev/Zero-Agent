from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

CANONICAL_MOODS = frozenset({
    "funny", "sad", "love", "angry", "greeting", "react",
    "cool", "shock", "surprise", "thinking", "approve", "disapprove",
    "fire", "celebrate", "pray", "smirk", "dead",
})

MOOD_ALIASES = {
    "reaction": "react", "reactions": "react", "face": "react",
    "expression": "react", "emoji": "react", "default": "react",
    "happy": "funny", "laugh": "funny", "meme": "funny",
    "cute": "love", "heart": "love", "party": "celebrate",
    "wow": "shock", "surprised": "surprise", "thoughtful": "thinking",
    "yes": "approve", "ok": "approve", "no": "disapprove",
    "mad": "angry", "hello": "greeting", "bye": "greeting",
}


def normalize_mood(value: str | None, *, default: str | None = None) -> str | None:
    cleaned = (value or "").strip().casefold().replace("-", "_")
    if not cleaned:
        return default
    canonical = MOOD_ALIASES.get(cleaned, cleaned)
    return canonical if canonical in CANONICAL_MOODS else default


def canonicalize_mood_tags(values: str | Iterable[str] | None) -> tuple[str, ...]:
    """Normalize aliases and return stable, duplicate-free mood tags."""
    raw = values.split(",") if isinstance(values, str) else (values or ())
    result: list[str] = []
    for value in raw:
        mood = normalize_mood(value)
        if mood and mood not in result:
            result.append(mood)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class StickerIntent:
    mood: str
    direct_request: bool = False
    retry_request: bool = False
    allow_generic_fallback: bool = False


@dataclass(frozen=True, slots=True)
class StickerSendOutcome:
    reason: str
    mood: str
    direct_request: bool = False
    candidate_id: int | None = None
    relevance_score: float = 0.0
    fallback_level: str = "none"
    transport: str = "not_attempted"
    candidate_count: int = 0
    send_probability: float = 1.0
    random_sample: float | None = None
    confidence_threshold: float = 0.0

    @property
    def sent(self) -> bool:
        return self.reason == "sent" and self.transport == "sent"

    @property
    def intent_detected(self) -> bool:
        return bool(self.mood)

    @property
    def intent_label(self) -> str:
        return self.mood

    @property
    def selected_sticker_id(self) -> int | None:
        return self.candidate_id

    @property
    def no_send_reason(self) -> str | None:
        return None if self.sent else self.reason

    @property
    def send_gate_result(self) -> str:
        return "passed" if self.reason in {"sent", "transport_failed", "transport_exception"} else "blocked"

    @property
    def transport_result(self) -> str:
        return self.transport

    def as_dict(self) -> dict[str, object]:
        return {
            "intent_detected": self.intent_detected,
            "intent_label": self.intent_label,
            "send_gate_result": self.send_gate_result,
            "send_probability": self.send_probability,
            "random_sample": self.random_sample,
            "candidate_count": self.candidate_count,
            "selected_sticker_id": self.selected_sticker_id,
            "relevance_score": self.relevance_score,
            "confidence_threshold": self.confidence_threshold,
            "no_send_reason": self.no_send_reason,
            "transport_result": self.transport_result,
            "fallback_level": self.fallback_level,
        }
