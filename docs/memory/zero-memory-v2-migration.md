# Zero Memory V2 — Migration and Rollback

## Preconditions

1. Take encrypted SQLite/source backup; verify `PRAGMA integrity_check` and SHA-256.
2. Start with `ZERO_MEMORY_V2_ENABLED=false`, `ZERO_MEMORY_V2_SHADOW=true`.
3. Never delete V1 tables during shadow or production rollout.

## Migration command

```bash
python3 scripts/migrate_memory_v1_to_v2.py --db runtime/state/zero.db --dry-run
python3 scripts/migrate_memory_v1_to_v2.py --db runtime/state/zero.db --apply
```

The tool reads legacy long/medium/semantic rows, creates atomic V2 candidates, preserves source IDs where available, marks ambiguous source scope, rejects unsafe/low-value/raw rows, and reports created/deduped/superseded/disputed/skipped counts. `--apply` requires an explicit backup path whose encrypted hash verifies; default is dry-run.

## Rollout

1. Shadow metrics on fixed fixture corpus and real non-content telemetry.
2. Compare token count/selection precision/latency; do not inject V2.
3. Migrate in apply mode to isolated V2 tables.
4. Enable V2 only after acceptance gates.
5. Keep V1 read path available during a rollback window.

## Rollback

Set:

```text
ZERO_MEMORY_V2_ENABLED=false
ZERO_MEMORY_V2_SHADOW=false
```

Restart listener after compile/tests. This disables V2 without schema deletion. To restore data, stop writer, verify backup SHA-256, decrypt only as Owner, run SQLite integrity check, then restore snapshot atomically. V1 tables are untouched by V2 migration.