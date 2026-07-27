# یافته‌ها، ابهام‌ها و بخش‌های نیازمند بررسی

این صفحه عمداً واقعیت‌های قطعی را از موارد مشکوک جدا می‌کند.

## قطعی از source

- `.git` در root موجود نیست؛ Git history/diff/branch قابل تأیید نیست.
- `run_listener.py` composition root اصلی user-session است و `run_panel.py` composition root مدیریت است.
- `ZeroStore` و OfficeRepository در runtime به مسیر DB config متصل می‌شوند؛ Office package DB مستقل خودکار نمی‌سازد.
- Office feature در listener و worker gate دارد و config پیش‌فرض example خاموش است.
- panel sessionها در process memory هستند، نه جدول durable؛ restart آن‌ها را از بین می‌برد (`panel_api.py:42-45`).
- source دارای archive، prototype، live test، migration و benchmark scriptهای متعدد است؛ همه production path نیستند.

## موارد مشکوک/قدیمی یا نیازمند تصمیم

- چند systemd unit برای listener در `deploy/systemd/zero-listener.service` و `deploy/zero-listener.service` وجود دارد؛ باید در deployment مشخص شود کدام canonical است.
- `run_panel.py` پیش‌فرض config path را `/etc/zero/zero.yaml` می‌گیرد (`:40`) ولی listener پیش‌فرض `/root/zero/config/zero.yaml` دارد (`run_listener.py:45`). این تفاوت ممکن است deliberate deployment behavior یا drift باشد؛ بدون بررسی نصب واقعی اصلاح نشود.
- README از aiohttp web panel، systemd و Office پشتیبانی می‌گوید، اما فهرست serviceهای نصب‌شده و source-control در نبود `.git` قابل اثبات کامل نیست.
- `zero/storage.py` schema بسیار بزرگ و مرکزی است؛ migration versioning باید پیش از توسعه‌های بعدی به‌صورت مستقل inventory شود، چون در این مرحله رفتار تغییر نکرده است.
- برخی config fields در مدل‌ها وجود دارند که باید با search call site بررسی شوند؛ صرف وجود field به معنی effective runtime control نیست.
- `runtime/` شامل artifactهای عملیاتی و test scriptهای موقت است؛ چون داده‌ی واقعی/خصوصی است در module catalog فقط به‌عنوان خارج از source آمده است.
- `panel_api.py` چند setting را allowlist و write می‌کند (`reactions_*`، `social_enabled`، knowledge settings)، اما reader runtime متناظر برای همه‌ی آن‌ها در call-site audit تأیید نشد؛ این‌ها نباید بدون بررسی بیشتر به‌عنوان effective control مستند شوند.
- `MemoryConfig` فیلدهایی مانند `memory_items_limit`، `summary_trigger_messages` و `per_user_profile_limit` دارد که consumer مؤثر آن‌ها در audit تأیید نشد؛ ZeroStore عمدتاً `recent_messages_limit` و `long_term_limit` می‌گیرد.
- `TelegramSearchClient.enabled()` در source hardcoded false است، در حالی‌که `is_tool_enabled()` مسیر DB/config gate دارد (`zero/telegram_search.py:368-379`).
- README inventory با package واقعی کامل هم‌تراز نیست و benchmark memory یک fixture path قدیمی دارد؛ این موارد اکنون ثبت شده‌اند، نه اصلاح.
- قابلیت‌های live web/Telegram search و providerها به credential/network وابسته‌اند؛ تست محلی آن‌ها معادل E2E production نیست.
- وجود `scripts/real_tests.py` و `scripts/live_search_e2e.py` به‌تنهایی نشان نمی‌دهد آن‌ها در CI یا systemd اجرا می‌شوند؛ call site اجرایی مشاهده‌شده برای production ثبت نشده است.

## چیزهایی که در این فاز عمداً انجام نشد

- هیچ source/config/runtime behavior تغییر نکرد.
- هیچ migration، service restart، network E2E، provider call یا DB mutation اجرا نشد.
- secret، token، Telegram session، DB content و log content بازنشر نشد.
- این مستندات ادعای کامل‌بودن coverage یا سلامت production نمی‌کنند؛ هدف آن‌ها نقشه‌ی evidence-backed برای توسعه‌ی بعدی است.
