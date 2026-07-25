# Release Blockers

Reconciled at `57f2693`. Not a claim of release readiness.
Full evidence: `RECONSTRUCTION_MATRIX.md`. Gate detail: `RELEASE_GATE_MATRIX.md`.

## Critical — must be fixed before anything is built on top

### B-1 Multi-group enforcement is dormant
`zero/tenancy` is complete and has 28 passing isolation tests, but nothing in
the production path uses it. `MemoryService.bind()` has no caller outside tests;
`zero/brain.py:256` constructs the service unbound, and
`zero/core/memory_service.py:44-46,55-57` deliberately skips every check when
unbound. The isolation suite therefore proves only that an unused module works.
→ **P0-1**

### B-2 Wrong-group delivery
`scripts/run_listener.py:553,561,563,565` selects `groups[0]` for the starter
loop; `scripts/run_panel.py:108,110` picks a first group the same way. Group B
receives content generated for group A. → **P0-1**

### B-3 Global cooldowns couple all groups
`last_starter_at` and `last_interject_at` are global settings
(`run_listener.py:444,558,566`, `brain.py:415`), so activity in one group
silences another. → **P0-1**

### B-4 Default credential accepted
`admin` / `Admin` passes bootstrap: `zero/panel_store.py:121` and
`zero/panel_api.py:169` both special-case that literal pair. → **P0-4**

### B-5 Sessions are process-local
`zero/panel_api.py:102` keeps sessions in a dict. A restart logs everyone out,
a second process cannot see them, and revocation cannot be enforced across
processes. → **P0-4**

### B-6 Unscoped stateful stores
Jobs, queues, outboxes, files and quotas carry no
`(installation_id, group_id)`. `zero/office/workspace.py` keys by `chat_id`
alone. Scoped memory with unscoped files still leaks. → **P0-2**

## High

### B-7 Two parallel provider systems
`zero/providers` is imported only by its own test. `zero/router.py:101,116,151,
230,247` still builds pools from `config.router.providers`. → **P0-3**

### B-8 Two config systems
`zero/config.py` (legacy runtime adapter) and `zero/configuration` (canonical)
coexist. → config completion phase

### B-9 Two auth systems
`/api/auth/*` (OTP via Telegram) and `/api/local/auth/*` (username/password)
both exist. → **P0-4**

### B-10 Migration remains incomplete
`zero/memory_v3/migration.py` maps `long_term_memory`, `medium_term_memory` and
`semantic_user_memory`. Remaining approved tables include `memory_items`,
`user_profiles`, `user_profiles_scoped`, `memory_rag_documents` and seven
`social_*` tables. → **P1-13**

### B-11 Panel advertises features it does not have
Ten of twelve pages render `generic()` placeholder copy describing capabilities
in the present tense: telegram, groups, models, web-search, memory, tools,
usage, logs, backups (settings unverified). Only dashboard and setup are real.
→ **P2-1…P2-10**

### B-12 Six Admin API surfaces missing
No `/api/groups`, `/api/backups`, `/api/telegram`, `/api/web-search`,
`/api/tools`, `/api/usage`. Endpoints are untyped and not group-scoped.
→ **P1-11**

### B-13 No Telegram adapters
Bot Mode and Hybrid Mode do not exist. User Session Mode is the legacy Telethon
listener with no portable contract, OTP/2FA flow or flood-wait handling.
→ **P1-1…P1-9**

### B-14 No backup, restore, upgrade or rollback
No script in `scripts/`, no endpoint, and the panel Backups page is placeholder
copy. → **P2-12**

### B-15 Documentation claims exceed implementation
README still describes private deployment and unsupported features. → **P2-16**

## Not verified — artifacts complete, never executed

These are **not** failures; they are unproven. Recording them as PARTIAL would
hide that no one has ever run them.

- **Docker image never built.** No Docker daemon on the audit host. The
  Dockerfile and compose file are gated only by tests that read their contents.
  → **P2-11**
- **CI never executed.** No runner. The workflow is valid YAML with five jobs.
  → **P2-14**
- **`pip-audit` never run.** Wired into CI only.
- **No live Telegram or provider call**, by design.

Expect the first CI run to change test status: the 4 POSIX-permission tests and
9 Office tests currently skipped on Windows will behave differently on Linux.

## Medium

- **B-16** Vendored `panel/three.module.min.js` (MIT, 682 KB) is absent from the
  SBOM and third-party notices. → **P0-5**
- **B-17** No allowlist release-tree builder; the scanner runs on the source
  tree, not on a built release tree. → **P2-17**
- **B-18** No interactive TUI; only the non-interactive CLI. → P2 follow-on
- **B-19** Office and Proactive lack release isolation gates. → **P3-1, P3-2**

## Closed since the previous checkpoint

Nothing. This was a planning and verification pass; no code changed.

## Corrections made during this audit

Three previously recorded statuses were overstated and are corrected in
`RECONSTRUCTION_MATRIX.md`:

- Provider architecture: COMPLETE → **PARTIAL** (no runtime integration)
- Multi-group tenancy: "enforced on MemoryService" → **enforcement dormant**
- English panel: NOT STARTED → **PARTIAL** (already English, 12 pages, real
  auth and setup; 10 pages are placeholders)

A fourth drift: `NEXT_AUTONOMOUS_PROMPT.md` claimed tag
`checkpoint/v0.1-green-suite` was `a426d1d`; it resolves to `74971e9`.

## Safety gates not executed

Production migration, credential/session rotation, permanent deletion, Git
history rewrite, public publication, Docker image or package publication.
