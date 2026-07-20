# Legacy Search Archive

## Status

Legacy Web Search and Telegram Search are archived and disabled on the production response path. Existing source code, SQLite tables, queues, caches, sessions, and migrations are retained read-only for recovery/audit; no destructive migration is used.

## Previous architecture

- `zero/web.py` facade over `zero/web_search/`.
- Legacy web providers: SearXNG and Bing RSS, with optional direct-fetch/browser extraction.
- Legacy web conversation state, candidate ingestion, context injection, and truthfulness/numeric fallback.
- `zero/telegram_search.py`: joined-dialog search, global search, public-channel inspection, and web Telegram discovery.
- Telegram search state/cache/limits and Telegram knowledge candidates in SQLite.

## Why archived

The old path mixed provider-specific discovery, scraping/fetching, and Telegram MTProto search with response generation. It did not provide one authoritative grounding boundary or a provider/key/quota-aware router. Production search is now restricted to Google AI Studio's official Google Search tool (Grounding).

## Known limitations of the archived path

- Legacy providers do not provide the new Google grounding metadata contract.
- Telegram global/joined/inspector calls are not allowed from the active response path.
- Old state/cache/queue records are not valid search context for a new request.
- Historical model IDs and provider settings must be re-verified before any recovery.

## Related files and tables

- Files: `zero/web.py`, `zero/web_search/`, `zero/telegram_search.py`.
- Configuration: `web.legacy_archived`, `web.legacy_web_enabled`, `telegram_search.archived`, `telegram_search.enabled`.
- SQLite: legacy web/Telegram cache, state, limit, and knowledge-candidate tables defined by `zero/storage.py`.
- Historical session files remain under `runtime/state/` and are not opened by the active search path.

## Recovery (read-only planning)

Recovery requires an explicit code/config change, a fresh encrypted backup, focused tests, and owner approval. Restore is not a toggle operation: the archived implementation must be reviewed against the current provider, security, scope-isolation, and truthfulness gates before reactivation. No secrets are stored in this document.
