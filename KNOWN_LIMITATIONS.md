# Known Limitations — Zero v0.1.0-alpha

## Environmental (Test Environment)

1. **`officecli` binary not installed** — 5 tests fail because `/usr/local/lib/zero-office/officecli` does not exist. These are real integration tests that require the Office rendering binary.
2. **`config/zero.yaml` not present** — 4 tests fail because the test environment lacks a valid `zero.yaml` configuration file. These tests work when the config exists.
3. **Live web/Telegram tests** — 3 tests require network access and `ZERO_LIVE_E2E=1` environment variable. They are opt-in by design.
4. **Memory corpus minimum** — 1 test checks for a minimum number of corpus items that the test environment doesn't have.

## Not Yet Implemented

5. **Zero TUI** — No terminal UI module exists. The panel (web-based dashboard) is the primary admin interface.
6. **Community E2E** — End-to-end testing with multiple real Telegram users and groups requires live credentials and cannot be done in isolation.
7. **`proactive_followups` table** — Does not yet have `installation_id`/`group_id` columns in the base schema (P0-2 handles this via ALTER TABLE migration). The `reserve()` call passes scope, but the source followup row doesn't carry scope yet.

## Design Decisions

8. **Auth is unified** — The panel API has both a "production" path (`_request_code`/`_verify` with Telegram) and a "local" path (`_local_bootstrap`/`_local_login`). Both are secured with the same session/CSRF infrastructure.
9. **Provider secrets are symbolic** — Raw API keys (prefixed `sk-`, `xoxb-`, `ghp_`) are rejected in `ProviderProfile.secret_ref`. Secrets are loaded at runtime via `secret_resolver`.
10. **Web search uses RSS/JSON only** — No HTML scraping. Bing RSS and SearXNG JSON are the only supported providers.
11. **Docker is hardened** — Multi-stage build, non-root user, secrets as volumes (not baked), loopback binding, `no-new-privileges`, `cap_drop: ALL`.
12. **CI runs on GitHub Actions** — lint (ruff), test (pytest matrix), security scan, Docker smoke build.

## Pre-Existing (From Before This Session)

13. **instrumentation.ts auto-start** — (From NewsBot project, not Zero) pollers/listener auto-start fails in next dev mode. Non-fatal.
14. **Memory v2 corpus** — The v2 corpus has fewer items than the regression test expects. The v3 migration is complete but the corpus hasn't been populated.
