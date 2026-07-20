# داده، SQLite و persistence

## DB اصلی

`ZeroConfig.memory.db_path` مسیر DB را تعیین می‌کند (`config.py:148-155`). `run_listener.py:79` و `run_panel.py:59` همان مسیر را به `ZeroStore` می‌دهند. `scripts/init_db.py:14-17` نیز با ساخت `ZeroStore` initialization را انجام می‌دهد.

`zero/storage.py` با `SCHEMA` چندین جدول را در یک DB نگه می‌دارد. schema از `storage.py:24` شروع می‌شود و شامل دسته‌های زیر است:

- settings، group context و consumed messages
- recent messages و incoming dedup
- user profiles scoped/unscoped
- memory items و rate/vision events
- stats و router keys
- group user/social state، feedback، threads، quotes و daily stats
- cron permissions/templates/runs
- memory V2 و لایه‌های semantic/experience/procedural/world
- knowledge، Telegram source/search و سایر featureها

فهرست کامل باید از `SCHEMA` و migrationهای همان فایل استخراج شود؛ این مستندات عمدی خلاصه‌اند تا schema را duplicate و زودقدیمی نکنند.

## هویت و dedup

`incoming_message_dedup` primary key را روی `(platform, account_scope, chat_id, message_id)` دارد (`storage.py:46-64`). این یعنی message ID بدون account/chat scope هویت کافی نیست. هر تغییر در listener یا transport باید همین چهارچوب را حفظ کند.

Profile scoped روی `(chat_id, sender_id)` است (`storage.py:75-89`). برای memory/Telegram search نیز identity باید از call site بررسی شود؛ استفاده از sender ID تنها می‌تواند scope را خراب کند.

## Memory ownership

`ZeroBrain` در constructor لایه‌های semantic، experience، procedural، world و MemoryV2 را جدا می‌سازد (`brain.py:226-248`). این جداسازی قراردادی مهم است: تغییر retrieval یا compaction یک لایه نباید به‌طور ضمنی جدول لایه‌ی دیگر را mutate کند.

`DeferredMemory`، `DocumentBundles` و `KnowledgeWorker` نیز به DB اصلی یا pathهای مرتبط متصل‌اند. migration scripts مستقل در `scripts/migrate_memory_v1_to_v2.py` و `scripts/backfill_memory_v2.py` side effect دارند؛ آن‌ها را در محیط production بدون backup/rollback اجرا نکنید.

## Migration و versioning

`ZeroStore._init_db()` ابتدا schema inline را اجرا و سپس migrationهای module-specific را صدا می‌زند (`storage.py:596-614`). migrationها عمدتاً با `CREATE TABLE IF NOT EXISTS`، بررسی `PRAGMA table_info` و `ALTER TABLE` پخش شده‌اند؛ برای DB اصلی یک migration-version مرکزی مشاهده نشد.

Memory V2 جداست و schema version row خودش را دارد؛ V1→V2 با `scripts/migrate_memory_v1_to_v2.py` صریحاً اجرا می‌شود و startup خودکار نیست.

## Office DB boundary

`OfficeRepository` در `zero/office/db.py` persistence جداگانه‌ی منطقی برای jobهاست اما در runtime به همان `config.memory.db_path` داده می‌شود. بنابراین migration و locking آن با ZeroStore مشترک است؛ «جدا بودن package» به معنای DB جدا نیست.

Office جدول/منطق‌های job، quota usage، job events و delivery outbox دارد؛ state transition و quota باید از repository عبور کند. مستقیم‌نویسی در SQLite فقط در تست/failure injection قابل قبول است.

## Outbox و delivery

Delivery باید پس از validation خروجی انجام شود. outbox کلید idempotency دارد و quota commit به receipt وابسته است؛ crash windowها در `zero/office/delivery.py` و تست‌های `tests/test_office_delivery.py` مستند/قابل بررسی‌اند.

## اندازه و دامنه‌ی runtime فعلی

در بررسی read-only، DB اصلی `runtime/state/zero.db` حدود ۱۴MB، mode `0600` و دارای حدود ۸۸ جدول مشاهده شد. Memory V2 نیز DB جداگانه‌ی `zero-memory-v2.db` با mode `0600` دارد. این اعداد snapshot همان محیط‌اند و قرارداد schema نیستند.

## Backup و فایل‌های runtime

مسیرهای runtime در config/example و systemd units دیده می‌شوند: `runtime/state`، `runtime/logs`، `runtime/office_jobs` و `runtime/office_ingest`. این‌ها داده‌ی operational هستند، نه source. session، DB، log و secret هرگز نباید در docs/archive عمومی قرار گیرند.
