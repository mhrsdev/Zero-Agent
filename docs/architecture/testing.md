# تست، baseline و verification

## ساختار واقعی

- `tests/` شامل تست‌های unit/integration/security/memory/proactive/web/panel/Office است.
- آخرین collection read-only: `541 tests collected`; نتیجه‌ی اجرای کامل شامل ۵۴۰ pass و ۱ skip بود.
- AST inventory این بررسی: ۸۲ فایل test و بدون syntax parse error در ۱۹۴ فایل بررسی‌شده.
- `pyproject.toml`، `pythonpath=["."]`، `asyncio_mode="auto"` و `testpaths=["tests"]` را تعریف می‌کند.
- dependency اصلی test در `requirements.txt` و test-only hints در `requirements-dev.txt` است.

## دسته‌بندی مهم

- Core/brain/router/triggers/security: testهای unit و policy.
- Storage/memory/memory_v2: persistence، identity، failure و adversarial.
- Web/search: intent، pipeline، extraction، truth و E2Eهای محدود.
- Proactive: scheduler/policy/outcome/transport/feedback/rollout.
- Panel: aiohttp test client و security behavior.
- Office: `test_office_*.py`، integration واقعی OfficeCLI و E2E محلی.

## فرمان‌های canonical

```bash
cd /root/zero
.venv/bin/pytest tests -q
.venv/bin/pytest tests/test_office_*.py -q
.venv/bin/python -m compileall -q zero scripts
.venv/bin/python -m pip check
```

فرمان‌های بالا برای verification هستند؛ `pip check` و E2Eهای live ممکن است خارج از محیط/credential در دسترس نباشند. نتیجه‌ی واقعی هر اجرا باید با timestamp و exit code ثبت شود؛ از تبدیل fixture به ادعای live production خودداری کنید.

## تست‌های حساس که قبل از تغییر باید خوانده شوند

- identity/scope: `test_identity_*` و `test_cross_user_context_leakage.py`
- dedup/concurrency: `test_incoming_message_dedup.py` و storage tests
- memory security: `test_memory_v2_security.py`، `tests/memory_v2/security/`
- panel auth: `test_panel_api.py` و `test_security_hardening.py`
- Office safety: `test_office_preflight_security.py`، `test_office_command_config.py`، `test_office_db_queue.py`، `test_office_delivery.py`، `test_office_failure_injection.py`

## Baseline ثبت‌شده در این بررسی

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests -q -p no:cacheprovider
540 passed, 1 skipped in 64.77s

.venv/bin/python -m pip check
No broken requirements found.

AST parse (zero/scripts/tests): PASS
Markdown local links: 9 checked, 0 broken
```

این baseline read-only بود و نتیجه‌ی آن فقط وضعیت همین checkout در زمان بررسی را نشان می‌دهد؛ live Telegram/provider E2E محسوب نمی‌شود.

## Fixtureها و محدودیت محیط

fixtureهای اصلی Memory V2 در `tests/fixtures/memory_v2/regression_corpus.jsonl` و `real_anonymized_corpus.jsonl` هستند. یک سند benchmark قدیمی به `tests/fixtures/memory_v2_cases.json` اشاره می‌کند که در checkout موجود نیست؛ این drift باید پیش از استفاده از benchmark اصلاح شود.

`requirements.txt` هم dependency runtime و هم pytest/pytest-asyncio دارد، در حالی‌که `requirements-dev.txt` همین test dependencyها را test-only معرفی می‌کند. منبع canonical نصب production/development در docs موجود فعلی یکدست نیست.

## پوشش و محدودیت شواهد

این repository config coverage threshold یا CI manifest مشاهده‌شده در این بررسی ندارد. تعداد testها به‌تنهایی coverage نیست. برخی scriptها (`real_tests.py`، `live_*`) به session/Telegram/provider وابسته‌اند و باید جدا از unit suite گزارش شوند.

هر test جدید باید:

1. اول boundary و failure را مشخص کند؛
2. assertion روی state/side effect داشته باشد؛
3. secret و raw user content چاپ نکند؛
4. در concurrency از SQLite constraint/transaction عبور کند؛
5. برای external integration mock/fake و live را جدا نام‌گذاری کند.
