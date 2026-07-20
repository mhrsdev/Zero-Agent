# راهنمای اعمال تغییر برای توسعه‌دهنده

## قبل از هر تغییر

1. `docs/architecture/README.md` و overview/flow مرتبط را بخوانید.
2. import و call site symbol هدف را با search بررسی کنید؛ فقط نام فایل کافی نیست.
3. config source، runtime service و testهای boundary را بخوانید.
4. اگر DB/state تغییر می‌کند، schema، migration، lock/transaction و backup را مشخص کنید.
5. اگر Telegram یا provider تغییر می‌کند، identity/account scope، dedup، rate limit و secret policy را مشخص کنید.

## کجا تغییر بدهیم؟

- افزودن setting: مدل و loader در `zero/config.py` + example/env + validation test.
- تغییر پیام/پاسخ: ابتدا `brain.py`، `prompts.py`، `triggers.py` و call site listener را trace کنید.
- provider/fallback/key policy: `router.py` و router tests؛ secret را به state/log ندهید.
- memory: لایه‌ی مربوط (`semantic/experience/procedural/world/memory_v2`) و `memory_context.py`؛ storage schema را بی‌دلیل cross-layer نکنید.
- Telegram event: `run_listener.py`؛ برای هر path dedup، allowed scope و self-message behavior را حفظ کنید.
- panel: `run_panel.py` فقط composition؛ route/auth/security در `panel_api.py`؛ UI در `panel/`.
- Office: command gate → intake/preflight → db quota/job → planner/adapter/worker/delivery؛ intent عمومی نباید gate را bypass کند.
- scheduled behavior: loop در `run_listener.py` و service مناسب `proactive_*` یا `template_jobs.py`؛ interval، claim و outcome را تست کنید.

## تغییرات ممنوع بدون طراحی/تست

- تغییر کلید هویت از `(chat_id,sender_id)` یا dedup scope.
- افزودن API key/token در YAML، source، fixture یا log.
- bypass کردن `ZeroStore`/`OfficeRepository` برای quota/job.
- اجرای command آزاد یا shell string از مدل/کاربر.
- تبدیل fixture/live script به production entrypoint بدون systemd و health.
- فعال‌سازی Office یا search با تغییر runtime بدون feature flag/rollback.

## verification gate پیشنهادی

```text
unit boundary
 → security/identity/concurrency tests
 → focused integration
 → full tests
 → compile/import check
 → inspect service/config diff
 → restart/health check در محیط staging
 → documentation update
```

اگر `.git` موجود نیست، به‌جای diff از manifest/checksum filesystem و backup استفاده کنید و این محدودیت را صریح گزارش کنید.

## rollback

هر تغییر config/service/DB باید rollback مشخص داشته باشد. برای runtime ابتدا feature را خاموش، worker را متوقف، service را reload/restart و health را بررسی کنید؛ برای migration از backup verified استفاده کنید. این راهنما procedure عمومی است و جایگزین runbook subsystem نمی‌شود.
