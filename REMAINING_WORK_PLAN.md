# Zero v0.1.0-alpha — Remaining Work Plan

Baseline: `6137075`, branch `open-source/v0.1-transformation`, tree clean,
`627 passed, 16 skipped`. Evidence for every status: `RECONSTRUCTION_MATRIX.md`.

Machine-readable twin: `REMAINING_WORK_PLAN.json`.

## How to read a phase

Every phase is one commit-sized vertical slice with its own tests and its own
rollback. Complexity is S (< ~150 lines changed), M (~150–400), L (~400–800),
XL (> 800 — only where a slice genuinely cannot be cut smaller).

`Verify` is the single command that proves the phase; it must pass before the
commit.

---

## Dependency graph

```
P0-1 listener scope enforcement ─┬─> P0-2 stateful scope (jobs/files/quotas)
                                 │
                                 ├─> P1-1 adapter contract ─┬─> P1-2 Bot inbound ─> P1-3 Bot outbound ─> P1-4 Bot health
                                 │                          ├─> P1-5 User onboarding ─> P1-6 User in/out
                                 │                          └─> (P1-2..P1-6) ─> P1-7 Hybrid routing ─> P1-8 dedup ─> P1-9 forum topics
                                 │
                                 └─> P0-3 provider wiring ─> P1-10 group model assignment

P0-4 auth consolidation ─> P1-11 typed Admin API ─┬─> P2-1..P2-10 panel pages
                                                  └─> P1-12 WebSearchService ─> P2-6 web-search page

P0-5 SBOM/licence completeness ─> P3-4 publication package
P1-13 remaining V1 tables ─> P2-12 migration package (needs P2-11 E2E)
P2-11 Docker verified ─> P2-13 Community E2E ─> P3-3 production migration package
P2-14 CI executed ─────────────┘
```

### Ordering rules this graph encodes

- Listener scope enforcement precedes Telegram adapters: writing three adapters
  against a `groups[0]` runtime would bake the bug into all three.
- Adapter contract precedes Bot/User/Hybrid: otherwise three divergent shapes.
- Auth consolidation precedes the typed Admin API: endpoints must not be built
  twice against two auth systems.
- Typed Admin API precedes panel pages: a page against an unstable API is rework.
- Provider wiring precedes per-group model assignment.
- `WebSearchService` precedes the panel Web Search page.
- Docker verified precedes clean-install E2E.
- CI executed precedes the release gate.
- Migration verification precedes any production migration package.
- Clean public tree precedes the publication package.

### Parallelisable

- **Track A** (tenancy → adapters): P0-1 → P0-2 → P1-1 → P1-2…P1-9
- **Track B** (admin surface): P0-4 → P1-11 → P2-1…P2-10
- **Track C** (release engineering): P0-5, P2-11, P2-14, P2-15, P3-1
- **Track D** (data): P1-13

A and B share only `zero/tenancy` (A writes, B reads). C is independent of A/B
except that P2-13 needs both. D is independent throughout.

---

# P0 — architecture and security blockers

Work that makes everything after it wrong or unsafe if skipped.

### P0-1 — Bind the listener to tenancy scope
- **Goal**: every listener path resolves an explicit `Scope`; delete `groups[0]`.
- **Why P0**: tenancy is dormant. Until the request path carries a scope, all
  28 isolation tests prove only that an unused module works.
- **Prereqs**: none.
- **Files**: `scripts/run_listener.py` (58, 131, 535, 553–566, 574, 640, 649),
  `zero/tenancy/registry.py`, `zero/core/memory_service.py`, `zero/brain.py:256`.
- **Schema**: tenancy tables must be created at listener start (already defined).
- **Config**: `listener.allowed_group_ids` becomes a *seed* for group discovery,
  not a runtime allowlist. Keep reading it for one release; mark deprecated.
- **Security**: this is the cross-group leakage boundary.
- **Migration**: on first start, import each `allowed_group_ids` entry as an
  ACTIVE group under the installation, so existing deployments keep working.
- **Tasks**: resolve scope per inbound message; `MemoryService.bind(scope)` per
  request; replace `groups[0]` in the starter loop with iteration over active
  groups; make `last_starter_at`/`last_interject_at` per-group keys.
- **Tests**: two active groups, a starter due in each → each receives its own and
  only its own; per-group cooldowns are independent; a message for an unknown
  chat is refused, not silently served.
- **Acceptance**: no `groups[0]` or bare `[0]` group index in
  `scripts/run_listener.py`; `grep` guard test enforces it.
