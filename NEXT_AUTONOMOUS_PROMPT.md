# Autonomous Zero Transformation Continuation

## Exact checkpoint

- Repository: `/root/zero`
- Branch: `open-source/v0.1-transformation`
- HEAD: `a58c83b` (`feat: enforce memory v3-only normal runtime`)
- Baseline/main: `f9588ec6588299a04d29561c9b4c8415c54e9507`; main was not changed
- Working tree: clean at checkpoint creation after the tracking commit below
- Full tests: `588 passed, 1 skipped`
- Changed-module compile: passed
- Production services/databases/sessions/credentials/systemd units: not modified or restarted

## Verified reconstruction

- The reported `55839d6` existed, but the tree was not clean: it contained an unfinished Memory V3 prompt patch and an untracked regression.
- The missing requested `RELEASE_CHECKLIST.md` was added.
- Initial pre-repair suite was `586 passed, 2 failed, 1 skipped`; failures were obsolete V1/V2 shadow-prompt expectations.
- Current suite is green at `588 passed, 1 skipped`.
- Full matrix: `docs/FORENSIC_RECONSTRUCTION.md`.

## Completed in this slice; do not repeat

- Normal `ZeroBrain` prompt retrieval now goes through `MemoryService` backed by Memory V3.
- Vision prompt retrieval now uses the same V3 boundary.
- Normal V1 runtime retrieval/write flag is disabled; legacy V1 storage remains for archive/migration only.
- V2 environment variables cannot select the V3 runtime and `brain.memory_v2` is absent.
- Group monthly summaries write canonical V3 group items.
- V3-only regressions cover prompt exclusion, V2-env isolation, and V1 runtime disablement.
- Tracking and forensic reconstruction files refreshed.

## Remaining incomplete work

1. Direct V1 → V3 migration with mandatory backup, dry-run, run ID/map, validation, quarantine, interruption resume, verification, idempotence and scoped rollback.
2. Remove V2 from active/public runtime, setup, API, panel, TUI, config, tools and public docs while retaining safe historical artifacts.
3. Finish canonical config conversion and prove every composition root.
4. Implement true installation/group/membership/permission/tenant ownership and adversarial isolation.
5. Normalize providers and implement only tested external API Web Search; remove public local/scraping coupling.
6. Implement shared Bot/User Session/Hybrid adapters and duplicate-response prevention; keep Management Bot separate.
7. Build secure canonical Admin API/authentication and the new English panel.
8. Build the Zero-specific TUI using shared services.
9. Isolate Office/Proactive, then Docker Compose, CI, license/NOTICE, dependency report, SBOM, docs, artifact tree and Community E2E.

## First next atomic action

Inventory the existing V1 tables and schemas from `zero/storage.py` and the
legacy migration helpers, then write a failing isolated test for a **direct**
V1→V3 dry-run/apply contract. The test must include one valid scoped record,
one ambiguous record that is quarantined rather than guessed, one pre-existing
V3 row that survives, and a stable run ID/map. Do not use V2 as an intermediate
target. Do not touch production databases.

Relevant files:

- `zero/memory_v3/service.py`
- `zero/core/memory_service.py`
- `zero/brain.py`
- `zero/storage.py`
- `scripts/migrate_memory_v1_to_v2.py` (historical shape; do not extend as the public path)
- `scripts/migrate_memory_v3.py` (current legacy importer to audit)
- `tests/test_memory_v3_only_prompt.py`
- `tests/memory_v2/integration/test_shadow_prompt_e2e.py` (now V3-only behavior despite historical path)
- `MIGRATION_STATUS.md`
- `docs/FORENSIC_RECONSTRUCTION.md`

## Safety boundaries

- Work only on `open-source/v0.1-transformation` or an isolated worktree.
- Never modify `main`, active production source/config/services, production DBs,
  sessions, credentials, queues, panel or group data.
- Never restart production or make live Telegram/provider calls.
- Development migrations must use copied synthetic databases and restricted
  backups with integrity/hash verification.
- Do not publish, push forcefully, rewrite history, rotate credentials, revoke
  sessions, permanently delete V1/V2 artifacts, or change repository visibility.
- Keep secrets redacted and symbolic references only.

## Required checkpoint discipline

For the next atomic migration slice: RED test first, implement minimally, run
targeted tests, run the full suite, run `git diff --check`, update all tracking
files and this prompt with the exact commit, commit, then verify a clean tree.
