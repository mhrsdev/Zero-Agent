from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Immutable identity carried across one Zero request.

    The context is transport-neutral, but retains enough account and group
    scope to prevent stateful services from silently falling back to globals.
    """

    installation_id: str
    account_scope: str
    platform: str
    telegram_chat_id: int | None
    internal_group_id: str
    sender_id: int | None
    thread_id: int | None = None
    message_id: int | None = None
    reply_to_message_id: int | None = None
    request_id: str = ""
    trace_id: str = ""
    transport_mode: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        for field_name in ("installation_id", "account_scope", "platform", "internal_group_id", "request_id", "trace_id"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")

    @property
    def scope_key(self) -> tuple[str, str, str, str, int | None, int | None]:
        """Stable key for installation/account/platform/group/user/thread state."""
        return (
            self.installation_id,
            self.account_scope,
            self.platform,
            self.internal_group_id,
            self.sender_id,
            self.thread_id,
        )
