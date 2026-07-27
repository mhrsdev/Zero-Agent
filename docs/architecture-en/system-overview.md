# Zero System Overview

## Project identity

The project README describes Zero as an independent Telegram companion rather than a Hermes subsystem. It has two operational surfaces:

- **Listener**: a Telethon Telegram user session for receiving updates and responding in allowed groups.
- **Management/panel**: an aiogram management bot plus an aiohttp owner panel.

Evidence: `README.md:10-20`, `scripts/run_listener.py:20-43`, and `scripts/run_panel.py:16-40`.

## Actual boundary diagram

```text
Telegram MTProto user session
        │
        ▼
 scripts/run_listener.py
        │  event handlers + global message lock
        ├── Office bridge (only when office.enabled)
        ├── ZeroBrain
        │     ├── trigger/security/policy
        │     ├── IndependentRouter ── Gemini / OpenRouter
        │     ├── memory/context/group/social
        │     ├── web/search/market
        │     ├── vision/stickers/reactions
        │     └── proactive followups
        └── ZeroStore / SQLite

Telegram Bot API
        │
        ▼
 scripts/run_panel.py
        ├── aiogram owner handlers
        ├── PanelAPI (aiohttp)
        ├── ZeroStore + memory services
        └── runtime_control → listener process

Office path (feature-gated)
 listener event → TelegramOfficeBridge → OfficeIntakeService
 → OfficeRepository / persistent job → listener coordinator
 → OfficePlanner → OfficeWorker → OfficeCLI adapter
 → validation/render/review → DeliveryCoordinator → Telegram + quota/outbox
```

This is derived from imports in `scripts/run_listener.py:23-43`, service construction at `:79-126`, background tasks at `:685-696`, and panel composition at `scripts/run_panel.py:57-79`.

## Responsibility map

| Boundary | Owner | Observed responsibility |
|---|---|---|
| Telegram input | `run_listener.py` | connection, authorization, event handlers, allowed chats, dedup, dispatch |
| Reply decision | `zero/brain.py` | message orchestration, prompts, security, memory, media, sending |
| Providers | `zero/router.py` | key pools, quota/cooldown, fallback, HTTP calls |
| Main persistence | `zero/storage.py` | large SQLite schema and async message/memory/group/cron operations |
| Panel | `zero/panel_api.py` + `run_panel.py` | OTP, sessions, owner authorization, read/update API |
| Office persistence | `zero/office/db.py` | jobs, state transitions, quota, leases, events, outbox |
| Office runtime | `zero/office/*` + `run_office_worker.py` | preflight, planning, adapter, worker, delivery, cleanup |
| Configuration | `zero/config.py` | YAML, secret file, Pydantic validation, environment overrides |

## External boundaries

- Telegram MTProto through Telethon in the listener.
- Telegram Bot API through aiogram in the panel and management helper.
- Gemini and OpenRouter in `zero/router.py` through `urllib.request` (`router.py:129-148`).
- Web/search providers under `zero/web_search/` and `zero/web.py`.
- Market APIs in `zero/market_prices.py`.
- OfficeCLI as an external subprocess, reached through the Office adapter rather than a raw model prompt.

## Concurrency boundary

The listener uses one global `asyncio.Lock` around `_on_message` (`run_listener.py:148-150`, `:427-430`). This serializes events within one process; it does not replace SQLite transactions, quota constraints, leases, or idempotency. Changes here require both single-process and restart/multi-worker tests.