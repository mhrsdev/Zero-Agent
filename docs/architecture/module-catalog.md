# کاتالوگ ماژول‌ها و محل مناسب تغییر

این فهرست از AST و importهای واقعی ساخته شده است. «مسئولیت» به رفتار مشاهده‌شده اشاره دارد، نه قرارداد ایده‌آل.

## هسته

- `zero/brain.py` — orchestration پاسخ، prompt، امنیت خروجی، memory، market، web، media و sticker. بزرگ‌ترین coupling هسته است؛ تغییر کوچک را ابتدا در helper/service جدا تست کنید.
- `zero/models.py` — dataclassهای `IncomingMessage`، `Decision`، `RouteResult` و `UserProfile`.
- `zero/prompts.py` — builderهای prompt پاسخ، starter و summary.
- `zero/persona.py` — ساخت block شخصیت.
- `zero/triggers.py` — normalize، trigger و تصمیم reply.
- `zero/security.py` — intent classification، dangerous request و پاسخ ثابت امنیتی.
- `zero/moderation.py` — spam/abuse helperها.

## Configuration و عملیات

- `zero/config.py` — مدل‌های Pydantic، YAML load، secret file و Office env overrides. تنها محل معتبر افزودن config جدید است.
- `zero/logging_utils.py` — logger setup.
- `zero/management.py` — خواندن bot token از private file و ارسال bot message.
- `zero/runtime_control.py` — PID/process identity و start/stop/restart listener.
- `scripts/office_health.py` و `scripts/office_feature_enabled.py` — health و fail-closed condition Office.

## Telegram و سرویس‌های تعامل

- `scripts/run_listener.py` — entrypoint و composition root؛ handlerهای Telethon و loopهای background.
- `scripts/run_panel.py` — entrypoint aiogram/panel.
- `zero/social.py`، `social_awareness.py`، `social_plus.py` — interaction/social policy و feedback.
- `zero/reactions.py` — reaction selection، cooldown و Telegram reactions.
- `zero/vision.py` — media download/vision/rate limits.
- `zero/stickers/` — observer، classifier، library، sender، panel، account saver و models.
- `zero/telegram_search.py` — Telegram search client/router/context؛ configuration آن در `TelegramSearchConfig` است.

## Memory و context

- `zero/storage.py` — ZeroStore و schema مرکزی SQLite؛ shared persistence boundary.
- `zero/memory.py` — candidate extraction و memory text policy.
- `zero/memory_context.py` و `memory_planner.py` — composition memory برای prompt.
- `zero/semantic_memory.py`، `experience_memory.py`، `procedural_memory.py`، `world_model.py` — لایه‌های durable مجزا با DB connectionهای خودشان.
- `zero/memory_v2/service.py` و `retrieval_planner.py` — مسیر Memory V2 و planner retrieval.
- `zero/deferred_memory.py` — حافظه deferred و زمان‌بندی/approval.
- `zero/group_context.py`، `document_bundles.py` — context گروه و bundleهای سند.
- `zero/identity.py` — canonical identity key و logging.

## Web، knowledge و market

- `zero/web.py` — facade/intent detector برای search.
- `zero/web_search/` — مدل، query rewrite، pipeline، providers، extraction، ranking، dedup، cache، truth guard و conversation state.
- `zero/google_grounding.py` — Google grounding.
- `zero/knowledge.py` — policy، retriever، backend و worker دانش.
- `zero/market_prices.py` — Binance/Navasan/Nobitex clientها و cache.
- `zero/tg_source_manager.py` و `scripts/tg_source_manager.py` — manifest/source discovery برای Telegram.

## Proactive و jobها

- `zero/proactive_followups.py` — followup orchestration.
- `proactive_scheduler.py`، `proactive_policy.py`، `proactive_outcome.py`، `proactive_feedback.py`، `proactive_transport.py`، `proactive_rollout.py` — decomposition scheduler/policy/outcome/feedback/transport/rollout.
- `zero/template_jobs.py` — template-based scheduled jobs و security parsing.
- `scripts/refresh_iran_market.py` — utility update market.

## Office

- `command_gate.py` — parser deterministic برای commandهای مجاز.
- `telegram.py` — اتصال attachment/reply/user/chat به intake.
- `intake.py` — request relationship، فایل و preflight/quota entry.
- `text.py` — normalize text و quota date.
- `preflight.py` — OOXML/MIME/archive/security checks.
- `workspace.py` — path confinement و workspace isolation.
- `db.py` — state machine، quota، lease، events، outbox و migration.
- `planner.py` — JSON plan و canonicalization محدود.
- `adapter.py` — schema عملیات و OfficeCLI subprocess boundary.
- `worker.py` — planning/process/validation/repair coordination.
- `delivery.py` — Telegram delivery idempotency، receipt/quota commit و visual review.
- `cleanup.py` — retention cleanup.
- `scripts/run_office_worker.py` — worker process composition root.

## Archive و prototypeها

`archive/`، `scripts/*prototype.py`، `scripts/real_tests.py`، `scripts/live_*` و بعضی benchmark/migration utilityها importable یا قابل اجرا هستند، اما composition root production محسوب نمی‌شوند مگر service unit یا README آن‌ها را متصل کرده باشد. قبل از استفاده باید call site و side effect را جدا بررسی کنید.
