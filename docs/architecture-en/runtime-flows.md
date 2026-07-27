# Actual Runtime Flows

## 1. Listener startup

1. `scripts/run_listener.py:66-79` loads `ZeroConfig` and initializes logging/storage.
2. `ZeroStore` expires stale incoming claims (`:79-82`).
3. Social, web, router, knowledge, and deferred-memory services are constructed (`:83-90`).
4. The Telethon client is connected and authorization is required (`:89-100`).
5. `ZeroBrain` and, when enabled, the Office repository/bridge/planner/coordinators are constructed (`:101-126`).
6. Allowed group IDs are resolved and short memory is rebuilt (`:128-142`).
7. Handlers and background tasks are registered; the process blocks in `run_until_disconnected` (`:427-516`, `:685-696`).

## 2. Telegram message path

`events.NewMessage` at `run_listener.py:427-430` invokes `_on_message` behind the global lock.

The handler:

- records DM permission and lets the Office bridge consume private messages first (`:152-165`);
- rejects chats outside ID, username, or title allowlists (`:53-63`, `:167-169`);
- claims/deduplicates the message using `(platform, account_scope, chat_id, message_id)` (`:171-181`);
- observes GIFs and stickers before normal handling (`:183-205`);
- ignores self-messages for normal replies (`:206-220`);
- gives the Office bridge another opportunity for allowed group messages (`:222-229`);
- resolves sender, reply context, bot state, mentions, media, threads, and platform/account scope (`:231-303`);
- records activity, social state, feedback, reactions, and memory (`:305-334`);
- processes deferred memory and possible reminder creation (`:335-345`);
- increments statistics and may answer immediately from deferred memory (`:346-360`);
- otherwise calls `brain.maybe_reply_with_media` (`:362-371`);
- suppresses policy/no-reply outcomes, applies delay/supersession checks, sends the reply, and persists delivery/social/memory state (`:373-422`).

Edited messages are reprocessed only when they newly address Zero, contain a trigger, or reply to Zero (`:432-447`). Membership events are handled as real `ChatAction` updates (`:449-507`). Raw reaction updates feed social awareness (`:508-528`).

## 3. Model response path

`ZeroBrain` constructs memory, web, market, vision, sticker, social, and proactive services (`brain.py:226-256`). `IndependentRouter.complete` follows configured primary/fallback providers (`router.py:182-200`).

- `KeyPool.reserve` uses an internal lock to select a healthy key (`router.py:50-77`).
- Provider errors become classified error types, cooldowns, or disabled keys (`router.py:120-180`).
- Logs/state expose shortened key IDs, not secret values (`router.py:52-56`, `:92-94`).

## 4. Listener background tasks

All of these run in the same asyncio listener process:

- Telegram health/reconnect every 30 seconds (`run_listener.py:664-683`).
- Idle starter every 300 seconds, gated by probability, gap, and policy (`:530-550`).
- Inactive-member ping every six hours with random backoff (`:552-573`).
- Template jobs every 30 seconds (`:575-584`).
- Office coordinator every two seconds when enabled (`:586-598`).
- Proactive followups with clamped environment-controlled interval/batch (`:600-616`).
- Social reflection every 24 hours (`:618-625`).
- Group-memory loop runs immediately at startup and then every 24 hours, despite its `monthly` function name (`:627-637`).
- Daily owner report checks every minute (`:639-662`).

## 5. Panel and management flow

`run_panel.py:56-80` constructs services and starts `PanelAPI`. Routes are registered in `panel_api.py:46-62` for health, authentication, dashboard, chats, memory, knowledge, router, logs, jobs, users, sessions, and settings.

- Owner access requires the configured owner ID and a private Telegram chat (`run_panel.py:43-48`).
- Panel sessions and CSRF values are process-memory state (`panel_api.py:42-45`, `:86-98`).
- Security headers and in-memory rate limiting are middleware (`:64-82`).
- Static paths are confined using `resolve()` (`:109-113`).

## 6. Office flow

Office services are created only when `config.office.enabled` is true. The bridge gets first priority for private messages and is called again for allowed groups (`run_listener.py:101-126`, `:158-165`, `:222-229`). The listener coordinator ticks lease recovery, planning, repair, review, and delivery every two seconds (`:586-598`). The separate worker is composed by `scripts/run_office_worker.py:21-46`.