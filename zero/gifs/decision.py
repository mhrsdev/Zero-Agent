from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class GifSendOutcome:
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
        return self.reason == "sent"

    @property
    def intent_detected(self) -> bool:
        return bool(self.mood)

    @property
    def intent_label(self) -> str:
        return self.mood

    @property
    def send_gate_result(self) -> str:
        return "passed" if self.sent else self.reason

    @property
    def selected_gif_id(self) -> int | None:
        return self.candidate_id

    @property
    def no_send_reason(self) -> str | None:
        return None if self.sent else self.reason

    @property
    def transport_result(self) -> str:
        return self.transport

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload.update({
            "intent_detected": self.intent_detected,
            "intent_label": self.intent_label,
            "send_gate_result": self.send_gate_result,
            "selected_gif_id": self.selected_gif_id,
            "no_send_reason": self.no_send_reason,
            "transport_result": self.transport_result,
        })
        return payload
