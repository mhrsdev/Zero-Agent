# Zero Transformation Status

- Current branch: `open-source/v0.1-transformation`
- Current HEAD: `bb8d56d` (`feat: bind listener requests to group tenancy`)
- Baseline commit: `f9588ec6588299a04d29561c9b4c8415c54e9507`
- Current milestone: P0-1 listener tenancy binding
- Latest full suite: `652 passed, 1 skipped`
- Initial reconstruction found an unfinished dirty Memory V3 slice; it is now ready for commit.
- Production migration: not started
- Public publication: not authorized
- Main branch: untouched

## Completed

- Phase 0 encrypted allowlist backup and isolated restore rehearsal.
- Phase 1 public/private boundary and fail-closed artifact scanner.
- Phase 2 immutable RequestContext transport boundary.
- Initial strict typed canonical configuration store and atomic persistence.
- Initial MemoryService boundary backed by Memory V3.
- Zero CLI entrypoint with version, status and config inspection.
- Memory V3-only normal prompt retrieval regression and canonical V3 monthly-summary write path.
- Direct V1→V3 mapping for `long_term_memory`, `medium_term_memory` and `semantic_user_memory` with backup proof, quarantine, resume, verification and rollback.
- Opus tenancy primitives and provider registry integrated; release infrastructure, Docker/CI definitions, SBOM generator, Apache-2.0 notices and lockfile added.
- Listener now resolves and validates a group `Scope` per inbound message, binds `MemoryService`, seeds configured groups as ACTIVE, and uses group-scoped idle/interject cooldowns.

## Active blockers

- Canonical config is not yet connected to every composition root.
- Direct V1→V3 migration remains incomplete; normal prompt retrieval is now V3-only.
- Memory V2 remains outside the public-release boundary and requires active-surface removal.
- Multi-Group delivery outside the listener, Telegram modes, provider runtime wiring, Admin API, panel, TUI and Docker remain incomplete.
- Public release-tree scan, dependency audit, SBOM JS coverage and Community E2E gates remain open.

## Safety

No production service, database, session, credential, queue, main branch or public infrastructure was changed.

## Next

Complete the remaining P0-1/P0-2 runtime isolation proof: wrong-thread delivery, jobs/files/quotas and panel scope. Do not touch production.