- **Rollback**: single revert; tenancy tables are additive and unread by legacy code.
- **Complexity**: L · **Parallel**: no (gates Track A)

### P0-2 — Extend scope to stateful subsystems
- **Goal**: jobs, queues, outboxes, files, quotas, usage carry
  `(installation_id, group_id)`.
- **Why P0**: a scoped memory with unscoped files still leaks across groups.
- **Prereqs**: P0-1.
- **Files**: `zero/office/db.py`, `zero/office/workspace.py`,
  `zero/proactive_*.py`, `zero/storage.py`.
- **Schema**: additive columns + a backfill for existing rows to the
  default installation. Ownership predicates on every read.
- **Security**: file-path traversal and cross-group artifact reads.
- **Tests**: two groups create same-named artifacts; neither can read the other's;
  quota consumed in group A leaves group B's untouched.
- **Acceptance**: every stateful read has an ownership predicate — verified by a
  test that walks the SQL in `zero/storage.py`.
- **Rollback**: columns are additive and nullable; revert restores prior queries.
- **Complexity**: XL · **Parallel**: no

### P0-3 — Wire `zero/providers` into the runtime router
- **Goal**: `IndependentRouter` delegates to `ProviderRegistry`.
- **Why P0**: two provider systems will diverge; per-group models cannot land on
  the legacy one.
- **Prereqs**: none (P0-1 recommended first for scope-aware selection).
- **Files**: `zero/router.py:97-247`, `zero/providers/`, `zero/config.py`.
- **Config**: `router.providers.*` maps onto `ProviderProfile`; keep the legacy
  YAML shape and translate at load.
- **Tests**: existing router tests must pass unchanged against the new backend;
  add a test that the legacy config shape produces the expected profiles.
- **Acceptance**: `zero/router.py` contains no direct HTTP call.
- **Rollback**: keep the legacy path behind a flag for one phase, then delete.
- **Complexity**: L · **Parallel**: yes (Track A/C independent)

### P0-4 — Consolidate authentication
- **Goal**: one auth system; no weak-password path; sessions survive restart.
- **Why P0**: `admin`/`Admin` is an accepted credential
  (`zero/panel_store.py:121`, `zero/panel_api.py:169`), and sessions live in a
  process dict (`zero/panel_api.py:102`), so every restart logs everyone out and
  a second process cannot share them.
- **Prereqs**: none.
- **Files**: `zero/panel_api.py`, `zero/panel_store.py`.
- **Schema**: `panel_sessions` table (id, user, csrf, created, expires, ip hash).
- **Security**: removes the documented default credential; adds durable session
  revocation and audit.
- **Tests**: bootstrap refuses a weak password; sessions survive a restart;
  revocation is immediate; the OTP path is either removed or reduced to one
  documented mechanism.
- **Acceptance**: no string comparison against a literal password anywhere;
  guard test greps for it.
- **Rollback**: revert; schema addition is additive.
- **Complexity**: M · **Parallel**: yes (gates Track B)

### P0-5 — Complete the SBOM and notices across languages
- **Goal**: `panel/three.module.min.js` (682 KB, MIT) appears in the SBOM and the
  third-party notices.
- **Why P0**: shipping a vendored MIT library absent from the bill of materials
  is a licence-compliance defect, and it is cheap to fix now.
- **Prereqs**: none.
- **Files**: `scripts/generate_sbom.py`, `THIRD_PARTY_NOTICES.md`.
- **Tests**: SBOM contains a component for every file under `panel/` that ships a
  licence header; notices list it.
- **Acceptance**: `test_release_artifacts.py` asserts JS assets are covered.
- **Rollback**: trivial revert.
- **Complexity**: S · **Parallel**: yes

---

# P1 — core release requirements

### P1-1 — `TelegramAdapter` contract and event normalization
- **Goal**: one protocol both transports satisfy; a `NormalizedEvent` carrying
  chat, thread, sender, reply, media and scope.
- **Prereqs**: P0-1.
- **Files**: new `zero/telegram/` (contract, events, test transport).
- **Tests**: a recorded-fixture transport; normalization of group, private,
  mention, reply, command, media, document and forum-topic events.
- **Acceptance**: no adapter imports `telethon` or `aiogram` outside its own module.
- **Complexity**: M · **Parallel**: no (gates P1-2…P1-9)

### P1-2 — Bot Mode inbound · P1-3 outbound · P1-4 health
- Split so inbound normalization lands before send paths, and health/permission
  diagnostics land last.
