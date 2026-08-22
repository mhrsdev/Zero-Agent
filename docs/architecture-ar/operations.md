# التشغيل ونقاط الدخول والنشر

## نقاط الدخول

| الخدمة/الأمر | الملف | الأثر |
|---|---|---|
| Listener | `python scripts/run_listener.py` | جلسة Telethon وBrain والمهام الخلفية |
| Panel | `python scripts/run_panel.py` | aiogram bot وaiohttp panel |
| Office worker | `python scripts/run_office_worker.py` | worker دائم؛ يخرج بنجاح عند التعطيل |
| DB init | `python scripts/init_db.py` | إنشاء/فتح ZeroStore |
| Office health | `python scripts/office_health.py` | فحوص حالة Office |
| Office condition | `python scripts/office_feature_enabled.py` | شرط systemd fail-closed |

## Panel المثبت وconfig

يعمل panel المثبت باستخدام `/opt/zero/config/panel.yaml` وملف secrets منفصل، ويربط على `127.0.0.1:8787`. نقطة دخوله هي `scripts/run_panel.py`.

لا يستخدم panel وlistener نفس default config path: panel يستخدم `ZERO_CONFIG_PATH`/`/opt/zero/config/panel.yaml`، بينما listener يستخدم `/opt/zero/config/zero.yaml` (`run_panel.py:40` و`run_listener.py:45`).

## وحدات systemd

Listener يعمل كمستخدم `zero:zero`، ويستخدم optional EnvironmentFile، و`NoNewPrivileges` وحماية filesystem و`ReadWritePaths` محددة (`deploy/systemd/zero-listener.service:5-26`).

Office worker يتطلب OfficeCLI ويمر عبر `ExecCondition`، ويستخدم `PrivateNetwork` و`AF_UNIX` فقط، وذاكرة 1G وCPU 100% و256 tasks ومدة 15 دقيقة (`deploy/systemd/zero-office-worker.service:3-38`).

توجد وحدات أخرى في `deploy/`، منها panel وخدمات Telegram source digest/join. في غياب Git لا يمكن مقارنة source والمثبت تاريخياً عبر diff.

## الخدمات المساعدة

`zero-tg-source-digest.service` و`zero-tg-source-join.service` خدمات oneshot ذات timers منفصلة؛ digest تقريباً كل ثلاث ساعات وjoin تقريباً كل عشر دقائق. ليست loops داخل listener.

## runtime وfail-closed

توجد مسارات صريحة لـ logs وstate وpids وoffice_jobs وoffice_ingest. يقوم `office_health.py` بعرض الحالة ويمنع `office_feature_enabled.py` تشغيل worker عند التعطيل. لا ينشئ listener Office repository/bridge/coordinator إذا كان Office معطلاً (`run_listener.py:91-126` و`:586-598`).

## Scripts المساعدة

`login_tgsearch.py` و`join_tgsearch_channels.py` و`real_tests.py` و`live_*` وprototype وmigration وbenchmark scripts قد تستخدم الشبكة أو الجلسات أو DB. لا تعتبر خدمات production إلا إذا ربطتها وحدة أو call site موثق.