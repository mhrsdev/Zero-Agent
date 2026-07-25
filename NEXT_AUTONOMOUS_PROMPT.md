# Autonomous Zero Transformation Continuation

## Exact checkpoint

- Repository: `/root/zero`
- Branch: `open-source/v0.1-transformation`
- HEAD: `8039e9e` (`feat: migrate medium-term memory directly to v3`)
- Baseline/main: `f9588ec6588299a04d29561c9b4c8415c54e9507`; main was not changed
- Working tree: clean at checkpoint creation after the tracking commit below
- Full tests: `590 passed, 1 skipped`
- Migration contract test: `1 passed`
- Changed-module compile and migration CLI help: passed
- Production services/databases/sessions/credentials/systemd units: not modified or restarted

## Completed in this slice; do not repeat

- Forensic reconstruction and matrix: `docs/FORENSIC_RECONSTRUCTION.md`.
- Normal `ZeroBrain` and vision prompt retrieval use `MemoryService` backed by V3.
- Normal V1 runtime retrieval/write flag is disabled; V1 remains archive/migration material.
- V2 environment variables cannot select V3 and `brain.memory_v2` is absent.
- Group monthly summaries write canonical V3 group items.
- Direct V1→V3 migration contract foundation in `zero/memory_v3/migration.py` and `scripts/migrate_memory_v1_to_v3.py` for `long_term_memory`:
  - dry-run/apply/verify/rollback;
  - mandatory backup path plus SHA-256 proof for apply;
  - stable run/source/hash mapping and idempotence;
  - quarantine for ambiguous content/scope/source IDs;
  - pre-existing V3 preservation;
  - fault-injected interruption and resume;
  - scoped soft-delete rollback.
- Direct mapping now also covers approved `medium_term_memory` rows with group scope,
  participant provenance, source-message provenance, quarantine, resume and rollback.
- Tests and tracking are committed; the migration is explicitly partial and has not touched production.

## Remaining incomplete work

1. Extend direct mapping to every approved V1 table after live schema inventory (semantic/profile/notes, social state, RAG/archive policy, and any other approved sources). Add complete source/target counts and provenance.
2. Remove V2 from active/public runtime, setup, API, panel, TUI, config, tools and public docs while retaining safe historical artifacts.
3. Finish canonical config conversion and prove every composition root.
4. Implement true installation/group/membership/permission/tenant ownership and adversarial isolation.
5. Normalize providers and implement only tested external API Web Search; remove public local/scraping coupling.
6. Implement shared Bot/User Session/Hybrid adapters and duplicate-response prevention; keep Management Bot separate.
7. Build secure canonical Admin API/authentication and the new English panel.
8. Build the Zero-specific TUI using shared services.
9. Isolate Office/Proactive, then Docker Compose, CI, license/NOTICE, dependency report, SBOM, docs, artifact tree and Community E2E.

## First next atomic action

Read all V1 source schemas in `zero/storage.py` and existing migration helpers,
then add a RED test for the next approved table (`semantic_user_memory`) through the same direct V1→V3 run map. Preserve the backup precondition, quarantine ambiguous rows, pre-existing V3 rows, interruption resume, verification and rollback. Do not route through V2 and do not apply to production.

Relevant files:

- `zero/memory_v3/migration.py`
- `scripts/migrate_memory_v1_to_v3.py`
- `zero/memory_v3/service.py`
- `zero/storage.py`
- `tests/test_memory_v1_to_v3_migration.py`
- `MIGRATION_STATUS.md`
- `TRANSFORMATION_JOURNAL.json`

## Safety boundaries

- Work only on `open-source/v0.1-transformation` or an isolated worktree.
- Never modify `main`, active production source/config/services, production DBs,
  sessions, credentials, queues, panel or group data.
- Never restart production or make live Telegram/provider calls.
- Development migrations must use copied synthetic databases and restricted
  backups with integrity/hash verification.
- Do not publish, force-push, rewrite history, rotate credentials, revoke
  sessions, permanently delete V1/V2 artifacts, or change repository visibility.
- Keep secrets redacted and symbolic references only.

## Required checkpoint discipline

For the next atomic migration slice: RED test first, implement minimally, run
targeted tests, run the full suite, run `git diff --check`, update all tracking
files and this prompt with the exact commit, commit, then verify a clean tree.
