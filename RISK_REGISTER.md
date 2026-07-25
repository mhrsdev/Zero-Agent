# Zero v0.1.0-alpha — Risk Register

Reconciled at `57f2693`. Severity is the impact if the risk materialises in a
public release. Likelihood is the chance it reaches release *given the current
tree and plan*.

| ID | Risk | Sev | Lik | Detection | Mitigation | Blocking phase | Owner decision |
|---|---|---|---|---|---|---|---|
| R-01 | **Cross-group leakage.** Tenancy is dormant: nothing binds `MemoryService`, so a scope violation cannot be raised at runtime. `zero/brain.py:256` constructs it unbound and `zero/core/memory_service.py:44-46` deliberately skips checks when unbound. | CRITICAL | HIGH | Two-group adversarial suite run against the *runtime*, not the module | P0-1 binds scope per request; P0-2 extends to stateful stores; SQL-walk test for ownership predicates | P0-1, P0-2 | no |
| R-02 | **Wrong-group delivery.** Starter and reflection loops send to `groups[0]` (`scripts/run_listener.py:561,563,565`), and `run_panel.py:108,110` picks a first group. Group B receives group A's content. | CRITICAL | HIGH | Two active groups with a starter due in each | P0-1 iterates active groups; guard test bans bare group indexing | P0-1 | no |
| R-03 | **Shared cooldown couples groups.** `last_starter_at` and `last_interject_at` are global settings (`run_listener.py:444,558,566`, `brain.py:415`), so activity in one group silences another. | HIGH | HIGH | Per-group cooldown test | P0-1 makes the keys per-group | P0-1 | no |
| R-04 | **Wrong-thread delivery.** Forum topic id must survive normalization and delivery; no adapter enforces it yet. | HIGH | MED | Two topics in one group; assert reply lands in the originating topic | P1-1 carries `thread_id` in `NormalizedEvent`; P1-9 tests it | P1-1, P1-9 | no |
| R-05 | **Hybrid duplicate responses.** Both transports observing one update would each reply. | HIGH | HIGH (if built without dedup) | Concurrent-delivery dedup test | P1-8 dedup before any Hybrid release | P1-8 | no |
| R-06 | **Default credential accepted.** `admin`/`Admin` passes bootstrap (`zero/panel_store.py:121`, `zero/panel_api.py:169`). | CRITICAL | HIGH | Guard test grepping for literal password comparison | P0-4 deletes the weak path | P0-4 | no |
| R-07 | **Session loss and non-revocable sessions.** Sessions are a process dict (`zero/panel_api.py:102`): a restart logs everyone out and a second process cannot see or revoke them. | HIGH | HIGH | Restart test asserting session survival | P0-4 persists sessions | P0-4 | no |
| R-08 | **Secret exposure.** Provider profiles reject credential-shaped refs and redact, but the legacy router path still handles raw keys, and the panel has no typed redaction contract. | CRITICAL | MED | Scanner + test asserting no response body carries a resolved secret | P0-3 routes through profiles; P1-11 enforces redaction per endpoint | P0-3, P1-11 | no |
| R-09 | **Snapshot vs real repository drift.** This tree is a sanitized single-commit snapshot of `cf00245`; the owner's real repository has history and excluded surfaces this tree never had. Merging back is not a fast-forward. | HIGH | HIGH | Compare against the owner repository before any merge | Treat this branch as a source of patches, not a replacement history; owner reconciles | before any merge to the real repo | **yes** |
| R-10 | **Migration count mismatch.** 13 of 15 V1 tables are unmapped; a partial migration that looks complete would silently drop memory. | HIGH | MED | Per-table source/target/reused/quarantined reconciliation | P1-13 maps each table with RED-first tests and count reconciliation | P1-13 | no |
| R-11 | **Rollback corruption.** Rollback soft-deletes only rows a run inserted; a mis-scoped rollback could delete pre-existing V3 rows. | CRITICAL | LOW | Existing test asserts pre-existing rows survive rollback | Keep the scoped-rollback test for every new table | P1-13 | no |
| R-12 | **Telegram API limitations.** Bot API cannot read arbitrary history; User Session can, but under different limits. Capability claims must match the transport. | MED | MED | Capability matrix test per adapter | P1-7 routes by declared capability; docs state limits per mode | P1-7, P2-16 | no |
| R-13 | **User Session flood wait.** Aggressive onboarding or polling triggers Telegram flood waits and can lock an account. | HIGH | MED | Flood-wait handling test with a simulated error | P1-5 handles flood wait explicitly with backoff; never auto-retries a login loop | P1-5 | no |
| R-14 | **Provider incompatibility.** Ollama and LM Studio are covered by the OpenAI-compatible implementation but have never been exercised against a real instance. | MED | MED | Opt-in live test against a local instance | Document as untested-against-live; keep the presets but do not claim verification | P2-16 | no |
| R-15 | **SSRF.** The transport guard was restored when the localhost allowance was removed, but `web_fetch` does not exist yet and will reintroduce the surface. | HIGH | MED | Private-address, redirect and rebinding tests | P1-12 pins the validated address to the connection | P1-12 | no |
| R-16 | **File path traversal.** `zero/office/workspace.py` keys by `chat_id` only; without installation/group ownership two tenants can collide. | HIGH | MED | Cross-tenant artifact read test | P0-2 scopes workspace paths | P0-2 | no |
| R-17 | **Docker permission issues.** Image runs as uid 10001 with a read-only root and a mounted volume; never built, so volume ownership is unproven. | MED | HIGH | First real build and restart | P2-11 builds and proves persistence | P2-11 | no |
| R-18 | **Windows/Linux portability.** Several POSIX-only assumptions were already found and fixed (`fchmod`, `statvfs`, `getloadavg`, `resource`); more may remain in untested paths. | MED | MED | CI matrix on Linux; local runs on Windows | P2-14 runs CI on Linux; keep POSIX gates explicit | P2-14 | no |
| R-19 | **Panel/backend contract drift.** 10 of 12 pages render `generic()` copy; when endpoints land, the pages may be written against a shape the API does not return. | MED | HIGH | Per-page test hitting the real endpoint | P1-11 stabilises the API *before* P2-1…P2-10 | P1-11 | no |
| R-20 | **Fake metrics / advertised-but-absent features.** The panel's placeholder pages describe capabilities in the present tense; a reader cannot tell them from working pages. | HIGH | HIGH | Page-to-endpoint map; no `generic()` renderer at release | P2-1…P2-10 replace each placeholder, or the page is removed from nav | P2-1…P2-10 | no |
| R-21 | **CI differs from local tests.** The suite has only ever run on Windows/3.11 here; CI targets Linux 3.11 and 3.12. POSIX-gated tests will *unskip* on Linux and may fail. | MED | HIGH | First CI run | P2-14 runs it and fixes what the runner reveals; expect the 4 POSIX-permission tests and 9 Office tests to change status | P2-14 | no |
| R-22 | **Third-party licensing.** Vendored `panel/three.module.min.js` (MIT, 682 KB) is absent from the SBOM and notices. | MED | HIGH | SBOM component list | P0-5 covers vendored assets | P0-5 | no |
| R-23 | **Documentation claims exceeding implementation.** README still describes private deployment and unsupported features. | HIGH | HIGH | Claim-to-code audit | P2-16 rewrites docs against the shipped feature set | P2-16 | no |
| R-24 | **Office external binary absent.** 9 tests skip without `/usr/local/lib/zero-office/officecli`; the Office path is effectively untested in CI unless the binary is provisioned. | MED | HIGH | Skip count in CI output | Keep Office disabled by default and out of the core release gate (P3-1) | P3-1 | no |
| R-25 | **Two config systems.** `zero/config.py` and `zero/configuration` coexist; a setting changed in one may not reach the other. | MED | MED | Composition-root audit | Complete the canonical conversion; single source of truth | P0-3 area / config phase | no |

## Highest-severity, highest-likelihood

R-01, R-02, R-03 and R-06 are all CRITICAL-or-HIGH with HIGH likelihood and all
resolve in the first two phases. That is the argument for the P0 ordering: the
project's four most dangerous defects are concentrated in the listener and the
auth bootstrap, and everything built on top of them inherits the bug.

## Owner decisions required

Only **R-09** (snapshot vs real repository reconciliation) requires an owner
decision before work can proceed safely. It does not block development in this
tree; it blocks *merging this tree back*.
