from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


MODES = {"normal", "funny", "sarcastic", "serious", "assistant", "teacher", "debate"}


@dataclass(slots=True)
class IncomingMessage:
    chat_id: int
    chat_title: str
    sender_id: int
    sender_label: str
    text: str
    reply_to_zero: bool = False
    mention_zero: bool = False
    sender_is_bot: bool = False
    reply_text: str = ""
    reply_sender_id: int | None = None
    reply_sender_label: str = ""
    reply_sender_is_bot: bool = False
    trace_id: str = ""
    message_id: int = 0
    media_type: str = ''
    media_caption: str = ''
    reply_to_message_id: int | None = None
    thread_id: int | None = None
    sender_username: str = ''
    sender_display_name: str = ''
    platform: str = 'telegram'
    account_scope: str = ''
    is_forwarded: bool = False
    is_service_message: bool = False
    resolved_target_user_id: int | None = None
    resolved_target_kind: str = ''
    resolved_mention_user_ids: tuple[int, ...] = ()


@dataclass(slots=True)
class Decision:
    should_reply: bool
    reason: str
    interject: bool = False


@dataclass(slots=True)
class RouteResult:
    text: str
    provider: str
    model: str
    attempts: int
    estimated_cost_usd: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class UserProfile:
    sender_id: int
    label: str
    nicknames: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    style_notes: list[str] = field(default_factory=list)
    reputation: int = 0
