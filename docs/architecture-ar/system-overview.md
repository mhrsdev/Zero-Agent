# النظرة العامة على نظام Zero

## هوية المشروع

يصف README مشروع Zero على أنه مرافق Telegram مستقل، وليس جزءاً من Hermes. له سطحان تشغيليان:

- **Listener**: جلسة مستخدم Telegram عبر Telethon لاستقبال التحديثات والرد في المجموعات المسموح بها.
- **Management/Panel**: بوت إدارة عبر aiogram ولوحة مالك عبر aiohttp.

الأدلة: `README.md:10-20` و`scripts/run_listener.py:20-43` و`scripts/run_panel.py:16-40`.

## الرسم الفعلي للحدود

```text
جلسة Telegram MTProto
        │
        ▼
 scripts/run_listener.py
        │ معالجات الأحداث + قفل الرسائل العام
        ├── Office Bridge (عند تفعيل office.enabled فقط)
        ├── ZeroBrain
        │     ├── triggers/security/policy
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
        ├── معالجات المالك عبر aiogram
        ├── PanelAPI (aiohttp)
        ├── ZeroStore + خدمات الذاكرة
        └── runtime_control → عملية listener

مسار Office
 listener event → TelegramOfficeBridge → OfficeIntakeService
 → OfficeRepository / job دائم → listener coordinator
 → OfficePlanner → OfficeWorker → OfficeCLI adapter
 → validation/render/review → DeliveryCoordinator → Telegram + quota/outbox
```

تم استخراج ذلك من imports في `scripts/run_listener.py:23-43`، وبناء الخدمات في `:79-126`، والمهام الخلفية في `:685-696`، وتجميع اللوحة في `scripts/run_panel.py:57-79`.

## خريطة المسؤوليات

| الحد | المالك | المسؤولية الفعلية |
|---|---|---|
| استقبال Telegram | `run_listener.py` | الاتصال، authorization، الأحداث، المجموعات المسموحة، dedup والتوجيه |
| قرار الرد | `zero/brain.py` | orchestration، prompts، الأمن، الذاكرة، الوسائط والإرسال |
| المزودون | `zero/router.py` | pools للمفاتيح، quota/cooldown، fallback واستدعاءات HTTP |
| التخزين الرئيسي | `zero/storage.py` | مخطط SQLite وعمليات الرسائل/الذاكرة/المجموعات/cron |
| اللوحة | `zero/panel_api.py` + `run_panel.py` | OTP، الجلسات، صلاحية المالك، API القراءة/التعديل |
| تخزين Office | `zero/office/db.py` | jobs، انتقالات الحالة، quota، leases، events، outbox |
| تشغيل Office | `zero/office/*` + `run_office_worker.py` | preflight، التخطيط، adapter، worker، delivery والتنظيف |
| الإعدادات | `zero/config.py` | YAML، ملف secrets، Pydantic وenv overrides |

## التزامن

يستخدم listener قفلاً عاماً من `asyncio.Lock` حول `_on_message` (`run_listener.py:148-150` و`:427-430`). هذا ي serializes الأحداث داخل العملية نفسها، لكنه لا يستبدل معاملات SQLite أو قيود quota أو leases أو idempotency. أي تغيير هنا يحتاج اختبارات عملية وإعادة تشغيل وتعدد workers.