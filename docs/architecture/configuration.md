# Configuration و precedence

## منبع پایه

- نمونه‌ی عمومی: `config/zero.example.yaml`
- فایل واقعی: `config/zero.yaml`؛ خصوصی و خارج از docs
- loader: `ZeroConfig.load` در `zero/config.py:398-407`
- schema/validation: Pydantic modelهای همان فایل
- secret file پیش‌فرض: `runtime/secrets/zero.secrets.yaml` نسبت به config path؛ env `ZERO_SECRET_FILE` می‌تواند آن را تغییر دهد (`config.py:402-404`).

ترتیب واقعی load:

```text
YAML
 → protected secret file merge
 → secret placeholder validation
 → OfficeConfig.from_env
 → ZeroConfig.model_validate
```

## گروه‌های config

`ZeroConfig` در `config.py:379-395` مالک `management_bot`, `listener`, `persona`, `policy`, `router`, `memory`, `reporting`, `web`, `telegram_search`, `vision`, `stickers`, `reactions`, `office` و `logs` است.

- listener: Telegram API/session/allowed groups.
- router: provider model/key/rate limits و primary/fallback.
- memory: DB path و سقف‌های context.
- web/search/vision/stickers/reactions: feature-specific controls.
- office: enabled، CLI/workspace، quota، limits، concurrency، retention، rollout.

## Secret policy

`_private_file` permission group/world را رد می‌کند (`config.py:16-22`). secret values از inline config یا protected file می‌آیند؛ placeholder resolve نشده خطا می‌دهد (`:32-66`). مقدار secret نباید در log/پنل/مستندات بازتولید شود.

## چند مرجع تنظیمات در runtime

مرجع تنظیمات یکپارچه نیست:

- YAML پایه برای بیشتر featureها استفاده می‌شود.
- `OfficeConfig.from_env` overlay مستقل دارد.
- جدول SQLite `settings` برای چند feature gate/runtime state روی YAML اثر می‌گذارد؛ call siteهای تأییدشده در `zero/web.py:113-118`، `zero/vision.py:283-288`، `zero/telegram_search.py:370-379`، `zero/brain.py:378-379` و `zero/limit_challenge.py:76-85` هستند.
- Memory V2 مسیر DB و flagهای env مستقل از `ZeroConfig.memory.db_path` دارد (`zero/memory_v2/service.py:19-25` و `zero/brain.py:239`).

برای هر setting جدید باید منبع authority، precedence و reader واقعی آن در docs ثبت شود.

## Office env precedence

`OfficeConfig.from_env` در `config.py:307-370` env را روی YAML اعمال می‌کند. نمونه‌های اصلی در `.env.example` هستند. مهم‌ترین‌ها:

- `ZERO_OFFICE_ENABLED`
- `ZERO_OFFICE_CLI_PATH`, `ZERO_OFFICE_WORKSPACE_ROOT`
- quota: `ZERO_OFFICE_USER_JOBS_PER_DAY`, `ZERO_OFFICE_USER_MAX_CHARACTERS`, `ZERO_OFFICE_TIMEZONE`
- admin/rollout IDs
- file/archive/runtime/repair limits
- global/per-user concurrency و retention

`from_env` type/boolean/int parsing دارد و model validation consistency را بعداً اعمال می‌کند (`:297-305`). برای تغییر config فقط YAML را patch نکنید؛ env، example و testهای `tests/test_office_command_config.py` را با هم بررسی کنید.

## Validationهای مهم

- Office quota timezone با `ZoneInfo` بررسی می‌شود (`config.py:225-237`).
- `per_user_jobs <= global_jobs`، CLI path و workspace root absolute هستند (`:297-305`).
- limitهای عددی با Pydantic `ge/le` محدود شده‌اند (`:246-265`).

## اصلاح نمونه config

ناسازگاری نمونه config که در audit معماری پیدا شد، پیش از تحویل نهایی اصلاح شد:

- `office.quota.timezone` و `office.quota.jobs_per_user_per_day` اکنون با مدل Pydantic هماهنگ‌اند.
- `max_zip_entries` اکنون نام واقعی `OfficeLimitsConfig` است.
- تست regression مربوط: `test_example_office_config_uses_model_field_names`.

مدل‌ها هنوز `extra='forbid'` ندارند؛ بنابراین ممکن است keyهای ناشناخته در بخش‌های دیگر بی‌صدا نادیده گرفته شوند. این یک hardening جداگانه است و به‌عنوان تکمیل‌شده پنهان نمی‌شود.

## پیکربندی فعلی ثبت‌شده

در زمان تهیه‌ی این docs، `config/zero.yaml` با feature Office خاموش و rollout lists خالی تنظیم شده بود؛ مقدار واقعی secretها در این سند ثبت نمی‌شود. تغییر runtime configuration خارج از scope این مستندسازی است.
