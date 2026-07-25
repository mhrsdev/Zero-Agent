# Memory Migration Status

- Canonical target: Memory V3
- Active stable boundary: `zero.core.memory_service.MemoryService`
- Shared SetupService is now wired into the panel composition root for Telegram setup.
- Runtime composition roots validate a present canonical config before legacy runtime load.
- Current runtime: normal prompt retrieval and normal writes are V3-only; V1 storage remains archive/migration material
- V1→V3 migration: partial; `long_term_memory`, `medium_term_memory` and `semantic_user_memory` map directly
- V1 read-only cutover: normal runtime flag disabled; migration tooling still required
- V2 removal: not yet complete
- Production migration: not started

## Required next work

1. extend direct mapping beyond `long_term_memory` to every approved V1 source table after schema inventory
2. preserve legitimate behavior in Memory V3
3. add source/target count and provenance reports for all mapped tables
4. add quarantine reasons for ambiguous identity, scope, time and content
5. verify interruption resume and scoped rollback across all mappings
6. remove V2 from active/public surfaces while retaining historical artifacts safely

## Implemented migration contract slice

- `zero.memory_v3.migration` provides direct V1→V3 dry-run/apply/verify/rollback for `long_term_memory`.
- `medium_term_memory` is now mapped directly as group-scoped `v1:group_event` items,
  preserving participant and source-message provenance.
- `semantic_user_memory` is now mapped directly as personal-scoped `v1:semantic`
  items, preserving identity/value/evidence provenance and quarantining invalid JSON.
- Apply requires an existing backup path and matching SHA-256 proof.
- Stable `(run_id, source_table, source_id, source_hash)` mapping makes repeat runs idempotent.
- Empty/invalid content, scope or source IDs are quarantined without guessing.
- Pre-existing V3 rows are reused and protected from rollback.
- Fault-injected interruption leaves an `interrupted` run that can resume.
- This is a partial migration contract, not permission to migrate production.
- known gap: remaining approved V1 tables and complete source/target reporting are not mapped
