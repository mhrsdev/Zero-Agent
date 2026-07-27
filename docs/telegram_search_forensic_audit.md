# Telegram Search — Forensic Audit (pre-rewrite)

Date: 2026-07-11 UTC

## Current implementation

- `zero/telegram_search.py`: one `TelegramSearchClient`, `TelegramSearchHit`, an in-memory TTL cache, and a loop over `config.telegram_search.allowed_chat_usernames`.
- `zero/brain.py`: heuristic `is_telegram_search_request`, one `build_search_query` call, then `TelegramSearchClient.search`; result context is a short ad-hoc string.
- `scripts/run_panel.py`: owner gate exists; only `on|off|status` and debug `tgtest` are exposed.
- `scripts/run_listener.py`: production listener uses `TelegramClient.connect()` and `is_user_authorized()`; module logging is routed to `listener.log`.
- `scripts/join_tgsearch_channels.py` and `scripts/login_tgsearch.py` are operational scripts outside the production search path.

## Telethon/session findings

- Installed Telethon: 1.44.0.
- A read-only prototype confirmed `messages.SearchGlobalRequest` exists and executes with the configured search session; for query `Gemini` it returned 10 messages from 1 peer, all observed peers were already in the 21 inspected group/channel dialogs. This is capability evidence, not full Telegram coverage.
- Production search session is the configured Telegram-search session. The production path uses `connect()` plus `is_user_authorized()`; no production `start()` was found in the provider. Separate login/join scripts do use `start()` and are not production search.

## Existing behavior and limitations

- Searches only the configured allowlist, not all dialogs and not Telegram-wide discovery.
- No global-search provider, public-channel inspector, web Telegram discovery provider, shared request/result contract, routing model, scoped Telegram follow-up state, ranking, cross-provider deduplication, or bounded structured context builder.
- Per-chat result budget is derived from total result count; one message fetch call is used per allowlisted peer.
- Query extraction is performed in the brain and passed to the client; the client does not extract again.
- FloodWait logs the wait and stops the remaining peers; other peer failures are isolated.
- Logging includes raw query/channel values in places and has no unified redacted event contract.
- No Telegram-search-specific unit/integration test module existed before the rewrite.

## Root-cause statement

The joined-only behavior is architectural: the only production provider is a configured allowlist loop calling `get_messages(entity, search=query)`. Telegram global search is never called, and web search is explicitly skipped for Telegram intents. Therefore results cannot come from peers outside that allowlist.

## Safety findings

- Production listener/client path is read-only and uses `connect()`/authorization checks.
- `login_tgsearch.py`, `join_tgsearch_channels.py`, and unrelated sticker prototypes contain interactive `client.start()` or join-capable code; they must remain outside the production search path.
- Session/API secrets are intentionally omitted from this audit.

## Baseline verification

- Targeted existing search/web suite: 40 passed.
- Backup gate completed before edits: encrypted archive, SHA-256 verification, decrypt/archive-list verification, and SQLite `PRAGMA integrity_check` passed. Backup handle is recorded in the change report, not in application logs.
