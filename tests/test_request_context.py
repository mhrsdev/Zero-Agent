from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zero.core.context import RequestContext
from zero.models import IncomingMessage


def test_request_context_requires_installation_and_group_identity() -> None:
    with pytest.raises(ValueError):
        RequestContext(
            installation_id="",
            account_scope="user-session",
            platform="telegram",
            telegram_chat_id=-100,
            internal_group_id="group-1",
            sender_id=7,
            request_id="req-1",
            trace_id="trace-1",
        )


def test_request_context_has_stable_scope_key_and_aware_timestamp() -> None:
    context = RequestContext(
        installation_id="install-1",
        account_scope="user-session",
        platform="telegram",
        telegram_chat_id=-100,
        internal_group_id="group-1",
        sender_id=7,
        thread_id=42,
        request_id="req-1",
        trace_id="trace-1",
    )

    assert context.scope_key == ("install-1", "user-session", "telegram", "group-1", 7, 42)
    assert context.timestamp.tzinfo is not None
    assert context.timestamp.utcoffset() == timezone.utc.utcoffset(context.timestamp)


def test_incoming_message_can_carry_canonical_context() -> None:
    context = RequestContext(
        installation_id="install-1",
        account_scope="user-session",
        platform="telegram",
        telegram_chat_id=-100,
        internal_group_id="group-1",
        sender_id=7,
        request_id="req-1",
        trace_id="trace-1",
    )
    message = IncomingMessage(
        chat_id=-100,
        chat_title="test",
        sender_id=7,
        sender_label="user",
        text="hello",
        context=context,
    )

    assert message.context is context
    assert message.context.scope_key[:4] == ("install-1", "user-session", "telegram", "group-1")
    assert message.context.scope_key[4:] == (7, None)


def test_listener_constructs_request_context_at_adapter_boundary() -> None:
    source = Path("scripts/run_listener.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "RequestContext" in names
