from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchConversationEntry:
    chat_id: int
    sender_id: int
    original_user_query: str
    rewritten_query: str
    subject: str
    domain: str
    created_at: float
    trace_id: str
    search_session_id: str
    message_id: int = 0
    thread_id: int | None = None


class SearchConversationState:
    """Short-lived Web-only search state; it never reads or writes memory storage."""

    def __init__(self, ttl_seconds: int = 300, clock=time.time):
        self.ttl_seconds = max(120, min(600, int(ttl_seconds)))
        self._clock = clock
        self._entries: dict[tuple[int, int, int | None, int], SearchConversationEntry] = {}

    def record(
        self,
        chat_id: int,
        sender_id: int,
        original_user_query: str,
        rewritten_query: str,
        subject: str,
        domain: str,
        trace_id: str,
        *,
        message_id: int = 0,
        thread_id: int | None = None,
        search_session_id: str = '',
        created_at: float | None = None,
    ) -> SearchConversationEntry:
        created = self._clock() if created_at is None else float(created_at)
        session = search_session_id or f'{chat_id}:{sender_id}:{thread_id or "root"}'
        entry = SearchConversationEntry(
            chat_id=int(chat_id), sender_id=int(sender_id),
            original_user_query=str(original_user_query), rewritten_query=str(rewritten_query),
            subject=str(subject), domain=str(domain), created_at=created,
            trace_id=str(trace_id), search_session_id=session,
            message_id=int(message_id or 0), thread_id=thread_id,
        )
        self._prune(created)
        self._entries[(entry.chat_id, entry.sender_id, entry.thread_id, entry.message_id)] = entry
        return entry

    def lookup(
        self,
        chat_id: int,
        sender_id: int,
        *,
        thread_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> SearchConversationEntry | None:
        now = self._clock()
        self._prune(now)
        if reply_to_message_id is not None:
            for entry in self._entries.values():
                if entry.chat_id == chat_id and entry.sender_id == sender_id and entry.message_id == int(reply_to_message_id):
                    return entry
        candidates = [
            entry for entry in self._entries.values()
            if entry.chat_id == int(chat_id)
            and entry.sender_id == int(sender_id)
            and entry.thread_id == thread_id
        ]
        return max(candidates, key=lambda entry: entry.created_at, default=None)

    def _prune(self, now: float) -> None:
        cutoff = now - self.ttl_seconds
        for key, entry in list(self._entries.items()):
            if entry.created_at < cutoff:
                self._entries.pop(key, None)
