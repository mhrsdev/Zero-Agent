# Zero Transformation Status

- Current branch: `open-source/v0.1-transformation`
- Current HEAD: `57f2693` (`feat: add release infrastructure and hardening`)
- Baseline commit: `f9588ec6588299a04d29561c9b4c8415c54e9507`
- Current milestone: Opus checkpoint reconciliation and release infrastructure
- Latest full suite: `650 passed, 1 skipped`
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

## Active blockers

- Canonical config is not yet connected to every composition root.
- Direct V1→V3 migration remains incomplete; normal prompt retrieval is now V3-only.
- Memory V2 remains outside the public-release boundary and requires active-surface removal.
- Multi-Group, Telegram modes, provider abstraction, Admin API, panel, TUI and Docker remain incomplete.
- Public release-tree scan, dependency audit, SBOM JS coverage and Community E2E gates remain open.

## Safety

No production service, database, session, credential, queue, main branch or public infrastructure was changed.

## Next

Complete P0-1: bind the listener request path to tenancy, remove `groups[0]`, and prove two-group routing/cooldown isolation. Do not touch production.
