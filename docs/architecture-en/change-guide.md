# Developer Change Guide

## Before changing anything

1. Read the overview and the relevant runtime-flow document.
2. Trace imports and call sites of the target symbol; file names are not enough.
3. Read the configuration source, service unit, and boundary tests.
4. For DB/state changes, define schema, migration, lock/transaction, backup, and rollback behavior.
5. For Telegram/provider changes, preserve identity/account scope, dedup, rate limits, and secret policy.

## Where to change

- New setting: `zero/config.py`, the public example/env file, and validation tests.
- Reply behavior: inspect `brain.py`, `prompts.py`, `triggers.py`, and listener call sites.
- Provider/fallback/key policy: `router.py` and router tests.
- Memory: the specific layer plus `memory_context.py`; do not silently cross layers.
- Telegram events: `run_listener.py`; preserve dedup, allowlist scope, and self-message behavior.
- Panel: composition in `run_panel.py`, routes/auth/security in `panel_api.py`, UI in `panel/`.
- Office: command gate → intake/preflight → DB quota/job → planner/adapter/worker/delivery. Never let a general intent detector bypass the gate.
- Scheduled behavior: listener loop plus the corresponding proactive/template service; test claim/outcome semantics.

## Do not bypass without design/tests

- Identity `(chat_id, sender_id)` or dedup scope.
- Secret storage and redaction boundaries.
- `ZeroStore`/`OfficeRepository` for quota and job state.
- Structured adapter boundaries with arbitrary shell commands.
- Feature flags, health checks, and rollback for Office/search.

## Verification gate

```text
unit boundary
 → security/identity/concurrency tests
 → focused integration
 → full tests
 → import/AST check
 → inspect service/config state
 → staging health/restart check
 → documentation update
```

With no `.git`, use filesystem manifests/checksums and verified backups instead of Git diffs, and report that limitation explicitly.

## Rollback

Every config/service/DB change needs a rollback. For runtime, disable the feature first, stop its worker, reload/restart the service, and verify health. For migrations, use a verified backup. This is a general gate, not a replacement for a subsystem-specific runbook.