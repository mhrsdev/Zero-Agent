from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from .models import IncomingMessage

logger = logging.getLogger('zero.memory_context')


_TERMS = re.compile(r"[\wآ-ی‌]{3,}")
_RAG_CUES = ("یادت", "قبلاً", "قبلا", "همون", "اون موقع", "در حافظه", "اسم من", "چی گفتم")

# Emission order. `zero/prompts.py` explains the blocks in this order, so it is
# fixed independently of which block is funded first.
_ORDER = (
    "CURRENT_MESSAGE_IDENTITY", "CURRENT_USER_MEMORY", "REPLY_CHAIN",
    "MULTI_PERSON_REPLY_THREAD", "TARGET_USER_MEMORY", "TARGET_IDENTITY_AMBIGUITY",
    "RECENT_GROUP_FLOW", "RELEVANT_RECENT_MESSAGES", "GROUP_MONTHLY_SUMMARY",
    "ORDINARY_MEMORY", "RAG_MEMORY",
)
# Funding order. The per-block caps sum to 36,200 characters and nothing capped
# the total, so the group flow competed only with itself: a chat with long
# messages shipped an order of magnitude more context than any turn needs.
# TOTAL_BUDGET bounds every block together and is handed out in this order, so
# the flow can only spend what identity and target memory did not need.
# Constraint: the fact-bearing blocks must appear here in the same relative
# order in which they are composed below, because the first block to render a
# fact suppresses the copies in later blocks. Funding a claimant after its
# suppressed duplicate would be able to lose the fact entirely.
_PRIORITY = (
    "CURRENT_MESSAGE_IDENTITY", "TARGET_IDENTITY_AMBIGUITY", "CURRENT_USER_MEMORY",
    "TARGET_USER_MEMORY", "REPLY_CHAIN", "MULTI_PERSON_REPLY_THREAD",
    "GROUP_MONTHLY_SUMMARY", "ORDINARY_MEMORY", "RAG_MEMORY",
    "RELEVANT_RECENT_MESSAGES", "RECENT_GROUP_FLOW",
)
_CAPS = {
    "CURRENT_MESSAGE_IDENTITY": 1800, "CURRENT_USER_MEMORY": 2200, "REPLY_CHAIN": 3200,
    "MULTI_PERSON_REPLY_THREAD": 3600, "TARGET_USER_MEMORY": 4600,
    "TARGET_IDENTITY_AMBIGUITY": 1800, "RECENT_GROUP_FLOW": 5200,
    "RELEVANT_RECENT_MESSAGES": 3200, "GROUP_MONTHLY_SUMMARY": 2200,
    "ORDINARY_MEMORY": 4200, "RAG_MEMORY": 4200,
}
# `zero/config.py` takes no new field in this change, so the ceiling lives here.
# 9,000 characters holds every block a populated 100-message group produced when
# this was measured (7,003 characters) with room to grow.
TOTAL_BUDGET = 9000


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


def _fit(lines: list[str], cap: int) -> tuple[list[str], int, int]:
    """Keep the lines that fit, in the order given, and count what was dropped."""
    kept: list[str] = []
    used = 0
    dropped = 0
    for line in lines:
        clean = str(line).strip()
        if not clean:
            continue
        if used + len(clean) + 1 > cap:
            dropped += 1
            continue
        kept.append(clean)
        used += len(clean) + 1
    return kept, used, dropped


class _Facts:
    """What has already been attributed, so no fact is paid for twice.

    The RAG index is rebuilt from the same long/medium/semantic rows the other
    blocks render, so an un-deduplicated context ships the identical sentence in
    two or three blocks. Suppression is owner-aware: a record is dropped only
    when the same owner, category and value were already rendered, or when an
    unowned (group-scope) copy repeats a value already rendered against a named
    owner. An unowned line can never be the only attribution of anything, so
    dropping it removes no attribution; the reverse is never done.
    """

    __slots__ = ("_owned", "_values", "suppressed")

    def __init__(self) -> None:
        self._owned: set[tuple[str, str, str]] = set()
        self._values: set[str] = set()
        self.suppressed = 0

    @staticmethod
    def _normalize(value: Any) -> str:
        return " ".join(str(value if value is not None else "").split()).strip('"')

    def claim(self, owner: Any, category: Any, value: Any) -> bool:
        """Register one fact. False when it has already been attributed."""
        holder = str(owner) if owner not in (None, "", "none") else "none"
        key = (holder, self._normalize(category), self._normalize(value))
        if key in self._owned or (holder == "none" and key[2] in self._values):
            self.suppressed += 1
            return False
        self._owned.add(key)
        self._values.add(key[2])
        return True


