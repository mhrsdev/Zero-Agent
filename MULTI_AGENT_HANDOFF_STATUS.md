# Zero Multi-Agent Handoff Status

## Snapshot

- Repository: Zero
- Branch: `open-source/v0.1-transformation`
- HEAD at handoff preparation: `53b487a5f009d98e40c89fdeca6316c4a0218c13`
- Working tree before this handoff file: clean
- `main`: untouched
- Production: untouched; no live service, Telegram session, credential, production database, or migration was used

## Real status

| Area | Status | Evidence / boundary |
|---|---|---|
| Opus checkpoint reconciliation | COMPLETE for the integrated slices | Tenancy primitives, provider registry, release infrastructure, and planning reconciliation were applied with adaptation where required |
| P0-1 listener tenancy binding | PARTIAL | Listener scope resolution, active-group discovery, group-scoped starter/interject cooldowns, and first-group routing removal are committed; full runtime isolation is not proven |
| P0-2 runtime isolation | NOT COMPLETE | No verified end-to-end ownership enforcement across delivery, jobs, outbox, files, artifacts, quotas, document bundles, panel, and transport |
| Multi-Group end-to-end | PARTIAL | Tenancy primitives exist; adversarial two-group/two-thread runtime call-graph evidence is still missing |
| Authentication unification | NOT STARTED | Existing authentication/setup code is not a verified unified production contract |
| Provider runtime integration | PARTIAL | Normalized provider registry exists; production router still has legacy wiring |
| External API-only Web Search | PARTIAL | Architecture/tests exist, but complete group-aware permission/quota/SSRF-safe production wiring is not verified |
| Telegram adapter/modes | NOT STARTED | Bot/User/Hybrid contract is not proven as one runtime |
| Admin API/panel/TUI | PARTIAL | Existing panel and auth surfaces exist; typed group-scoped control-plane integration is not complete |
| Release Candidate | NOT READY | Critical runtime, artifact, CI, dependency, clean-install, rollback, and E2E gates remain open |

## Work completed before this handoff

- Reconciled the correct Opus snapshot against the real repository instead of overwriting the tree.
- Added/adapted scoped tenancy primitives and `MemoryService` tenancy contracts.
- Added normalized provider registry primitives.
- Added release infrastructure, Docker/CI definitions, SBOM generator, Apache-2.0 materials, `NOTICE`, and lockfile.
- Recorded reconciliation and test status in the planning/tracking documents.
- Bound the listener to group tenancy and removed implicit first-group routing from listener/panel paths addressed by P0-1.
- Converted starter/interject cooldown keys to group-scoped keys in the committed P0-1 slice.

## Verification last recorded at HEAD

- Full suite: `652 passed, 1 skipped`
- Targeted tenancy/memory/migration suites: green in the recorded checkpoint
- Compile: `python -m compileall -q zero scripts` passed
- `git diff --check`: passed
- Ruff: not available on the host; not reported as passed
- Public artifact scan on the real checkout: not a valid release result because the checkout contains private historical/deployment material; a clean allowlist-built tree is required
- Strong secret-pattern scan at the recorded checkpoint: passed

## P0-2 first task after handoff

Write and run RED adversarial tests before implementation for:

1. two groups and two forum threads with one user;
2. concurrent claims and duplicate delivery attempts;
3. wrong-group and wrong-thread destinations;
4. missing installation/group/thread context;
5. restart and lease recovery;
6. explicit ownership for Office jobs/files/generated artifacts;
7. explicit ownership for proactive candidates/outbox/transport;
8. group/thread-owned document bundles, quota reservations, and panel state.

Then trace and repair the real caller → stateful persistence → outbox → transport path. Missing ownership must fail closed before filesystem/database side effects. Do not add `legacy`, `candidate:<id>`, `0`, first-group, first-thread, or chat-derived runtime fallbacks. Update synthetic fixtures and callers to pass explicit scope.

## Handoff safety rules

- Work only on `open-source/v0.1-transformation` or an isolated worktree.
- Do not modify `main` or production.
- Do not use real credentials, sessions, databases, user/group data, or live Telegram/provider calls.
- Keep migrations limited to copied synthetic databases.
- Do not call P0-2 complete until targeted tests, full suite, compile/diff, ownership call-graph evidence, clean tree, focused commit, tracking update, verified bundle, and SHA-256 all exist.

## Restore and continue

```bash
unzip zero-multi-agent-handoff-<HEAD>-<timestamp>.zip -d handoff
cd handoff/repository

git bundle verify ../git/zero-open-source-v01.bundle
# The extracted source is a snapshot. To restore Git history:
git clone . ../restored-zero
cd ../restored-zero
git fetch ../git/zero-open-source-v01.bundle 'refs/heads/*:refs/remotes/handoff/*'
git checkout -b open-source/v0.1-transformation handoff/open-source/v0.1-transformation

python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

The first task is P0-2 RED adversarial isolation tests, not a later Batch 1 milestone.
