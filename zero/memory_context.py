from __future__ import annotations

import json
import logging
import re
from typing import Any

from .models import IncomingMessage

logger = logging.getLogger('zero.memory_context')


_TERMS = re.compile(r"[\wآ-ی‌]{3,}")
_RAG_CUES = ("یادت", "قبلاً", "قبلا", "همون", "اون موقع", "در حافظه", "اسم من", "چی گفتم")


def _line(row: dict[str, Any], text_cap: int = 420) -> str:
    text = " ".join(str(row.get("text") or "").split())[:text_cap]
    # `reply_to_message_id=none` is omitted rather than spelled out: absence
    # already means "not a reply", and the field is ~26 characters that were
    # re-sent for every non-reply message in two blocks of every prompt.
    reply_to = row.get("reply_to_message_id")
    reply_field = f"reply_to_message_id={reply_to} " if reply_to else ""
    return (
        f"telegram_message_id={row.get('telegram_message_id') or 'legacy:' + str(row.get('id', 'unknown'))} "
        f"{reply_field}"
        f"sender_id={row.get('sender_id')} role={row.get('role', '')} "
        f"sender_label={row.get('sender_label', '')!r} text={text!r}"
    )


def _block(tag: str, lines: list[str], cap: int) -> str:
    header, footer = f"[{tag}]", f"[/{tag}]"
    kept: list[str] = []
    used = len(header) + len(footer) + 2
    for line in lines:
        clean = str(line).strip()
        if not clean or used + len(clean) + 1 > cap:
            continue
        kept.append(clean)
        used += len(clean) + 1
    return "\n".join((header, *kept, footer))


def _profile_lines(profile: dict[str, Any] | None, semantic_rows: list[dict], chat_id: int, sender_id: int) -> list[str]:
    lines = [f"owner=chat_id={chat_id},sender_id={sender_id}"]
    if profile:
        lines.append(
            "profile=" + json.dumps({
                "username": profile.get("username", ""),
                "display_name": profile.get("display_name", ""),
                "label": profile.get("label", ""),
                "nicknames": json.loads(profile.get("nicknames_json") or "[]"),
            }, ensure_ascii=False, separators=(",", ":"))
        )
    for row in semantic_rows[:6]:
        lines.append(f"{row['category']}.{row['key']}={row['value']}")
    return lines


