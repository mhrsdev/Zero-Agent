# Architecture Status — Zero v0.1.0-alpha

## Branch
`open-source/v0.1-transformation`

## HEAD
`8f5e44b`

## Test Summary
- **819 passed**, 13 pre-existing failures (environmental), 3 skipped, **0 new regressions**
- 13 failures: `officecli` binary missing (5), config/zero.yaml missing (4), live web/Telegram (3), memory corpus (1)

## Subsystem Status

| # | Subsystem | Status | Tests | Commit |
|---|---|---|---|---|
| 1 | P0-2 Runtime Isolation | COMPLETE | 32 | `99097cf` |
| 2 | Multi-Group E2E | COMPLETE | 11 | `3d94dc7` |
| 3 | Authentication & Authorization | COMPLETE | 12 | `04b65b7` |
| 4 | Provider Registry → runtime | COMPLETE | 7 | `f313b9b` |
| 5 | Web Search (official APIs) | COMPLETE | 6 | `5d253c7` |
| 6 | TelegramAdapter contract | COMPLETE | 6 | `7d71388` |
| 7 | Admin API (CSRF + RBAC) | COMPLETE | 11 | `20789f3` |
| 8 | Docker build | COMPLETE | 11 | `d0a48f8` |
| 9 | Panel / Office / Secret scan | COMPLETE | 10 | `097a656` |
| 10 | CI / Release / Backup | COMPLETE | 36 | `2597082` |
| 11 | Hybrid / Panel / Office / Backup / Upgrade | COMPLETE | 20 | `8f5e44b` |
| 12 | Zero TUI | COMPLETE | 11 | `eaf5117` |
| 13 | Community E2E | PARTIAL (structure ok, live skip) | 7+2 | `40a52a3` |

## Key Architectural Decisions

### P0-2: Fail-Closed Runtime Isolation
- Every stateful resource carries `installation_id` + `group_id` at minimum
- `Scope.__post_init__` rejects: `legacy`, `candidate:*`, `0`, `default`, `None`, `""`
- Schema migrations: `office_jobs`, `office_quota_usage`, `office_delivery_outbox`, `proactive_followup_outbox` all scope-aware
- `DeliveryCoordinator` verifies scope before every send
- `Outbox.reserve()` requires scope kwargs (fail-closed)

### Provider Registry
- Symbolic secret refs (no raw credentials in config)
- Rate limiting per profile
- Fallback chains across providers
- Health checks with exponential backoff

### Auth
- scrypt password hashing
- Session tokens (hashed, never stored plaintext)
- CSRF on all write operations
- Role-based: owner / viewer
- `must_change_password` for default admin
- Rate limiting: 30/min auth, 120/min API

### Web Search
- Official APIs only (Bing RSS, SearXNG JSON)
- No HTML scraping
- SSRF protection via `allowed_private_endpoints`
- Group-scoped `web_search_enabled` setting

### Docker
- Multi-stage build
- Non-root user
- Healthcheck
- docker-compose: panel + listener, secrets mounted (never baked), loopback binding, drop-all + no-new-privileges

### CI
- GitHub Actions: lint (ruff), test (pytest matrix), security scan, Docker smoke build
- Least-privilege permissions
- Concurrency group prevents overlapping runs

## NOT STARTED

| Item | Notes |
|---|---|

| Documentation & release hardening | FINAL_HANDOFF.md etc. (this document) |
