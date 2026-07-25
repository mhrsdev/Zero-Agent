# Zero Reconstruction Matrix

Evidence-based state of all 35 subsystems, reconstructed from Git, source and
tests rather than from tracking-file claims.

- Reconciled at: `bb8d56d` on `open-source/v0.1-transformation`
- Working tree: clean
- Suite: `652 passed, 1 skipped, 0 failed, 0 collection errors`
- `ruff check zero scripts tests`: not run; `ruff` unavailable on the real host
- `compileall zero scripts`: exit 0
- source-tree artifact scan: not a valid public-release result because private historical files remain
- `pip check`: not run as a release gate

A subsystem is COMPLETE only when it has a real implementation, is reached by
the production call graph, is tested, and behaves as intended. **A module that
exists and passes its own tests but is imported only by those tests is
PARTIAL, not COMPLETE.**

## Corrections to previously recorded status

Three prior entries overstated completion. Verified here and corrected:

| Subsystem | Was recorded | Actually | Proof |
|---|---|---|---|
| Provider architecture | COMPLETE | **PARTIAL** | `zero/providers` is imported only by `tests/test_providers.py`. `zero/router.py:101` still builds pools from `config.router.providers`. Zero runtime integration. |
| Multi-Group tenancy | PARTIAL, "enforced on MemoryService" | **PARTIAL, enforcement dormant** | `MemoryService.bind()` has no production call site. `zero/brain.py:256` constructs `MemoryService(self.memory_v3)` unbound, and an unbound service deliberately skips every check (`zero/core/memory_service.py:44-46, 55-57`). |
| English panel | NOT STARTED | **PARTIAL** | `panel/index.html:2` is `lang="en"` with no Persian text; `panel/app.js` has all 12 required nav items and real auth + setup wiring. But 10 of 12 pages render `generic()` placeholder copy. |

## Matrix

