# البيانات وSQLite وPersistence

## قاعدة البيانات الرئيسية

يحدد `ZeroConfig.memory.db_path` المسار الرئيسي (`config.py:148-155`). يستخدم listener وpanel المسار نفسه مع `ZeroStore` (`run_listener.py:79` و`run_panel.py:59`). ويهيئه `scripts/init_db.py:14-17`.

يبدأ مخطط `SCHEMA` المضمّن في `zero/storage.py:24`، ويشمل settings وcontext وrecent messages وincoming dedup وprofiles وطبقات الذاكرة وrate/vision events والإحصاءات وrouter وgroup/social وcron وknowledge وTelegram search وproactive وstickers وOffice.

## الهوية وdedup

المفتاح الأساسي لـ `incoming_message_dedup` هو `(platform, account_scope, chat_id, message_id)` (`storage.py:46-64`). رقم الرسالة وحده ليس هوية كافية.

Profiles المقيدة تستخدم `(chat_id, sender_id)` (`storage.py:75-89`). يجب ألا يستبدل ذلك بـ sender ID وحده.

## ملكية الذاكرة

ينشئ `ZeroBrain` طبقات semantic وexperience وprocedural وworld وMemory V2 منفصلة (`brain.py:226-248`). لا يجب أن يغير retrieval أو compaction طبقة أخرى ضمنياً.

## Migration وversioning

ينفذ `ZeroStore._init_db()` المخطط المضمّن ثم migrations خاصة بالوحدات (`storage.py:596-614`). قاعدة V1 الرئيسية تستخدم `CREATE TABLE IF NOT EXISTS` وفحوص `PRAGMA table_info` و`ALTER TABLE` موزعة؛ لم تتم ملاحظة migration-version مركزية.

لدى Memory V2 صف version خاص بها. Migration V1→V2 صريحة عبر `scripts/migrate_memory_v1_to_v2.py` وليست migration تلقائية عند startup.

## Memory V2

القيمة الافتراضية لـ `MemoryV2Service` هي `/opt/zero/runtime/state/zero-memory-v2.db` بشكل مستقل عن `config.memory.db_path` (`memory_v2/service.py:19-25` و`brain.py:239`). session state منظّم ومحدود وذو TTL وoptimistic versioning.

## Office persistence

`OfficeRepository` حد منطقي منفصل، لكنه يستقبل في runtime مسار DB نفسه. لذلك تشترك Office وZeroStore في SQLite locking وmigration؛ انفصال الحزمة لا يعني انفصال DB.

## Delivery وoutbox

لا يتم التسليم قبل validation. لدى outbox مفتاح idempotency، ويرتبط quota commit بـ receipt. توجد اختبارات crash-window في `zero/office/delivery.py` و`tests/test_office_delivery.py`.

## لقطة runtime

أظهر الفحص read-only قاعدة رئيسية بحجم يقارب 14MB ووضع `0600` وحوالي 88 جدولاً، مع DB منفصلة لـ Memory V2. هذه لقطة بيئية وليست عقداً لمخطط schema.

بيانات `runtime/state` و`runtime/logs` و`runtime/office_jobs` و`runtime/office_ingest` خاصة وتشغيلية ولا يجب وضعها في archive عام.