# Zero Administration Panel Audit

Date: 2026-07-24

## Scope and evidence

Inspected the tracked repository structure (260 tracked files), current panel source, panel tests, composition roots, Pydantic configuration, storage/architecture documentation, web-search adapters, Office modules, proactive modules, and deployment units. Runtime files, databases, sessions, secrets, and logs were not copied into this report.

## Current architecture

- `scripts/run_listener.py` is the production Telegram user-session composition root. It constructs Telethon, `ZeroBrain`, `IndependentRouter`, memory services, `HybridWeb`, Office and proactive workers.
- `scripts/run_panel.py` is a second composition root. It constructs the aiogram management bot, `ZeroStore`, read/write memory helpers, knowledge/jobs services, `PanelAPI`, and serves the static panel.
- `zero/panel_api.py` is an `aiohttp` adapter with in-memory OTP pending state, in-memory sessions, CSRF, rate limiting, security headers, dashboard, chat, memory, knowledge, router, logs, jobs, users, sessions, and allowlisted settings routes.
- `panel/index.html`, `panel/app.js`, and `panel/styles.css` are a single-page, Persian RTL, glass/cinematic control-center UI. It has no package/build/type-check pipeline and uses direct string-rendered HTML.
- `zero/config.py` uses YAML + Pydantic and a separate protected secret YAML. The schema is broad but global; group configuration is primarily represented by allowed group IDs/usernames and shared defaults.
- `zero/storage.py` is the central SQLite persistence boundary. Office repositories also use the configured DB path. There is no central panel-owned migration/version table observed.
- `deploy/zero-panel.service` binds the panel to loopback and runs as `zero:zero` with strict systemd hardening. It uses `/opt/zero/config/panel.yaml` and `/opt/zero/runtime/secrets/zero.secrets.yaml`.

## Reusable components

- Pydantic validation style and protected secret-file loading in `zero/config.py`.
- `ZeroStore` service methods already used by the panel, subject to scope/authorization review.
- `IndependentRouter.status()` for provider health summaries; never return key values.
- `listener_status()` and existing runtime controls for operational state, subject to explicit authorization.
- Existing Office repository/worker status and quota semantics; do not mutate semantics as part of UI work.
- Existing log redaction helper pattern, after removing hardcoded paths and adding server-side tests.
- Existing loopback systemd deployment posture.

## Replace or isolate

- Current RTL/Persian/glass UI and navigation: replace completely.
- Telegram-OTP-only panel authentication: replace with durable local administrator auth. Telegram login may remain as an optional future/compatibility path only.
- In-memory sessions and pending OTP state: replace with durable hashed session/setup state.
- Direct settings override mutations: replace with typed configuration commands/repositories and atomic updates.
- `zero/web.py` local SearXNG/engine fallback: not a public-release path; external API providers only.
- Telegram Search config/runtime/public visibility: remove from public API, setup, tools, dashboard, docs, and feature flags; retain only an internal disabled compatibility boundary if needed for migration.
- Static `/opt/zero/runtime/logs/requests.log` assumption in `PanelAPI`: derive paths from validated configuration.

## Security risks

1. Current auth sessions disappear on process restart and use process memory.
2. The current login flow depends on a management bot and sends OTP through Telegram; this does not satisfy first-run local administrator setup.
3. Current `_ALLOWED_SETTINGS` includes `tgsearch_enabled`, exposing an unsupported capability through a write boundary.
4. Current frontend contains Persian labels, RTL layout, and placeholder/static UI semantics that conflict with the public installation goal.
5. Current public API does not have typed request/response models for setup, providers, groups, backups, or Telegram mode flows.
6. Existing configuration contains sensitive/production-specific paths in examples/docs; new panel docs must use neutral placeholders.
7. Current provider/web configuration represents Gemini/OpenRouter and local search, not the requested generalized external-only provider management.
8. Group-scoped memory access must continue to use `(chat_id, sender_id)` and never be generalized by sender ID alone.

## Migration risks

- Existing runtime data is real and private. No destructive migration is safe without an encrypted backup and a dry run.
- Current panel admins are Telegram owner/viewer identities, not local password accounts; only owner identity can be mapped automatically without user approval.
- Current group configuration is distributed across YAML, storage settings, and resolved Telegram usernames/IDs; automatic group migration needs explicit ambiguity handling.
- Main SQLite schema has many module-owned migrations without one central version table. Panel schema should be separate or clearly namespaced to avoid changing ZeroStore behavior.
- Current production panel and listener use different default config paths; deployment must set `ZERO_CONFIG_PATH` explicitly.

## Proposed routes

- `/login`, `/setup/*`, `/dashboard`
- `/telegram`, `/groups`, `/groups/:id`, `/models`, `/web-search`, `/memory`, `/tools`, `/usage`, `/logs`, `/backups`, `/settings`
- API namespaces: `/api/auth`, `/api/setup`, `/api/telegram`, `/api/providers`, `/api/groups`, `/api/web-search`, `/api/memory`, `/api/tools`, `/api/usage`, `/api/logs`, `/api/backups`, `/api/settings`, `/api/health`

Telegram Search is intentionally absent from this list.

## First implementation slice

Build a durable panel store, local admin auth, setup state machine, typed API error model, new English-only application shell, and a truthful dashboard. Keep existing core behavior untouched. Add focused tests before exposing mutation endpoints. Add Bot/User/Hybrid Telegram and external provider workflows only after their backend contracts are proven and mocked tests exist.

## Confirmed baseline

`python3 -m pytest tests/test_panel_api.py -q` passed with `6 passed` before new changes.

## Explicitly not done in audit

No service restart, deployment, live Telegram login, paid API request, destructive migration, runtime database mutation, or production rollout was performed.