| # | Subsystem | Status | Evidence | Missing integration / risk |
|---|---|---|---|---|
| 1 | Core & RequestContext | PARTIAL | `zero/core/context.py`; `tests/test_request_context.py` | Not threaded through adapters; no `request_id`/`trace_id` propagation into tenancy |
| 2 | Canonical configuration | PARTIAL | `zero/configuration/__init__.py`; strict models, atomic save, backup+rollback, symbolic refs; `tests/test_canonical_configuration.py` (9) | Legacy `ZeroConfig` (`zero/config.py`) is still the runtime adapter; two config systems coexist |
| 3 | SetupService | PARTIAL | `zero/configuration`; panel `/api/local/setup/{step}`; `tests/test_panel_setup_service.py` | Setup→persist→reload→launch never proven end to end; TUI has no setup path |
| 4 | Memory V3 runtime | COMPLETE | `zero/core/memory_service.py`, `zero/memory_v3/`; `tests/test_memory_v3_only_prompt.py`, `test_memory_service_contract.py` | V3 is the only normal write/read/prompt source |
| 5 | V1→V3 migration | PARTIAL | `zero/memory_v3/migration.py` maps `long_term_memory`, `medium_term_memory`, `semantic_user_memory`; migration tests pass | Remaining approved V1 tables unmapped: `memory_items`, `user_profiles`, `user_profiles_scoped`, `memory_rag_documents`, `memory_audit`, `memory_revisions`, `social_*` |
| 6 | Memory V2 retirement | COMPLETE | Module unimportable; guard test in `tests/test_memory_v3.py` scans `zero/` and `scripts/` | — |
| 7 | Multi-Group tenancy | PARTIAL | `zero/tenancy/`; `tests/test_tenancy_isolation.py` plus P0 listener guard | Listener now resolves/binds ACTIVE group scopes; jobs/files/quotas/panel are not fully scoped |
| 8 | Listener routing & delivery | PARTIAL | `scripts/run_listener.py`, `run_panel.py`, `tests/test_p0_listener_tenancy_guard.py` | First-group routing removed and cooldown keys are group-scoped; wrong-thread and all delivery paths still need adversarial E2E |
| 9 | Jobs, queues, outboxes | NOT STARTED (scope-wise) | `zero/office/db.py`, `zero/proactive_*.py` | No `installation_id`/`group_id` ownership columns or predicates |
| 10 | Files & generated artifacts | NOT STARTED (scope-wise) | `zero/office/workspace.py` keys by `chat_id` only | No installation/group ownership; no cross-tenant path test |
| 11 | Provider architecture | PARTIAL | `zero/providers/` + `tests/test_providers.py` (24) | Not wired: `zero/router.py:101,116,151,230,247` uses legacy config |
| 12 | External API Web Search | PARTIAL | `zero/web_search/providers/brave.py`; AST guard in `test_web_search_architecture.py` | No central `WebSearchService`; no `web_fetch` split; no group/permission/quota awareness; single provider |
| 13 | Bot Mode | NOT STARTED | No adapter module; `aiogram` used only by Management Bot (`run_panel.py`) and a `doctor` import check | — |
| 14 | User Session Mode | PARTIAL | `scripts/run_listener.py` Telethon listener | No portable adapter, no OTP/2FA/flood-wait/session-replace contract |
| 15 | Hybrid Mode | NOT STARTED | — | No capability router, no dedup |
| 16 | Management Bot | PARTIAL | `scripts/run_panel.py` aiogram bot | Correctly separate; not yet behind the adapter contract |
| 17 | Admin authentication | PARTIAL | scrypt hashing `zero/panel_store.py:39` (n=16384,r=8,p=1) — modern and adequate | **`admin`/`Admin` weak-password path** `panel_store.py:121`, `panel_api.py:169`; **sessions in process memory** `panel_api.py:102`; **two auth systems** (`/api/auth/*` OTP and `/api/local/auth/*`) |
| 18 | Admin API | PARTIAL | 35 endpoints in `zero/panel_api.py`; `tests/test_panel_api.py`, `test_panel_local_api.py` | No `/api/groups`, `/api/backups`, `/api/telegram`, `/api/web-search`, `/api/tools`, `/api/usage`; not typed; no group scoping |
| 19 | English panel | PARTIAL | `panel/index.html:2` `lang="en"`; 12 nav items; real auth + setup; no TODO markers | **10 of 12 pages are `generic()` placeholder copy** (telegram, groups, models, web-search, memory, tools, usage, logs, backups; settings unverified). Only dashboard and setup are real |
| 20 | Zero TUI | PARTIAL | `zero/cli.py`: version/status/doctor/config/panel/listener; `tests/test_cli.py` (7) | No interactive TUI; no setup/groups/backup commands |
| 21 | Office Agent | PARTIAL | `zero/office/*`; rlimit sandbox fails closed | Not group-scoped; 9 tests skip without the external binary; release isolation gate open |
| 22 | Proactive | PARTIAL | `zero/proactive_*.py` | No consent/opt-out/quiet-hours; no group/thread ownership on outbox |
| 23 | Docker Compose | PARTIAL / NOT VERIFIED | `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `requirements.lock` | **Image never built** — no Docker daemon available. Content-gated only |
| 24 | CI | PARTIAL / NOT VERIFIED | `.github/workflows/ci.yml`, 5 jobs, valid YAML | **Never executed** — no runner |
| 25 | Dependency locking | COMPLETE | `requirements.lock`, pinned hashes, `--require-hashes` in Dockerfile | JS deps unlocked (see 28) |
| 26 | Security scanning | PARTIAL | `scripts/verify_public_artifact.py` available; `pip-audit` wired in CI | public-tree scan and pip-audit not run; no JS scan |
| 27 | Apache-2.0 & notices | COMPLETE | `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md` (31 pkgs), `SECURITY.md` | Vendored `panel/three.module.min.js` (MIT) is not in the notices table |
| 28 | SBOM | PARTIAL | `scripts/generate_sbom.py`, CycloneDX 1.5, 36 components | **Python-only.** `panel/three.module.min.js` (682 KB, MIT) absent from SBOM |
| 29 | Documentation | PARTIAL | `docs/architecture/`, `docs/architecture-en/`, `docs/architecture-ar/`, `docs/memory`, `docs/panel` | README still claims private deployment/unsupported features; no Quick Start, Docker install, mode guides, upgrade/rollback |
| 30 | Public release tree | PARTIAL | Scanner 0 findings; `.gitignore`, `.dockerignore` | No allowlist-based release builder script |
| 31 | Backup & restore | NOT STARTED | No script in `scripts/`; no `/api/backups` | Panel Backups page is placeholder copy |
| 32 | Upgrade & rollback | NOT STARTED | Migration has scoped rollback only | No product-level upgrade path or version pinning |
| 33 | Community E2E | NOT STARTED | — | Depends on Docker (23) |
| 34 | Production migration package | NOT STARTED | — | Depends on 5 + 33 |
| 35 | Publication package | NOT STARTED | — | Depends on 30 + 24 + 29 |

## Totals

| Status | Count |
|---|---|
| COMPLETE | 5 |
| PARTIAL | 20 |
| NOT STARTED | 9 |
| REGRESSED | 1 |
| OBSOLETE | 0 |
| BLOCKED | 0 |

Subsystem 8 is marked REGRESSED against the multi-group goal: it is not broken
in the single-group sense it was written for, but it actively contradicts the
tenancy model now in the tree, and it is the reason tenancy is dormant.

Nothing is BLOCKED on an external party. Docker build (23) and CI execution
(24) are environment limitations of the audit host, not blocked work.

## Documentation drift found during verification

| Claim | Reality |
|---|---|
| `NEXT_AUTONOMOUS_PROMPT.md`: tag `checkpoint/v0.1-green-suite` is `a426d1d` | Tag resolves to `74971e9` |
| Prior matrix: "MemoryService enforces tenancy" | It *can*; nothing binds it, so enforcement never runs |
| Prior matrix: provider architecture COMPLETE | No runtime integration |
| Handoff: "existing panel is legacy Persian/RTL" | Panel is `lang="en"` with no Persian text |