async def compose_memory_context(*, store, semantic_memory, message: IncomingMessage, recent: list[dict], layered: dict[str, list[dict]], extra_lines: list[str] | None = None, v3_memory: Any | None = None) -> tuple[str, dict[str, Any]]:
    """Build one bounded identity-safe context for every model path."""
    chat_id, current_id = int(message.chat_id), int(message.sender_id)
    current_profile = await store.get_profile(chat_id, current_id)
    current_semantic = semantic_memory.retrieve(chat_id, current_id, limit=6)
    identity_rows = [row for row in current_semantic if row.get('category') == 'identity']
    user_rows = [row for row in current_semantic if row.get('category') != 'identity']
    sections = [_block("CURRENT_MESSAGE_IDENTITY", _profile_lines(current_profile, identity_rows, chat_id, current_id), 1800)]
    current_lines = _profile_lines(None, user_rows, chat_id, current_id)
    for note in await store.get_user_notes(chat_id, current_id, message.text, limit=4):
        current_lines.append(f"note section={note['section']} source_message_id={note.get('source_message_id') or 'none'} content={note['content']}")
    sections.append(_block("CURRENT_USER_MEMORY", current_lines, 2200))

    chain = []
    if message.message_id and message.account_scope:
        chain = await store.get_reply_chain(message.platform, message.account_scope, chat_id, message.message_id, max_depth=8)
    chain_lines = [_line(row) for row in chain]
    if not chain and message.reply_sender_id:
        chain_lines = [
            f"telegram_message_id={message.reply_to_message_id or 'unknown'} sender_id={message.reply_sender_id} "
            f"role={'assistant' if message.reply_sender_is_bot else 'user'} sender_label={message.reply_sender_label!r} text={message.reply_text[:420]!r}"
        ]
    sections.append(_block("REPLY_CHAIN", chain_lines, 3200))

    thread_participant_ids: list[int] = []
    if v3_memory is not None and message.message_id:
        try:
            thread = await v3_memory.thread_context(message, max_depth=8, sibling_limit=12)
            thread_participant_ids = list(thread.participant_ids)
            thread_lines = [
                f"role=ancestor message_id={row.message_id} reply_to={row.reply_to_message_id or 'none'} sender_id={row.sender_id} sender_label={json.dumps(row.sender_label, ensure_ascii=False)} text={json.dumps(row.text[:420], ensure_ascii=False)}"
                for row in reversed(thread.ancestors)
            ]
            thread_lines.extend(
                f"role=related_reply message_id={row.message_id} reply_to={row.reply_to_message_id or 'none'} sender_id={row.sender_id} sender_label={json.dumps(row.sender_label, ensure_ascii=False)} text={json.dumps(row.text[:420], ensure_ascii=False)}"
                for row in thread.siblings
            )
            if thread_lines:
                sections.append(_block("MULTI_PERSON_REPLY_THREAD", thread_lines, 3600))
        except Exception as exc:
            logger.warning('V3_THREAD_CONTEXT_FAILED chat_id=%s message_id=%s error=%s', chat_id, message.message_id, type(exc).__name__)

    direct_targets: list[int] = []
    immediate_is_assistant = bool(
        chain and int(chain[0].get("telegram_message_id") or 0) == int(message.reply_to_message_id or 0)
        and chain[0].get("role") == "assistant"
    )
    if message.reply_sender_id and not message.reply_to_zero and not message.reply_sender_is_bot and not immediate_is_assistant and int(message.reply_sender_id) != current_id:
        direct_targets.append(int(message.reply_sender_id))
    if not direct_targets:
        for row in chain:
            sender_id = int(row.get("sender_id") or 0)
            if row.get("role") == "user" and sender_id and sender_id != current_id:
                direct_targets.append(sender_id)
                break

    mentions = await store.find_identity_mentions(chat_id, message.text)
    ambiguity = {name: ids for name, ids in mentions.items() if len(ids) > 1 and not any(i in direct_targets for i in ids)}
    target_ids = list(direct_targets)
    for ids in mentions.values():
        if len(ids) == 1 and ids[0] != current_id and ids[0] not in target_ids:
            target_ids.append(ids[0])

    target_lines: list[str] = []
    target_rag_lines: list[str] = []
    if not ambiguity:
        for target_id in target_ids[:4]:
            profile = await store.get_profile(chat_id, target_id)
            semantic_rows = semantic_memory.retrieve(chat_id, target_id, limit=6)
            target_lines.extend(_profile_lines(profile, semantic_rows, chat_id, target_id))
            target_memory = await store.retrieve_layered_memory(chat_id, "", sender_id=target_id, short_limit=0, medium_limit=4, long_limit=6)
            for row in target_memory["medium"]:
                participants = {int(x) for x in json.loads(row.get("participants_json") or "[]")}
                if participants and target_id in participants:
                    target_lines.append(f"medium owner_sender_ids={sorted(participants)} topic={row.get('topic','')} content={row.get('summary','')}")
            for row in target_memory["long"]:
                if int(row.get("subject_user_id") or 0) == target_id:
                    target_lines.append(f"long owner_sender_id={target_id} category={row.get('category','')} content={row.get('content','')}")
            if any(cue in (message.text or "").casefold() for cue in _RAG_CUES):
                try:
                    for row in await store.retrieve_rag(chat_id, target_id, message.text, limit=4):
                        if row.get('subject_user_id') is not None and int(row['subject_user_id']) == target_id:
                            target_rag_lines.append(f"layer={row.get('layer','')} owner_sender_id={target_id} category={row.get('category','')} content={row.get('content','')}")
                except Exception as exc:
                    logger.warning('TARGET_MEMORY_RAG_FAILED chat_id=%s target_sender_id=%s error=%s', chat_id, target_id, type(exc).__name__)
    if target_lines:
        sections.append(_block("TARGET_USER_MEMORY", target_lines, 4600))
    if ambiguity:
        sections.append(_block("TARGET_IDENTITY_AMBIGUITY", [json.dumps(ambiguity, ensure_ascii=False, separators=(",", ":"))], 1800))

    recent_keys: set[tuple[Any, ...]] = set()
    chain_keys = {(row.get("platform"), row.get("account_scope"), row.get("chat_id"), row.get("telegram_message_id")) for row in chain}
    flow_lines = []
    for row in recent[-20:]:
        key = (row.get("platform"), row.get("account_scope"), row.get("chat_id", chat_id), row.get("telegram_message_id") or f"local:{row.get('id')}")
        if key in chain_keys or key in recent_keys:
            continue
        recent_keys.add(key)
        flow_lines.append(_line(row))
    sections.append(_block("RECENT_GROUP_FLOW", flow_lines, 5200))

    terms = {x.casefold() for x in _TERMS.findall(message.text or "")}
    relevant_lines = []
    for row in reversed(recent[:-20]):
        key = (row.get("platform"), row.get("account_scope"), row.get("chat_id", chat_id), row.get("telegram_message_id") or f"local:{row.get('id')}")
        if key in chain_keys or key in recent_keys:
            continue
        hay = {x.casefold() for x in _TERMS.findall(str(row.get("text") or ""))}
        if terms & hay:
            recent_keys.add(key)
            relevant_lines.append(_line(row))
        if len(relevant_lines) >= 10:
            break
    sections.append(_block("RELEVANT_RECENT_MESSAGES", relevant_lines, 3200))

    monthly = await store.find_active_long_memory(chat_id, "group_monthly_summary")
    monthly_lines = [f"scope=group content={monthly.get('content','')}"] if monthly and monthly.get("subject_user_id") is None else []
    sections.append(_block("GROUP_MONTHLY_SUMMARY", monthly_lines, 2200))

    ordinary = list(extra_lines or [])
    for row in layered.get("short", []):
        ordinary.append(f"short scope=group topic={row.get('active_topic','')} mood={row.get('mood','neutral')}")
    for row in layered.get("medium", []):
        participants = [int(x) for x in json.loads(row.get("participants_json") or "[]")]
        scope = "group" if not participants else f"participants:{participants}"
        ordinary.append(f"medium scope={scope} topic={row.get('topic','')} content={row.get('summary','')}")
    for row in layered.get("long", []):
        owner = row.get("subject_user_id")
        ordinary.append(f"long scope={'group' if owner is None else 'personal'} owner_sender_id={owner or 'none'} category={row.get('category','')} content={row.get('content','')}")
    sections.append(_block("ORDINARY_MEMORY", ordinary, 4200))

    rag_lines: list[str] = list(target_rag_lines)
    if any(cue in (message.text or "").casefold() for cue in _RAG_CUES):
        try:
            for row in await store.retrieve_rag(chat_id, current_id, message.text, limit=8):
                rag_lines.append(f"layer={row.get('layer','')} owner_sender_id={row.get('subject_user_id') or 'none'} category={row.get('category','')} content={row.get('content','')}")
        except Exception as exc:
            logger.warning('MEMORY_RAG_FAILED chat_id=%s sender_id=%s error=%s', chat_id, current_id, type(exc).__name__)
    sections.append(_block("RAG_MEMORY", rag_lines, 4200))

    context = "\n".join(sections)
    return context, {"target_ids": target_ids if not ambiguity else direct_targets, "ambiguous": bool(ambiguity), "reply_chain_depth": len(chain), "thread_participant_ids": thread_participant_ids, "chars": len(context)}
