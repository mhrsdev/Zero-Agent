from __future__ import annotations

import logging

logger = logging.getLogger('zero.identity')


def canonical_user_key(chat_id: int, sender_id: int, thread_id: int | None = None) -> str:
    """Return the only internal user identity key; display metadata is never used."""
    chat = int(chat_id)
    sender = int(sender_id)
    if thread_id is None:
        return f'chat:{chat}:user:{sender}'
    return f'chat:{chat}:thread:{int(thread_id)}:user:{sender}'


def log_identity_resolved(chat_id: int, sender_id: int, thread_id: int | None = None, *, trace_id: str = '-') -> str:
    key = canonical_user_key(chat_id, sender_id, thread_id)
    logger.info('IDENTITY_RESOLVED chat_id=%s sender_id=%s thread_id=%s trace_id=%s', int(chat_id), int(sender_id), thread_id if thread_id is not None else '-', trace_id)
    return key