class _Draft:
    """Blocks collected in any order, funded in `_PRIORITY`, emitted in `_ORDER`.

    A block with nothing in it is not emitted at all: a header and footer that
    frame no record cost tokens on every turn and describe data the model does
    not have. `meta` reports which blocks were emitted and what was dropped, so
    an absent block is still an observable state.
    """

    __slots__ = ("_blocks", "budget", "dropped")

    def __init__(self, budget: int) -> None:
        self._blocks: dict[str, tuple[list[str], bool]] = {}
        self.budget = budget
        self.dropped = 0

    def add(self, tag: str, lines: list[str], *, chronological: bool = False) -> None:
        """`lines` are in relevance order; `chronological` re-orders what survives."""
        kept = [str(line).strip() for line in lines if str(line).strip()]
        if kept:
            self._blocks[tag] = (kept, chronological)

    def render(self) -> tuple[str, dict[str, Any]]:
        remaining = self.budget
        rendered: dict[str, str] = {}
        for tag in _PRIORITY:
            entry = self._blocks.get(tag)
            if entry is None:
                continue
            lines, chronological = entry
            header, footer = f"[{tag}]", f"[/{tag}]"
            frame = len(header) + len(footer) + 2
            if remaining <= frame:
                self.dropped += len(lines)
                continue
            kept, used, dropped = _fit(lines, min(_CAPS[tag], remaining) - frame)
            self.dropped += dropped
            if not kept:
                continue
            if chronological:
                kept.reverse()
            rendered[tag] = "\n".join((header, *kept, footer))
            remaining -= used + frame
        context = "\n".join(rendered[tag] for tag in _ORDER if tag in rendered)
        return context, {
            "blocks": [tag for tag in _ORDER if tag in rendered],
            "dropped_lines": self.dropped,
            "budget_used": self.budget - remaining,
        }


def _read_subject_memory(conn, chat_id: int, subject_ids: list[int], now: int) -> tuple[dict, list[dict], list[dict]]:
    """Exactly the rows TARGET_USER_MEMORY renders, for up to four subjects, in one trip.

    This replaces one `get_profile` plus one `retrieve_layered_memory` per
    target. That call read every short, medium and long row of the chat, scored
    all of them in Python, wrote a MEMORY_RETRIEVED audit row — a write on the
    read path — and returned group-scope rows this block then discarded. Reading
    each subject's own rows removes the writes, the discarded rows and all but
    one round trip, and stops a chat's group-scope long rows from crowding a
    target's own rows out of the limit.
    """
    holders = ",".join("?" for _ in subject_ids)
    profiles = {
        int(row["sender_id"]): dict(row)
        for row in conn.execute(
            f'SELECT * FROM user_profiles_scoped WHERE chat_id=? AND sender_id IN ({holders})',
            (chat_id, *subject_ids),
        ).fetchall()
    }
    long_rows = [
        dict(row) for row in conn.execute(
            f'SELECT subject_user_id,category,content FROM long_term_memory WHERE chat_id=? '
            f'AND status="active" AND subject_user_id IN ({holders}) '
            'AND (expires_at IS NULL OR expires_at>=?) ORDER BY confidence DESC, updated_at DESC',
            (chat_id, *subject_ids, now),
        ).fetchall()
    ]
    # sqlite cannot filter a JSON participant list portably, so membership is
    # checked in Python; excluding '[]' still keeps every group-scope row out.
    medium_rows = [
        dict(row) for row in conn.execute(
            'SELECT participants_json,topic,summary FROM medium_term_memory WHERE chat_id=? '
            'AND status="active" AND expires_at>=? AND participants_json<>? '
            'ORDER BY confidence DESC, occurred_at DESC',
            (chat_id, now, "[]"),
        ).fetchall()
    ]
    return profiles, long_rows, medium_rows


