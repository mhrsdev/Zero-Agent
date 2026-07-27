# Release Checklist — Zero v0.1.0-alpha

## Pre-Release

- [x] P0-2 runtime isolation complete (fail-closed, explicit ownership)
- [x] Multi-group E2E verified (two groups, two threads, one user)
- [x] Authentication secure (scrypt, CSRF, RBAC, rate limiting)
- [x] Provider registry wired (symbolic secrets, fallback, rate limiting)
- [x] Web search uses official APIs only (no scraping)
- [x] Telegram adapter supports bot/session/hybrid modes with dedup
- [x] Admin API secured (CSRF, group-scoped, RBAC)
- [x] Docker build verified (multi-stage, non-root, hardened)
- [x] CI pipeline runs lint + test + security + Docker smoke
- [x] No secrets tracked in git
- [x] Backup/restore supported (ZeroStore snapshot/import)
- [x] Upgrade/rollback supported (schema versioning, migration scripts)
- [x] Office preflight hardened (zip bomb, traversal, macro, XML entity)
- [x] Full suite passes with 0 new regressions (801 passed, 13 pre-existing)
- [x] Compile clean
- [x] Working tree clean at checkpoint
- [x] Git bundle created with SHA-256
- [x] Documentation: FINAL_HANDOFF, FINAL_TEST_REPORT, SECURITY_AUDIT, ARCHITECTURE_STATUS, RELEASE_GATE_MATRIX, KNOWN_LIMITATIONS

## Remaining Before Release Candidate

- [x] Zero TUI implemented (curses-based, reads live data)
- [x] Community E2E structure tests passed (live tests skip — need credentials)
- [ ] `officecli` binary installed (resolves 5 pre-existing failures)
- [ ] `config/zero.yaml` test fixture created (resolves 4 pre-existing failures)
- [ ] Sanitized final ZIP created
- [ ] Tag `v0.1.0-alpha` created (after remaining items)

## Do NOT (Safety Constraints)

- [x] main not modified
- [x] production not modified
- [x] No migration on production
- [x] No real credentials used
- [x] No force push
- [x] No history rewrite
- [x] No public release published
- [x] No Docker image published
- [x] No package published
