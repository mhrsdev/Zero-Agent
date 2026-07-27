# Security Audit — Zero v0.1.0-alpha

## 1. Runtime Isolation (P0-2)

### Fail-Closed Enforcement
- `Scope.__post_init__` rejects: `legacy`, `default`, `0`, `""`, `None`, `candidate:*`
- Every stateful resource (office_jobs, office_quota_usage, office_delivery_outbox, proactive_followup_outbox) carries `installation_id` + `group_id` as `TEXT NOT NULL`
- `reserve_and_create()` validates scope before any DB effect
- `DeliveryCoordinator.tick()` verifies scope before every send
- `Outbox.reserve()` validates scope before INSERT

### Cross-Group Isolation
- Quota is scoped by `(installation_id, group_id, user_id, quota_date)` — no cross-group quota consumption
- Office jobs are scoped — job from group A cannot be delivered to group B
- Proactive outbox is scoped — followup from group A cannot be sent to group B's thread
- Verified by 11 adversarial Multi-Group E2E tests (two groups, two threads, one user)

## 2. Authentication & Authorization

### Password Security
- scrypt hashing (not bcrypt, not plaintext)
- Default admin password must be changed on first login
- Password never logged, never returned in API responses

### Session Security
- Session tokens are hashed (SHA-256) before storage
- 86400s expiry (24h)
- Tokens never reused across installations
- `logout_all` endpoint revokes all sessions

### CSRF Protection
- CSRF token required on ALL write operations (`POST`, `PUT`, `DELETE`)
- Missing CSRF → 403
- Wrong CSRF → 403
- Verified by 6 dedicated CSRF tests

### Role-Based Access Control
- Roles: `owner` (full), `viewer` (read-only)
- Viewer cannot: mutate settings, revoke sessions, perform setup steps
- Verified by 4 RBAC tests

### Rate Limiting
- Auth endpoints: 30 requests/minute
- API endpoints: 120 requests/minute
- Exceeding → 429 Too Many Requests

## 3. Input Security

### Office Document Preflight
- Zip bomb rejection (compression ratio + uncompressed size limits)
- Archive traversal detection
- Macro rejection
- XML entity injection rejection
- External relationship rejection
- Embedded object rejection
- Dangerous Excel formula detection
- Structural limits (independent of character count)

### SSRF Protection
- `ConnectionPoolTransport` has `allowed_private_endpoints` parameter
- Connection limits per host
- Per-call timeout

## 4. Secret Hygiene

### .gitignore
- `.env`, `*.env*` (except `.env.example`)
- `*.session`, `*.session-*` (Telegram sessions)
- `*.db`, `*.sqlite`, `*.sqlite3`
- `runtime/`, `release/`, `public-release/`, `docker-data/`
- `config/zero.yaml`, `config/*secret*`

### Secret Scan Results
- **0 secrets found** in tracked source files (`zero/` directory)
- Scanned for: `sk-[a-zA-Z0-9]{20,}`, `ghp_[a-zA-Z0-9]{36}`, `xoxb-[0-9]{10,}`, `AKIA[A-Z0-9]{16}`
- Test files contain regex patterns (not real secrets) — excluded from scan scope

### Provider Secrets
- Symbolic secret refs (`secret_ref` field) — raw credentials rejected
- Prefixes `sk-`, `xoxb-`, `ghp_` rejected as non-symbolic
- Secrets loaded at runtime via `secret_resolver`, never stored in config

## 5. Docker Security

- Multi-stage build (build dependencies not in final image)
- Non-root user (`zero:zero`)
- docker-compose: secrets mounted as volumes (never baked into image)
- Loopback binding by default (`127.0.0.1`)
- `security_opt: no-new-privileges`
- `cap_drop: ALL`
- Healthcheck endpoint

## 6. CI Security

- Least-privilege GitHub Actions permissions
- Dependency vulnerability scan in security job
- Public artifact scan
- Docker build smoke test
- Concurrency group prevents overlapping runs

## Known Limitations

1. `officecli` binary not installed in test environment (5 tests skip/soft-fail)
2. Live Telegram/web tests require `ZERO_LIVE_E2E=1` (environmental)
3. Zero TUI not yet implemented
4. Community E2E not yet implemented (requires live credentials)
