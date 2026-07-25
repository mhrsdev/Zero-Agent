# Zero v0.1.0-alpha — Release Gate Matrix

Reconciled at `57f2693`. A gate is PASS only with the stated evidence in hand.
"Artifact exists" is never evidence: a Dockerfile is not a verified image and a
workflow file is not a passing pipeline.

Legend — **Owner?** = needs owner approval before the action.
**Prod?** = touches production; never performed by the transformation worktree.

| # | Gate | Current state | Pass condition | Required evidence | Required tests | Owner? | Prod? | Current blocker |
|---|---|---|---|---|---|---|---|---|
| 1 | Architecture | PARTIAL | One composition root per process; no parallel duplicate systems | Import graph shows a single config system, single provider system, single auth system | Guard test: `zero/router.py` has no direct HTTP; no second auth path | no | no | Two config systems (`zero/config.py` + `zero/configuration`); two provider systems; two auth systems |
| 2 | Config / Setup | PARTIAL | Setup → persist → reload → launch proven end to end | Recorded run through all 12 setup steps producing a valid canonical config | `test_panel_setup_service.py` extended to a full-flow test | no | no | Legacy `ZeroConfig` still the runtime adapter (P0-3 area) |
| 3 | Memory V3 | **PASS** | V3 is the only normal write, read and prompt source | `test_memory_v3_only_prompt.py`, `test_memory_service_contract.py` green | present | no | no | — |
| 4 | Migration V1→V3 | PARTIAL | All approved V1 tables mapped with reconciled counts | Per-table source/target/reused/quarantined report | `test_memory_v1_to_v3_migration.py` per table | no | no | Remaining approved tables unmapped (P1-13) |
| 5 | Multi-Group | **FAIL (dormant)** | Every stateful operation carries an explicit scope | `bind()` call sites in the request path; SQL walk shows ownership predicates | Two-group adversarial suite against the *runtime*, not the module | no | no | `MemoryService.bind()` has no production caller; `groups[0]` at `run_listener.py:561-565` (P0-1, P0-2) |
| 6 | Bot Mode | NOT STARTED | Adapter passes the shared contract suite | Contract suite green against a mocked Bot transport | `zero/telegram` contract tests | no | no | No adapter module (P1-1…P1-4) |
| 7 | User Session Mode | PARTIAL | Portable adapter with OTP/2FA/flood-wait/session-replace | Contract suite + session-material-absent assertion | as above | no | no | Legacy Telethon listener only (P1-5, P1-6) |
| 8 | Hybrid Mode | NOT STARTED | Exactly one reply when both transports see one update | Dedup test under concurrent delivery | Hybrid routing + dedup suite | no | no | Depends on 6 and 7 (P1-7…P1-9) |
| 9 | Web Search | PARTIAL | External-API-only, group/permission/quota aware, `web_fetch` split | SSRF, redirect and rebinding tests; citation output | `test_web_search_architecture.py` extended | no | no | No central `WebSearchService`; single provider (P1-12) |
| 10 | Authentication | **FAIL** | No default credential; durable sessions; one mechanism | Guard test finds no literal password comparison; session survives restart | new auth suite | no | no | `admin`/`Admin` accepted at `panel_store.py:121`, `panel_api.py:169`; in-process sessions `panel_api.py:102` (P0-4) |
| 11 | Admin API | PARTIAL | Every endpoint typed, scoped, validated, redacted | Endpoint inventory with auth + scope assertions | per-endpoint suite | no | no | 6 surfaces missing; not typed; no group scoping (P1-11) |
| 12 | Panel | PARTIAL | Every page backed by a real endpoint; no placeholder copy | Page-to-endpoint map with no `generic()` renderer | per-page tests | no | no | 10 of 12 pages are `generic()` placeholders (P2-1…P2-10) |
| 13 | TUI | PARTIAL | Interactive setup, status, doctor, groups, backup | Recorded terminal session; narrow-terminal and non-interactive fallback | TUI suite | no | no | Only non-interactive CLI exists (P2 follow-on) |
| 14 | Docker | **NOT VERIFIED** | Image builds, runs non-root, persists volumes, restarts cleanly | Build log, `id -u` ≠ 0, restart with data intact | clean-container smoke | no | no | No Docker daemon on the audit host (P2-11) |
| 15 | CI | **NOT VERIFIED** | All jobs green on a real runner | Run URL with green jobs | the workflow itself | no | no | No runner available (P2-14) |
| 16 | Security | PARTIAL | Scanner clean on the *built* tree; dependency audit clean | Scanner + `pip-audit` output | release-tree scan | no | no | `pip-audit` never run; scanner runs on source not release tree (P2-17) |
| 17 | Licensing | PARTIAL | Every shipped component in the notices | Notices covering Python *and* vendored JS | release-tree test still required | no | no | Vendored JS coverage requires final release-tree audit |
| 18 | SBOM | PARTIAL | SBOM covers every shipped component | CycloneDX with Python and JS components | release-tree test still required | no | no | Generator added; JS component coverage not yet verified |
| 19 | Docs | PARTIAL | No documented feature lacks an implementation | Link check + claim-to-code audit | docs link check | no | no | README claims private deployment and unsupported features (P2-16) |
| 20 | Clean public tree | PARTIAL | Scanner passes on an allowlist-built tree | Built tree + scan output | scanner on built tree | no | no | No release-tree builder (P2-17) |
| 21 | Backup / restore | **FAIL** | Restore proven from a backup the tool itself made | Restore rehearsal into an isolated target | backup/restore suite | no | no | No script and no endpoint exist (P2-12) |
| 22 | Upgrade / rollback | **FAIL** | Upgrade then rollback preserves data | Rehearsal log | upgrade/rollback suite | no | no | No product-level path (P2-12) |
| 23 | E2E | NOT STARTED | Full scenario list passes in isolated containers | E2E run log with synthetic data only | Community E2E suite | no | no | Depends on 14 (P2-13) |
| 24 | Production migration | NOT STARTED | Package prepared, never executed | Dry-run report, backup proof, rollback rehearsal, runbook | migration suite | **yes** | **yes** | Depends on 4 and 23 (P3-3) |
| 25 | Publication | NOT STARTED | Package prepared, never published | Artifact + SHA-256, final scan, release notes | all gates above | **yes** | **yes** | Depends on 15, 19, 20 (P3-4) |

## Gate summary

- PASS: 1 (Memory V3)
- PARTIAL: 13
- FAIL: 4 (Multi-Group, Authentication, Backup/restore, Upgrade/rollback)
- NOT VERIFIED: 2 (Docker, CI) — environment-limited, not failed
- NOT STARTED: 5

**No gate may be marked PASS from a file's existence.** Gates 14 and 15 are
explicitly NOT VERIFIED rather than PARTIAL, because their artifacts are
complete but unexecuted; conflating the two is how a release ships an image
that has never run.

## Owner-approval gates

Only two gates require owner approval, and both are terminal:

- **24 Production migration** — irreversible against live data.
- **25 Publication** — irreversible once public.

Everything else can be completed by the transformation worktree without owner
interaction.