async def _reply_chain(store, message: IncomingMessage, chat_id: int) -> list[dict]:
    if not (message.message_id and message.account_scope):
        return []
    return await store.get_reply_chain(message.platform, message.account_scope, chat_id, message.message_id, max_depth=8)


async def _thread_context(v3_memory, message: IncomingMessage, chat_id: int):
    if v3_memory is None or not message.message_id:
        return None
    try:
        return await v3_memory.thread_context(message, max_depth=8, sibling_limit=12)
    except Exception as exc:
        logger.warning('V3_THREAD_CONTEXT_FAILED chat_id=%s message_id=%s error=%s', chat_id, message.message_id, type(exc).__name__)
        return None


def _profile_lines(profile: dict[str, Any] | None, semantic_rows: list[dict], chat_id: int, sender_id: int, facts: _Facts) -> list[str]:
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
        if facts.claim(sender_id, f"{row['category']}.{row['key']}", row["value"]):
            lines.append(f"{row['category']}.{row['key']}={row['value']}")
    return lines


def _flow_key(row: dict[str, Any], chat_id: int) -> tuple[Any, ...]:
    return (
        row.get("platform"), row.get("account_scope"), row.get("chat_id", chat_id),
        row.get("telegram_message_id") or f"local:{row.get('id')}",
    )


async def _rag_lines(store, message: IncomingMessage, chat_id: int, current_id: int, subject_ids: list[int], facts: _Facts) -> tuple[str, list[str]]:
    """Retrieved memory, and which of three states the retrieval ended in.

    `not_requested` (the message carries no recall cue), `ok` and `unavailable`
    are different facts about the turn: an empty block used to mean all three at
    once. The reads are issued together because they differ only in whose rows
    they may return.
    """
    if not any(cue in (message.text or "").casefold() for cue in _RAG_CUES):
        return "not_requested", []
    reads = [store.retrieve_rag(chat_id, current_id, message.text, limit=8)]
    reads += [store.retrieve_rag(chat_id, sender_id, message.text, limit=4) for sender_id in subject_ids]
    current_rows, *target_rows = await asyncio.gather(*reads, return_exceptions=True)
    state = "ok"
    owned: list[str] = []
    for target_id, rows in zip(subject_ids, target_rows):
        if isinstance(rows, BaseException):
            state = "unavailable"
            logger.warning('TARGET_MEMORY_RAG_FAILED chat_id=%s target_sender_id=%s error=%s', chat_id, target_id, type(rows).__name__)
            continue
        for row in rows:
            if row.get('subject_user_id') is None or int(row['subject_user_id']) != target_id:
                continue
            if facts.claim(target_id, row.get('category', ''), row.get('content', '')):
                owned.append(f"layer={row.get('layer','')} owner_sender_id={target_id} category={row.get('category','')} content={row.get('content','')}")
    if isinstance(current_rows, BaseException):
        logger.warning('MEMORY_RAG_FAILED chat_id=%s sender_id=%s error=%s', chat_id, current_id, type(current_rows).__name__)
        return "unavailable", owned
    for row in current_rows:
        owner = row.get('subject_user_id')
        if facts.claim(owner, row.get('category', ''), row.get('content', '')):
            owned.append(f"layer={row.get('layer','')} owner_sender_id={owner or 'none'} category={row.get('category','')} content={row.get('content','')}")
    return state, owned


