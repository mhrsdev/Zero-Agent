# دليل المطور لإجراء التغييرات

## قبل أي تغيير

1. اقرأ overview ومسار التشغيل المرتبط.
2. تتبع imports وcall sites للرمز المطلوب؛ اسم الملف وحده لا يكفي.
3. اقرأ config ومهمات الخدمة واختبارات boundary.
4. عند تغيير DB/state، حدد schema وmigration وtransaction وbackup وrollback.
5. عند تغيير Telegram/provider، حافظ على identity scope وdedup وrate limits وسياسة الأسرار.

## أين يتم التغيير؟

- setting جديد: `zero/config.py` مع example/env واختبارات validation.
- سلوك الرد: افحص `brain.py` و`prompts.py` و`triggers.py` وcall sites في listener.
- provider/fallback/key policy: `router.py` واختباراته.
- memory: الطبقة المعنية و`memory_context.py`، ولا تخلط الطبقات ضمنياً.
- Telegram events: `run_listener.py` مع الحفاظ على dedup والـ allowlist ورسائل self.
- panel: composition في `run_panel.py` وroutes/auth/security في `panel_api.py` وUI في `panel/`.
- Office: command gate ثم intake/preflight ثم DB quota/job ثم planner/adapter/worker/delivery. لا تسمح لـ intent عام بتجاوز gate.
- scheduled behavior: loop في listener والخدمة المناسبة في proactive/template، مع اختبارات claim/outcome.

## لا تتجاوز دون تصميم واختبار

- هوية `(chat_id, sender_id)` أو نطاق dedup.
- حدود تخزين الأسرار وإخفائها.
- `ZeroStore`/`OfficeRepository` لحالة quota/jobs.
- structured adapter وحدود shell.
- feature flags وhealth وrollback لـ Office/search.

## بوابة التحقق

```text
unit boundary
 → security/identity/concurrency
 → focused integration
 → full tests
 → import/AST check
 → فحص service/config
 → staging health/restart
 → تحديث documentation
```

عند غياب `.git` استخدم filesystem manifests/checksums ونسخاً احتياطية متحققة، واذكر هذا القيد صراحة.

## rollback

كل تغيير في config/service/DB يحتاج rollback. في runtime عطّل feature أولاً، أوقف worker، أعد تحميل/restart الخدمة وافحص health. في migration استخدم backup متحققاً.