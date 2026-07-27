# Module Catalog and Change Boundaries

This catalog is based on AST/import inspection and observed call sites. Responsibilities describe current behavior, not an ideal future design.

## Core

- `zero/brain.py` — response orchestration, prompts, output security, memory, market, web, media, and stickers. It is the most coupled core module; isolate and test helpers before adding more branching.
- `zero/models.py` — `IncomingMessage`, `Decision`, `RouteResult`, and `UserProfile` data classes.
- `zero/prompts.py` — reply, starter, summary, and merge prompt builders.
- `zero/persona.py` — persona block construction.
- `zero/triggers.py` — normalization, triggers, and reply decisions.
- `zero/security.py` — intent classification, dangerous-request handling, and fixed security replies.
- `zero/moderation.py` — spam/abuse helpers.

## Configuration and operations

- `zero/config.py` — Pydantic models, YAML loading, secret-file handling, and Office environment overrides. Add configuration here, not in a feature module.
- `zero/logging_utils.py` — logger setup.
- `zero/management.py` — private bot-token loading and bot message helper.
- `zero/runtime_control.py` — PID/process identity and listener start/stop/restart.
- `scripts/office_health.py` and `scripts/office_feature_enabled.py` — Office health and fail-closed systemd condition.

## Telegram, social, and media

- `scripts/run_listener.py` — production composition root, Telethon handlers, and background loops.
- `scripts/run_panel.py` — aiogram/panel composition root.
- `zero/social.py`, `social_awareness.py`, `social_plus.py` — social policy and feedback.
- `zero/reactions.py` — reaction selection, cooldowns, and Telegram reactions.
- `zero/vision.py` — media download, vision, and rate limits.
- `zero/stickers/` — observer, classifier, library, sender, panel, account saver, and models.
- `zero/telegram_search.py` — Telegram search client/router/context; configuration lives in `TelegramSearchConfig`.

## Memory and context

- `zero/storage.py` — central `ZeroStore` and SQLite schema; shared persistence boundary.
- `zero/memory.py` — memory candidate extraction and text policy.
- `zero/memory_context.py` and `memory_planner.py` — prompt memory composition.
- `semantic_memory.py`, `experience_memory.py`, `procedural_memory.py`, `world_model.py` — separate durable layers.
- `memory_v2/service.py` and `retrieval_planner.py` — Memory V2 path and retrieval planning.
- `deferred_memory.py` — deferred memory and timing/approval behavior.
- `group_context.py`, `document_bundles.py` — group context and document bundles.
- `identity.py` — canonical identity keys and identity logging.

## Web, knowledge, and markets

- `zero/web.py` — search facade and intent detector.
- `zero/web_search/` — models, query rewriting, pipeline, providers, extraction, ranking, deduplication, cache, truth guard, and conversation state.
- `google_grounding.py` — Google grounding.
- `knowledge.py` — policy, retrieval, backends, and knowledge worker.
- `market_prices.py` — Binance/Navasan/Nobitex clients and cache.
- `tg_source_manager.py` and `scripts/tg_source_manager.py` — source discovery and manifest management.

## Proactive and scheduled jobs

- `proactive_followups.py` — followup orchestration.
- `proactive_scheduler.py`, `proactive_policy.py`, `proactive_outcome.py`, `proactive_feedback.py`, `proactive_transport.py`, `proactive_rollout.py` — scheduler/policy/outcome/feedback/transport/rollout decomposition.
- `template_jobs.py` — template-based scheduled jobs and security parsing.
- `scripts/refresh_iran_market.py` — market update utility.

## Office

- `command_gate.py` — deterministic parser for supported commands.
- `telegram.py` — attachment/reply/user/chat boundary into intake.
- `intake.py` — request relationship, file, preflight, and quota entry.
- `text.py` — text normalization and quota date.
- `preflight.py` — OOXML/MIME/archive/security checks.
- `workspace.py` — path confinement and isolated workspaces.
- `db.py` — state machine, quota, lease, events, outbox, and migration.
- `planner.py` — JSON plan and bounded canonicalization.
- `adapter.py` — structured operation schema and OfficeCLI subprocess boundary.
- `worker.py` — planning/process/validation/repair coordination.
- `delivery.py` — idempotent Telegram delivery, receipt/quota commit, and visual review.
- `cleanup.py` — retention cleanup.

## Archive and prototypes

`archive/`, `scripts/*prototype.py`, `scripts/real_tests.py`, `scripts/live_*`, benchmark utilities, and migration utilities may be importable or executable, but are not production composition roots unless a service unit or documented call site connects them. Inspect side effects before running them.