async def compose_memory_context(*, store, semantic_memory, message: IncomingMessage, recent: list[dict], layered: dict[str, list[dict]], extra_lines: list[str] | None = None, v3_memory: Any | None = None) -> tuple[str, dict[str, Any]]:
    """Build one bounded identity-safe context for every model path."""
    chat_id, current_id = int(message.chat_id), int(message.sender_id)
    now = int(time.time())
    draft = _Draft(TOTAL_BUDGET)
    facts = _Facts()
    # One round of independent reads instead of six sequential awaits. The store
    # runs them back to back on its own thread, so what this removes is the loop
    # round trip between each, not the database work. `semantic_memory.retrieve`
    # is synchronous sqlite: on the loop thread it was the most expensive step of
    # composition (3.5 ms of 7.4 ms, nearly all of it connection setup).
    current_profile, notes, chain, mentions, monthly, current_semantic, thread = await asyncio.gather(
        store.get_profile(chat_id, current_id),
        store.get_user_notes(chat_id, current_id, message.text, limit=4),
        _reply_chain(store, message, chat_id),
        store.find_identity_mentions(chat_id, message.text),
        store.find_active_long_memory(chat_id, "group_monthly_summary"),
        asyncio.to_thread(lambda: semantic_memory.retrieve(chat_id, current_id, limit=6)),
        _thread_context(v3_memory, message, chat_id),
    )

    identity_rows = [row for row in current_semantic if row.get('category') == 'identity']
    user_rows = [row for row in current_semantic if row.get('category') != 'identity']
    draft.add("CURRENT_MESSAGE_IDENTITY", _profile_lines(current_profile, identity_rows, chat_id, current_id, facts))
    current_lines = _profile_lines(None, user_rows, chat_id, current_id, facts)
    for note in notes:
        current_lines.append(f"note section={note['section']} source_message_id={note.get('source_message_id') or 'none'} content={note['content']}")
    draft.add("CURRENT_USER_MEMORY", current_lines)

    rendered_ids: set[int] = set()
    chain_lines = []
    for row in chain:
        chain_lines.append(_line(row))
        if row.get("telegram_message_id"):
            rendered_ids.add(int(row["telegram_message_id"]))
    if not chain and message.reply_sender_id:
        chain_lines = [
            f"telegram_message_id={message.reply_to_message_id or 'unknown'} sender_id={message.reply_sender_id} "
            f"role={'assistant' if message.reply_sender_is_bot else 'user'} sender_label={message.reply_sender_label!r} text={message.reply_text[:420]!r}"
        ]
        if message.reply_to_message_id:
            rendered_ids.add(int(message.reply_to_message_id))
    draft.add("REPLY_CHAIN", chain_lines)

    thread_participant_ids: list[int] = []
    if thread is not None:
        thread_participant_ids = list(thread.participant_ids)
        # A V3 ancestor is the same message REPLY_CHAIN already rendered from the
        # store; rendering both shipped every ancestor of a deep reply twice.
        thread_lines = [
            f"role=ancestor message_id={row.message_id} reply_to={row.reply_to_message_id or 'none'} sender_id={row.sender_id} sender_label={json.dumps(row.sender_label, ensure_ascii=False)} text={json.dumps(row.text[:420], ensure_ascii=False)}"
            for row in reversed(thread.ancestors) if int(row.message_id) not in rendered_ids
        ]
        thread_lines.extend(
            f"role=related_reply message_id={row.message_id} reply_to={row.reply_to_message_id or 'none'} sender_id={row.sender_id} sender_label={json.dumps(row.sender_label, ensure_ascii=False)} text={json.dumps(row.text[:420], ensure_ascii=False)}"
            for row in thread.siblings if int(row.message_id) not in rendered_ids
        )
        draft.add("MULTI_PERSON_REPLY_THREAD", thread_lines)
        rendered_ids.update(int(row.message_id) for row in (*thread.ancestors, *thread.siblings))

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

    ambiguity = {name: ids for name, ids in mentions.items() if len(ids) > 1 and not any(i in direct_targets for i in ids)}
    target_ids = list(direct_targets)
    for ids in mentions.values():
        if len(ids) == 1 and ids[0] != current_id and ids[0] not in target_ids:
            target_ids.append(ids[0])
    subject_ids = [] if ambiguity else target_ids[:4]

    target_lines: list[str] = []
    if subject_ids:
        (profiles, long_rows, medium_rows), target_semantic = await asyncio.gather(
            store._exec(lambda conn: _read_subject_memory(conn, chat_id, subject_ids, now)),
            asyncio.to_thread(lambda: {sender_id: semantic_memory.retrieve(chat_id, sender_id, limit=6) for sender_id in subject_ids}),
        )
        for target_id in subject_ids:
            target_lines.extend(_profile_lines(profiles.get(target_id), target_semantic.get(target_id, []), chat_id, target_id, facts))
            events = 0
            for row in medium_rows:
                participants = {int(x) for x in json.loads(row.get("participants_json") or "[]")}
                if events >= 4 or target_id not in participants:
                    continue
                events += 1
                if facts.claim(",".join(str(x) for x in sorted(participants)), row.get("topic", ""), row.get("summary", "")):
                    target_lines.append(f"medium owner_sender_ids={sorted(participants)} topic={row.get('topic','')} content={row.get('summary','')}")
            recalled = 0
            for row in long_rows:
                if recalled >= 6 or int(row.get("subject_user_id") or 0) != target_id:
                    continue
                recalled += 1
                if facts.claim(target_id, row.get("category", ""), row.get("content", "")):
                    target_lines.append(f"long owner_sender_id={target_id} category={row.get('category','')} content={row.get('content','')}")
    draft.add("TARGET_USER_MEMORY", target_lines)
    if ambiguity:
        draft.add("TARGET_IDENTITY_AMBIGUITY", [json.dumps(ambiguity, ensure_ascii=False, separators=(",", ":"))])

    monthly_lines = []
    if monthly and monthly.get("subject_user_id") is None and facts.claim(None, monthly.get("category", ""), monthly.get("content", "")):
        monthly_lines.append(f"scope=group content={monthly.get('content','')}")
    draft.add("GROUP_MONTHLY_SUMMARY", monthly_lines)

    ordinary = list(extra_lines or [])
    for row in layered.get("short", []):
        ordinary.append(f"short scope=group topic={row.get('active_topic','')} mood={row.get('mood','neutral')}")
    for row in layered.get("medium", []):
        participants = [int(x) for x in json.loads(row.get("participants_json") or "[]")]
        scope = "group" if not participants else f"participants:{participants}"
        if facts.claim(",".join(str(x) for x in sorted(participants)) if participants else None, row.get("topic", ""), row.get("summary", "")):
            ordinary.append(f"medium scope={scope} topic={row.get('topic','')} content={row.get('summary','')}")
    for row in layered.get("long", []):
        owner = row.get("subject_user_id")
        if facts.claim(owner, row.get("category", ""), row.get("content", "")):
            ordinary.append(f"long scope={'group' if owner is None else 'personal'} owner_sender_id={owner or 'none'} category={row.get('category','')} content={row.get('content','')}")
    draft.add("ORDINARY_MEMORY", ordinary)

    rag_state, rag_lines = await _rag_lines(store, message, chat_id, current_id, subject_ids, facts)
    draft.add("RAG_MEMORY", rag_lines)

    seen_keys = {_flow_key(row, chat_id) for row in chain}
    flow_lines = []
    # Newest first, so a block that overruns its share drops the oldest message
    # rather than the newest; `chronological` puts the survivors back in order.
    for row in reversed(recent[-20:]):
        key, message_id = _flow_key(row, chat_id), int(row.get("telegram_message_id") or 0)
        if key in seen_keys or (message_id and message_id in rendered_ids):
            continue
        seen_keys.add(key)
        flow_lines.append(_line(row))
    draft.add("RECENT_GROUP_FLOW", flow_lines, chronological=True)

    terms = {x.casefold() for x in _TERMS.findall(message.text or "")}
    relevant_lines = []
    for row in reversed(recent[:-20]):
        key, message_id = _flow_key(row, chat_id), int(row.get("telegram_message_id") or 0)
        if key in seen_keys or (message_id and message_id in rendered_ids):
            continue
        hay = {x.casefold() for x in _TERMS.findall(str(row.get("text") or ""))}
        if terms & hay:
            seen_keys.add(key)
            relevant_lines.append(_line(row))
        if len(relevant_lines) >= 10:
            break
    draft.add("RELEVANT_RECENT_MESSAGES", relevant_lines)

    context, budget = draft.render()
    logger.info(
        'MEMORY_CONTEXT_BUDGET chat_id=%s chars=%s budget=%s blocks=%s dropped_lines=%s deduped_facts=%s rag=%s',
        chat_id, len(context), TOTAL_BUDGET, len(budget['blocks']), budget['dropped_lines'], facts.suppressed, rag_state,
    )
    return context, {
        "target_ids": target_ids if not ambiguity else direct_targets,
        "ambiguous": bool(ambiguity), "reply_chain_depth": len(chain),
        "thread_participant_ids": thread_participant_ids, "chars": len(context),
        "rag": rag_state, "deduped_facts": facts.suppressed, **budget,
    }
