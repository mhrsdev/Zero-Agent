# Forensic Reconstruction — 2026-07-25

Evidence was collected from the live checkout at `/opt/zero`; handoff files were treated as historical claims.

| Requirement | Evidence | Status | Relevant files/commits | Tests | Exact next action |
|---|---|---|---|---|---|
| Transformation branch and baseline | `git branch -avv`, `git rev-parse`, `git diff main...HEAD` | COMPLETE | branch `open-source/v0.1-transformation`; HEAD before this slice `55839d6`; baseline `f9588ec` | `git diff --check` | Continue only on transformation branch |
| Working tree/checkpoint truth | Initial `git status` showed modified `zero/brain.py`, `zero/core/memory_service.py`, and an untracked V3 regression | PARTIAL | current slice files | targeted and full suite after repair | Commit the completed V3 slice and refresh continuation checkpoint |
| Tracking set | All requested files existed except `RELEASE_CHECKLIST.md` | PARTIAL | root tracking files | N/A | Add release checklist and keep all tracking files synchronized |
| Production safety | `main` remained at `f9588ec`; worktree list contained only `/opt/zero`; active `zero-*` services were observed but not restarted or edited | COMPLETE for this slice | Git refs; systemd inventory only | N/A | Keep all runtime/service actions isolated and unapplied |
| Canonical config/setup | Runtime roots validate canonical config; panel setup uses shared service per prior commits, but legacy conversion and all adapters are not proven | PARTIAL | `zero/configuration/`, `zero/runtime_config.py`, `scripts/run_*.py`, commits `b9e75c8..55839d6` | prior 587 baseline | Audit remaining composition roots and legacy conversion |
| Memory V3 normal prompt path | `ZeroBrain._handle_no_media` and vision path use `MemoryService`/V3; V1 prompt composer is no longer called there | COMPLETE for normal prompt slice | `zero/brain.py`, `zero/core/memory_service.py` | V3-only regression; full suite | Inventory remaining non-prompt V1 writes/maintenance paths |
| Memory V1 retirement | `v1_memory_runtime_enabled=False`; legacy storage remains for archive/migration and some maintenance code is still present | PARTIAL | `zero/brain.py`, `zero/storage.py` | full suite green | Build direct V1→V3 migration contract with quarantine/resume/verify/rollback |
| Memory V2 retirement | V2 env vars no longer control V3; no `brain.memory_v2` alias; V2 implementation/tests still exist as historical/private material | PARTIAL | `zero/memory_v2/`, `zero/memory_v3/service.py`, V2 tests | V2 contract tests still collected | Remove V2 from active/public surfaces and classify/archive its tests without weakening required coverage |
| Direct V1→V3 migration | Existing tooling is V1→V2 or V2→V3 shaped; no verified direct V1→V3 run contract | NOT STARTED | `scripts/migrate_memory_v1_to_v2.py`, `zero/memory_v3/service.py` | none for direct path | Implement isolated direct migration with mandatory backup, run map, quarantine, resume, verify and scoped rollback |
| Multi-group tenancy | RequestContext exists, but installation/group ownership is not proven across all stores/jobs/files | PARTIAL | `zero/core/context.py`, storage and runtime modules | RequestContext tests only | Audit and implement ownership propagation and adversarial isolation |
| Providers/API web search | Existing legacy router/web paths exist; public API-only provider boundary is not proven | PARTIAL | `zero/router.py`, `zero/web.py`, `zero/web_search/` | existing local tests | Define normalized provider contract and remove unsupported public search surfaces |
| Telegram modes | Existing listener/Telethon/management paths exist, but Bot/User/Hybrid capability contracts are not proven as one product | PARTIAL | `scripts/run_listener.py`, Telegram modules | mocked legacy tests | Build shared adapters and duplicate-delivery prevention |
| Admin API/panel | Existing panel and setup code exist; complete English authenticated control-plane rebuild is not proven | PARTIAL | `panel/`, `zero/panel_store.py` | panel setup tests | Audit routes/auth/UI and implement truthful vertical slices |
| TUI | Minimal Zero CLI exists; setup/status/doctor/groups/backup/panel/logs are not complete | PARTIAL | `zero/cli.py`, `zero/__main__.py` | CLI tests | Extend using shared SetupService only after backend contracts |
| Docker/CI/license/SBOM | No final clean-install/release gate evidence; `PROPRIETARY_LICENSE` remains | NOT STARTED | root/deploy/docs | none for final gate | Prepare Apache materials, lock/dependency reports, SBOM, Compose and CI |
| Public release/E2E | No clean allowlist release tree and isolated Community E2E proof | NOT STARTED | `scripts/verify_public_artifact.py`, docs | scanner fixtures only | Build release tree and isolated E2E after core slices |

## Commands and observed results

- Branch: `open-source/v0.1-transformation`
- Baseline: `f9588ec6588299a04d29561c9b4c8415c54e9507`
- Initial reported HEAD verified: `55839d6a8859c35de33df9cf333c10ca6ccd4523`
- Initial tree was **not clean**; it contained an unfinished Memory V3 patch.
- Before repair: `586 passed, 2 failed, 1 skipped`; both failures were obsolete V1/V2 shadow-prompt expectations.
- After repair: `588 passed, 1 skipped`.
- `py_compile` passed for changed Python runtime modules.

This is a reconstruction and continuation checkpoint, not a release decision.
