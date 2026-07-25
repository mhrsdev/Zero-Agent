# Zero v0.1.0-alpha — Session Execution Plan

Sized against observed capacity. The session that produced `6137075` completed
roughly **one L phase plus two S/M phases** before context ran out, including
tests, tracking updates and a checkpoint. Sessions below are sized to that, not
to an ideal.

Every session: start clean → RED test first → implement → targeted tests → full
suite → `ruff check` → `git diff --check` → normalize CRLF → update tracking →
commit → clean tree → rewrite `NEXT_AUTONOMOUS_PROMPT.md`.

**Completion gate** = what must be true to call the session done.
**Continuation point** = what the next session starts on.

---

## S1 — Listener tenancy enforcement  ·  P0-1

- **Start**: `checkpoint/v0.1-release-infra` (`6137075`)
- **Goal**: the request path carries an explicit scope; `groups[0]` is gone.
- **Tasks**: guard test banning bare group indexing → scope resolution per
  inbound message → `MemoryService.bind()` per request → per-group
  `last_starter_at` / `last_interject_at` → seed groups from `allowed_group_ids`.
- **Expected commits**: 2–3 (guard + scope; cooldowns; seed import)
- **Tests**: two-group starter isolation; independent cooldowns; unknown chat
  refused; guard test green
- **Gate**: no `groups[0]` in `scripts/`; full suite green; tenancy tests now
  exercise the runtime, not just the module
- **Continuation**: P0-2, stateful scope

## S2 — Stateful scope, part 1: office and workspace  ·  P0-2 (a)

- **Start**: S1 checkpoint
- **Goal**: office jobs and workspaces carry `(installation_id, group_id)`.
- **Expected commits**: 2 (schema + backfill; predicates + path scoping)
- **Tests**: two groups, same-named artifact, no cross-read; backfill idempotent
- **Gate**: SQL walk finds no unscoped office read
- **Continuation**: P0-2 (b)

## S3 — Stateful scope, part 2: proactive, quotas, usage  ·  P0-2 (b)

- **Start**: S2 checkpoint
- **Expected commits**: 2
- **Tests**: quota consumed in A leaves B untouched; outbox ownership
- **Gate**: SQL-walk guard test covers every stateful table
- **Continuation**: P0-4 (Track B opens) or P0-3

## S4 — Auth consolidation  ·  P0-4  +  P0-5

- **Start**: S3 checkpoint (or parallel from S1 — Track B is independent)
- **Goal**: no default credential; durable sessions; SBOM covers vendored JS.
- **Expected commits**: 3–4
- **Tests**: weak password refused; session survives restart; revocation
  immediate; SBOM contains the JS component
- **Gate**: guard test finds no literal password comparison anywhere
- **Continuation**: P1-11 typed Admin API

## S5 — Provider wiring  ·  P0-3

- **Start**: any checkpoint after S1 (parallel-safe)
- **Expected commits**: 3 (translation; delegation; remove legacy path)
- **Tests**: existing router tests unchanged; legacy config maps to profiles
- **Gate**: `zero/router.py` has no direct HTTP call
- **Continuation**: P1-1 adapter contract

## S6 — Telegram adapter contract  ·  P1-1

- **Start**: S1 checkpoint or later
- **Expected commits**: 1–2
- **Tests**: normalization across group, private, mention, reply, command,
  media, document, forum topic
- **Gate**: no `telethon`/`aiogram` import outside an adapter module
- **Continuation**: P1-2 Bot inbound

## S7 — Bot Mode  ·  P1-2, P1-3, P1-4

- **Expected commits**: 3
- **Tests**: mocked transport only; thread-correct replies; send results
  checked; permission diagnostics name the missing right
- **Gate**: Bot adapter passes the shared contract suite
- **Continuation**: P1-5

## S8 — User Session onboarding  ·  P1-5

