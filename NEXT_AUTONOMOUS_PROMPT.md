# Autonomous Zero Transformation Continuation

## Exact checkpoint

- Repository: `/root/zero`
- Branch: `open-source/v0.1-transformation`
- HEAD: `9ff6a56816d31c80c1f1dfcf354e9ddd223aa429`
- Working tree: clean at checkpoint creation
- Latest full test result: `585 passed, 1 skipped`
- Production and `main`: untouched

## Completed; do not repeat

- Phase 0 encrypted backup and isolated restore rehearsal
- Phase 1 public/private boundary and fail-closed artifact scanner
- Phase 2 immutable RequestContext listener boundary
- Phase 3 initial strict canonical configuration models and atomic store
- Phase 3 MemoryService V3 boundary
- Zero CLI entrypoint: version, status, config show

Commits relevant to this transformation:

- `23f1a08` public release boundary
- `f81bc8f` RequestContext boundary
- `b9e75c8` strict canonical configuration store
- `110311d` canonical MemoryService boundary
- `455ddbc` Zero CLI entrypoint

## First next action

Complete the shared setup integration boundary: pass `ConfigStore`/`SetupService`
into `PanelStore`/`PanelAPI`, make Telegram setup write only symbolic refs to
canonical config, and add tests proving panel setup reloads the same config used
by runtime roots. Then begin the V3-only migration contract.

## Current unfinished work

1. Canonical configuration integration and shared setup
2. V3-only runtime cutover without dual prompt injection
3. Direct V1→V3 migration with quarantine/resume/verification/rollback
4. Active/public Memory V2 removal
5. Multi-Group isolation
6. Provider abstraction
7. External API-only Web Search
8. Bot/User Session/Hybrid Telegram adapters
9. Admin API/authentication
10. English web panel
11. Zero TUI
12. Docker/CI/SBOM/license gates
13. Documentation and clean public artifact
14. Isolated Community E2E
15. Production migration/publication packages, prepared but unapplied

## Known regression history

A direct attempt to force `v1_memory_runtime_enabled=False` broke existing V1 migration-shadow and profile-refresh behavior. It was reverted. Do not repeat that broad switch. Classify each failing behavior and migrate it behind `MemoryService`/V3 with regression tests before disabling V1.

## Safety boundaries

- Work only on `open-source/v0.1-transformation`.
- Do not touch `main`, production databases, sessions, credentials, queues, systemd units, active panel or services.
- No live Telegram/provider calls; use mocks or isolated synthetic environments.
- Do not delete legacy V1/V2 artifacts during safe development.
- Do not publish, rewrite Git history, rotate credentials, migrate production or make the repository public.
- Keep secrets redacted and symbolic references only.

## Required continuation discipline

After each atomic milestone: targeted tests, full suite if feasible, `git diff --check`, focused commit, and updates to `TRANSFORMATION_STATUS.md`, `TRANSFORMATION_JOURNAL.json`, `RELEASE_BLOCKERS.md`, `MIGRATION_STATUS.md`, `TEST_STATUS.md`, and this file. Keep the tree clean before stopping.