- **Prereqs**: P1-1.
- **Tests**: mocked Bot API transport only; no live token. Correct thread on
  reply; command parsing; media download bounds; permission diagnostics report
  missing rights rather than failing silently.
- **Complexity**: M · M · S · **Parallel**: sequential within, parallel to Track B

### P1-5 — User Session onboarding · P1-6 inbound/outbound
- Onboarding covers API ID/hash, phone, send OTP, verify OTP, optional 2FA,
  invalid/expired code, flood wait, session replacement and local revoke.
- **Security**: session material must never reach an API response, a log or the
  panel. Test asserts it.
- **Complexity**: L · M

### P1-7 Hybrid routing · P1-8 deduplication · P1-9 forum topics
- Capability router picks a transport per capability with a declared fallback;
  dedup guarantees exactly one reply when both transports see one message.
- **Tests**: both transports deliver the same update → exactly one response;
  destination and thread are correct.
- **Complexity**: M · M · S

### P1-10 — Per-group provider assignment
- **Prereqs**: P0-2, P0-3.
- Groups select a profile name; never a credential.
- **Complexity**: S

### P1-11 — Typed Admin API
- **Prereqs**: P0-4.
- Typed request/response models, group scoping, validation and redaction on
  every endpoint; add the six missing surfaces (`groups`, `backups`, `telegram`,
  `web-search`, `tools`, `usage`).
- **Tests**: every endpoint rejects unauthenticated and cross-group access;
  no response contains a resolved secret.
- **Complexity**: XL — split per resource group if a session cannot hold it.
- **Parallel**: yes (Track B)

### P1-12 — `WebSearchService`
- Central service; `web_search` and `web_fetch` separated; group/permission/quota
  aware; canonical URL, tracking-parameter stripping, citations; SSRF, private
  network rejection, redirect validation, DNS-rebinding pinning.
- **Complexity**: L

### P1-13 — Remaining V1 tables
- 13 tables remain. Suggested order: `memory_items`, `user_profiles_scoped`,
  `user_profiles`, `memory_rag_documents`, then `social_*`.
- **Policy**: RAG/archive content must not become durable memory automatically —
  quarantine anything whose scope or ownership is ambiguous.
- **Tests per table**: RED first, rollback, resume, count reconciliation,
  quarantine coverage.
- **Complexity**: M per table · **Parallel**: yes (Track D)

---

# P2 — release hardening

- **P2-1…P2-10 — Panel pages**, one page per phase, each against a real endpoint:
  telegram, groups, models, web-search, memory, tools, usage, logs, backups,
  settings. Each replaces a `generic()` placeholder. A page ships only when its
  endpoint exists and every control it renders performs a real action.
  **Prereqs**: P1-11. **Complexity**: S–M each.
- **P2-11 — Docker build verified** on a host with a daemon: build, clean-install
  smoke, non-root assertion, volume persistence across restart. **L**
- **P2-12 — Backup / restore / upgrade / rollback**: script + `/api/backups` +
  panel page; restore rehearsal into an isolated target. **L**
- **P2-13 — Community E2E**: isolated containers, volumes, ports, synthetic
  users and groups; the full scenario list. **Prereqs**: P2-11. **XL**
- **P2-14 — CI executed** on a real runner; fix what the runner reveals. **M**
- **P2-15 — Responsive / accessibility pass** on the panel. **M**
- **P2-16 — Documentation set**: README rewrite plus Quick Start, Docker install,
  three mode guides, Multi-Group, Memory V3, migration, providers, web search,
  panel, TUI, security, privacy, retention, backup/restore, configuration,
  troubleshooting, development, testing, contributing, upgrade, rollback,
  release guide. **L**
- **P2-17 — Allowlist release-tree builder** + artifact scan on the built tree. **M**

---

# P3 — optional / experimental

- **P3-1 — Office isolation gate**: disabled by default, group workspace
  ownership, cancellation, delivery receipts, licence review. Must not block core.
- **P3-2 — Proactive isolation gate**: explicit consent, opt-out, quiet hours,
  per-group/thread outbox ownership, duplicate prevention.
- **P3-3 — Production migration package**: dry-run report, backup proof, rollback
  rehearsal, owner runbook. **Prereqs**: P1-13, P2-13. Prepared only; never run.
- **P3-4 — Publication package**: final scan, SBOM, notices, release notes,
  artifact + SHA-256, owner commands. Prepared only; never published.