- **Expected commits**: 2–3
- **Tests**: OTP, 2FA, invalid code, expired code, flood wait, session replace,
  local revoke; session material absent from every surface
- **Gate**: a test proves session strings never reach a response or log
- **Continuation**: P1-6

## S9 — User Session in/out + Hybrid routing  ·  P1-6, P1-7

- **Expected commits**: 2–3
- **Gate**: both adapters pass one contract suite; routing decisions logged
- **Continuation**: P1-8

## S10 — Hybrid dedup + forum topics  ·  P1-8, P1-9

- **Expected commits**: 2
- **Tests**: both transports see one update → exactly one reply; topic routing
- **Gate**: no duplicate reply under concurrent delivery
- **Continuation**: P1-10, then Track B

## S11–S13 — Typed Admin API  ·  P1-11 (split by resource group)

- **S11**: auth, setup, dashboard, health
- **S12**: telegram, groups, models
- **S13**: web-search, memory, tools, usage, logs, backups, settings
- **Expected commits**: 3–4 each
- **Tests**: per endpoint — unauthenticated rejected, cross-group rejected, no
  secret in any response
- **Gate**: every endpoint typed and scoped; endpoint inventory test green
- **Continuation**: panel pages

## S14 — WebSearchService  ·  P1-12

- **Expected commits**: 2–3
- **Tests**: private address rejected; redirect to private rejected; validated
  address pinned; tracking parameters stripped; per-group quota
- **Gate**: `web_search` and `web_fetch` are separate permissions
- **Continuation**: P2-6

## S15–S17 — Panel pages  ·  P2-1…P2-10

Three sessions, roughly three to four pages each, in dependency order:

- **S15**: telegram, groups, models
- **S16**: memory, tools, web-search, usage
- **S17**: logs, backups, settings, then P2-15 responsive/accessibility

- **Gate per page**: renders from a real endpoint; every control performs a real
  action; no `generic()` renderer remains
- **Continuation**: TUI, then release engineering

## S18 — Remaining V1 tables  ·  P1-13

Parallel-safe with any session from S4 onward. Likely two sessions:

- **S18a**: `memory_items`, `user_profiles_scoped`, `user_profiles`
- **S18b**: `memory_rag_documents`, `social_*`
- **Gate**: per-table count reconciliation; RAG/archive never auto-promoted

## S19 — Docker verified + CI executed  ·  P2-11, P2-14

- **Requires a host with a Docker daemon and a CI runner.** Cannot be done on
  the current audit host.
- **Gate**: build log, `id -u` ≠ 0, restart with data intact, five green CI jobs
- **Continuation**: P2-12

## S20 — Backup / restore / upgrade / rollback  ·  P2-12

- **Gate**: restore proven from a backup the tool itself produced

## S21 — Documentation + release tree  ·  P2-16, P2-17

- **Gate**: no documented feature lacks an implementation; scanner passes on the
  *built* tree

## S22 — Community E2E  ·  P2-13

- **Requires S19.**
- **Gate**: full scenario list green in isolated containers with synthetic data

## S23 — Packages  ·  P3-3, P3-4

- Prepared only. **Never executed, never published.** Owner approval required
  for both.

---

## Realistic shape

- **S1–S5** (P0): five sessions, and until they land nothing built on top is
  trustworthy.
- **S6–S10** (Telegram): five sessions.
- **S11–S17** (API + panel): seven sessions.
- **S18–S23**: six sessions, two of which need infrastructure this host lacks.

**≈ 23 sessions** to a genuine Release Candidate, assuming no significant
rework. Tracks A, B, C and D can be interleaved by separate agents after S1;
S1 must complete first because every other track inherits its scope model.

## What cannot be done on the current host

- Docker build and clean-install smoke (no daemon)
- CI execution (no runner)
- Any live Telegram or provider call (no credentials, by design)

These are the only hard environmental limits. Everything else in this plan is
executable in the current environment.
