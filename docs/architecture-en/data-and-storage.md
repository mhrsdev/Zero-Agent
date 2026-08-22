# Data, SQLite, and Persistence

## Primary database

`ZeroConfig.memory.db_path` determines the main database path (`config.py:148-155`). The listener and panel pass that same path to `ZeroStore` (`run_listener.py:79`, `run_panel.py:59`). `scripts/init_db.py:14-17` initializes the same store.

`zero/storage.py` contains the inline `SCHEMA` beginning at line 24. It covers settings, context, recent messages, incoming dedup, profiles, memory layers, rate/vision events, statistics, router state, group/social state, cron, knowledge, Telegram search, proactive state, stickers, and Office-related tables.

## Identity and dedup

`incoming_message_dedup` uses `(platform, account_scope, chat_id, message_id)` as its primary key (`storage.py:46-64`). A message ID without account/chat scope is not sufficient identity.

Scoped user profiles use `(chat_id, sender_id)` (`storage.py:75-89`). Memory and Telegram search identity must preserve this scope; sender ID alone is not a safe replacement.

## Memory ownership

`ZeroBrain` constructs semantic, experience, procedural, world, and Memory V2 layers separately (`brain.py:226-248`). Retrieval or compaction in one layer must not silently mutate another layer.

`DeferredMemory`, `DocumentBundles`, and `KnowledgeWorker` also connect to the primary DB or related paths. Migration utilities under `scripts/` have side effects and require backup/rollback before production use.

## Migration and versioning

`ZeroStore._init_db()` runs the inline schema and then module-specific migrations (`storage.py:596-614`). The primary V1 database uses `CREATE TABLE IF NOT EXISTS`, `PRAGMA table_info`, and `ALTER TABLE` checks distributed across modules; no central migration-version table was observed.

Memory V2 has its own schema-version row. V1→V2 migration is explicit through `scripts/migrate_memory_v1_to_v2.py`, not automatic ZeroStore startup migration.

## Memory V2

`MemoryV2Service` defaults to `/opt/zero/runtime/state/zero-memory-v2.db`, independently of `config.memory.db_path` (`memory_v2/service.py:19-25`, `brain.py:239`). Its enable/read/write/shadow/retrieval controls are environment-driven. V2 sessions are structured, bounded, TTL-based, and optimistic-versioned.

## Office persistence

`OfficeRepository` is a separate logical persistence boundary but receives the same configured DB path in runtime. Therefore Office migration and locking share the SQLite database with ZeroStore; a separate Python package does not imply a separate DB.

The repository owns jobs, quotas, state transitions, leases, job events, and delivery outbox. Direct SQLite writes should be limited to tests/failure injection.

## Delivery and outbox

Delivery is expected only after output validation. The outbox has an idempotency key, and quota commit is tied to a delivery receipt. Crash-window behavior is implemented/tested under `zero/office/delivery.py` and `tests/test_office_delivery.py`.

## Runtime snapshot

A read-only audit observed a primary DB around 14 MB, mode `0600`, with approximately 88 tables. A separate Memory V2 DB was also mode `0600`. These are environment snapshots, not schema contracts.

Runtime state under `runtime/state`, `runtime/logs`, `runtime/office_jobs`, and `runtime/office_ingest` is operational/private data and must not be placed in public documentation or source archives.