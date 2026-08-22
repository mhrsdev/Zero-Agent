# Autonomous Zero Transformation Continuation

Reconciled from the Opus execution plan produced at `6137075` and the real
repository checkpoint `57f2693`. Read this file, then
`REMAINING_WORK_PLAN.md` and `SESSION_EXECUTION_PLAN.md`. You need no chat
history.

## Exact checkpoint

- Repository: `/opt/zero`
- Branch: `open-source/v0.1-transformation`
- HEAD: `bb8d56d` (`feat: bind listener requests to group tenancy`)
- Other tag: `checkpoint/v0.1-green-suite` → `74971e9` (first fully green suite)
- Original snapshot baseline: `578b56c` (sanitized snapshot of `cf00245`)
- Working tree: clean at this checkpoint
- Production, `main`, credentials and sessions: untouched

## Environment

```bash
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
# Windows only: pip install tzdata
python -m pytest -q -p no:cacheprovider
```

Release-gate extras: `ruff`, `pip-licenses`, `pip-tools`, `pip-audit`.

## Verified at this commit

```
652 passed, 1 skipped, 0 failed, 0 collection errors
```

`compileall` exit 0 · `git diff --check` passed. Ruff was not installed on the
real host. The source-tree artifact scanner is not a release gate here because
the real repository retains private historical files; it must run on an
allowlist-built public tree.

## Never executed anywhere

- **Docker image has never been built** (no daemon on the audit host)
- **CI has never run** (no runner)
- **`pip-audit` has never run**
- No live Telegram or provider call, by design

Do not report any of these as verified until they have actually run.

## Read the plan before starting

| File | Purpose |
|---|---|
| `RECONSTRUCTION_MATRIX.md` | All 35 subsystems with evidence |
| `REMAINING_WORK_PLAN.md` | Phases, dependency graph, P0–P3 |
| `REMAINING_WORK_PLAN.json` | Machine-readable twin |
| `RELEASE_GATE_MATRIX.md` | 25 gates with pass conditions |
| `RISK_REGISTER.md` | 25 risks with mitigations |
| `SESSION_EXECUTION_PLAN.md` | ~23 sessions, S1 first |
| `RELEASE_BLOCKERS.md` | B-1…B-19 |

## Do not trust these earlier claims

Verified false at the Opus checkpoint and still false after reconciliation:

- "Provider architecture COMPLETE" — `zero/providers` now exists, but is still
  not wired into the production router; `zero/router.py` still uses legacy config.
- "MemoryService enforces tenancy" — it *can*, but `bind()` has no production
  caller, and an unbound service skips every check.
- "Existing panel is legacy Persian/RTL" — it is `lang="en"` with no Persian
  text and all 12 required pages.

## Current atomic task — P0-1

**Bind the listener to tenancy scope and delete `groups[0]`.**

Confirmed against the real source at `bb8d56d`:

| Site | Problem |
|---|---|
| `scripts/run_listener.py:553` | `groups = list(config.listener.allowed_group_ids) or await store.get_active_group_chat_ids()` |
| `scripts/run_listener.py:561,563,565` | `awareness.allow_action(groups[0], …)`, `brain.maybe_starter(groups[0])`, `client.send_message(groups[0], …)` |
| `scripts/run_panel.py:108,110` | `allowed_group_ids[0]` then `active[0]` |
| `scripts/run_listener.py:444,558,566` and `zero/brain.py:415` | `last_starter_at` / `last_interject_at` are **global** settings, so one group's activity silences another |
| `zero/brain.py:256` | `MemoryService(self.memory_v3)` — unbound, so enforcement never runs |

### Order of work

1. **RED first**: a guard test that fails on any bare group index in `scripts/`,
   and an adversarial test with two active groups each having a starter due,
   asserting neither receives the other's message.
2. Resolve a `Scope` per inbound message via `TenancyRegistry`.
3. Bind memory per request: `service.bind(scope, registry)`.
4. Replace `groups[0]` with iteration over active groups.
5. Make `last_starter_at` / `last_interject_at` per-group keys.
6. Import `allowed_group_ids` entries as ACTIVE groups on first start so
   existing deployments keep working; mark the field deprecated.

### Files

`scripts/run_listener.py` · `scripts/run_panel.py` · `zero/brain.py` ·
`zero/tenancy/registry.py` · `zero/core/memory_service.py` ·
`tests/test_tenancy_isolation.py`; provider/release infrastructure commits are
already present and are not part of P0-1.

### Acceptance

No `groups[0]` or bare group index remains in the listener/panel source; full
suite is green. Runtime call-graph E2E for wrong-thread delivery and all
stateful subsystems is still open, so this task remains PARTIAL.

### Rollback

Single revert. Tenancy tables are additive and unread by legacy code.

## Then

S2–S3 (`P0-2` stateful scope) → S4 (`P0-4` auth + `P0-5` SBOM) →
S5 (`P0-3` provider wiring) → S6 (`P1-1` adapter contract) → see
`SESSION_EXECUTION_PLAN.md`.

Track B (`P0-4` → `P1-11` → panel) and Track D (`P1-13` migration) can run in
parallel with Track A **after S1**. S1 must land first: every other track
inherits its scope model.

## Safety boundaries

- Work only on `open-source/v0.1-transformation` or an isolated worktree.
- Never modify `main`, production source, config, services, databases,
  sessions, credentials, queues, panel or group data.
- Never restart production, migrate production, or make live Telegram/provider
  calls.
- Development migrations use copied synthetic databases only.
- Do not publish, force-push, rewrite history, rotate credentials, revoke
  sessions, permanently delete V1/V2 artifacts, or change repository visibility.
- Keep secrets symbolic and redacted. Test fixtures must read as obvious
  examples so the artifact scanner stays meaningful.
- If a secret or production datum appears: stop work on it, do not print its
  value, isolate and report it.
- Production migration (`P3-3`) and publication (`P3-4`) are prepared only and
  require owner approval.

## Checkpoint discipline

RED test first → implement minimally → targeted tests → full suite →
`ruff check` → `git diff --check` → normalize CRLF (Python's `write_text` emits
CRLF on Windows unless given an explicit newline) → update
`TRANSFORMATION_STATUS.md`, `TRANSFORMATION_JOURNAL.json`,
`MIGRATION_STATUS.md`, `TEST_STATUS.md`, `RELEASE_BLOCKERS.md`,
`RELEASE_CHECKLIST.md`, `SECURITY_FINDINGS.md`, `RECONSTRUCTION_MATRIX.md` and
this prompt → commit → verify a clean tree.

Never weaken a test to make it pass. If a test cannot run in an environment,
gate it on the real prerequisite with an explicit reason and keep the
production check strict. Never mark a subsystem COMPLETE when its module is
imported only by its own test.
