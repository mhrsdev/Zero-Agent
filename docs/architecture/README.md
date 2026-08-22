# مستندات معماری Zero

این پوشه مرجع مستندات مهندسی Zero است. محتوای آن از بررسی مستقیم source، importها، call siteها، configuration، SQLite schema، systemd units و تست‌ها تهیه شده است؛ نام فایل به‌تنهایی مبنای نتیجه‌گیری نبوده است.

## از کجا شروع کنم؟

1. [نمای کلی سیستم](system-overview.md) — مرز سرویس‌ها و مسئولیت‌ها.
2. [مسیرهای اجرای واقعی](runtime-flows.md) — listener، پیام، provider، memory، search، panel و Office.
3. [کاتالوگ ماژول‌ها](module-catalog.md) — محل مناسب تغییر در هر subsystem.
4. [داده و ذخیره‌سازی](data-and-storage.md) — SQLite، جدول‌ها، migrationها و ownership.
5. [Configuration](configuration.md) — منبع تنظیمات و precedence واقعی.
6. [عملیات و استقرار](operations.md) — entrypointها، systemd و runtime state.
7. [تست و verification](testing.md) — suiteها و محدودیت شواهد.
8. [راهنمای اعمال تغییر](change-guide.md) — قبل از دست‌زدن به هر boundary چه بخوانیم.
9. [یافته‌ها و عدم‌قطعیت‌ها](known-uncertainties.md) — چیزهای ناقص، مشکوک، آرشیوی یا نیازمند بررسی.

## وضعیت دامنه بررسی

- Root پروژه: `/opt/zero`
- زبان اصلی: Python؛ در `pyproject.toml`، pytest روی `tests/` تنظیم شده است.
- بررسی AST انجام‌شده: ۱۹۴ فایل Python، ۸۸ ماژول زیر `zero/`، ۲۴ script و ۸۲ فایل test؛ مجموع ۲۶٬۵۲۷ خط فایل‌های بررسی‌شده، بدون `runtime` و cache.
- `.git` در root وجود ندارد؛ تاریخچه، branch، diff و ownership بر اساس Git قابل تأیید نیست.
- runtime شامل DB، session، log، backup و خروجی‌های آزمایشی است و بخشی از source محسوب نمی‌شود.
- `config/zero.yaml` و secret/sessionهای واقعی عمداً در مستندات مقداردهی یا بازنشر نشده‌اند.

## قرارداد شواهد

ارجاع‌های این مستندات به شکل `path:line` هستند و باید هنگام تغییرات دوباره بازبینی شوند. عبارت‌های «در حال حاضر»، «مشکوک» یا «تأیید نشده» عمداً برای جداکردن مشاهده‌ی مستقیم از فرض معماری استفاده شده‌اند.
