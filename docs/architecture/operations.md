# عملیات، entrypointها و استقرار

## Entry points

| فرمان/سرویس | فایل | اثر |
|---|---|---|
| listener | `python scripts/run_listener.py` | Telethon user-session، Brain و loopهای background |
| panel | `python scripts/run_panel.py` | aiogram bot + aiohttp panel |
| office worker | `python scripts/run_office_worker.py` | persistent Office worker؛ اگر Office خاموش باشد با کد ۰ خارج می‌شود (`:21-24`) |
| DB init | `python scripts/init_db.py` | ساخت/بازکردن ZeroStore روی DB config |
| Office health | `python scripts/office_health.py` | وضعیت feature و availability checks |
| Office condition | `python scripts/office_feature_enabled.py` | systemd ExecCondition fail-closed |

## Panel نصب‌شده و مرجع config

Panel نصب‌شده با `/opt/zero/config/panel.yaml` و secret file جدا اجرا می‌شود و روی `127.0.0.1:8787` bind می‌کند؛ source entrypoint آن `scripts/run_panel.py` است. Listener و panel هر دو سرویس مستقل‌اند.

Panel default config path با listener یکسان نیست: panel از `ZERO_CONFIG_PATH` و default `/opt/zero/config/panel.yaml` استفاده می‌کند (`scripts/run_panel.py:40`)، اما listener از `/opt/zero/config/zero.yaml` (`scripts/run_listener.py:45`). این را هنگام deployment صریح تنظیم کنید.

## systemd واقعی

`deploy/systemd/zero-listener.service`:

- User/Group: `zero`
- WorkingDirectory: `/opt/zero`
- optional EnvironmentFile: `/opt/zero/runtime/office.env`
- ExecStart: `.venv/bin/python scripts/run_listener.py`
- restart always، `NoNewPrivileges`، private tmp/devices، ProtectSystem و ReadWritePaths محدود (`:5-26`).

`deploy/systemd/zero-office-worker.service`:

- User/Group: `zero`
- شرط وجود OfficeCLI و `ExecCondition` feature flag (`:3-13`)
- `PrivateNetwork=true` و فقط `AF_UNIX` (`:17-32`)
- Memory 1G، CPU 100%، Tasks 256، Runtime 15min (`:33-37`)
- write paths به state/office jobs/ingest (`:38`).

واحدهای دیگری نیز در `deploy/` وجود دارند: `zero-panel.service`، Telegram source digest/join و listener unit قدیمی/موازی. قبل از نصب، source unit و installed unit را جدا مقایسه کنید؛ نبود `.git` اجازه‌ی ادعای برابری تاریخی نمی‌دهد.

## سرویس‌های auxiliary

`deploy/zero-tg-source-digest.service` و `deploy/zero-tg-source-join.service` oneshot هستند و timerهای جدا دارند: digest تقریباً هر سه ساعت و join تقریباً هر ده دقیقه (`deploy/*.timer`). آن‌ها background loop listener نیستند.

## Runtime directories

مسیرهای واقعی در config و serviceها hard-coded/explicit هستند: `/opt/zero/runtime/logs`، `/opt/zero/runtime/state`، `/opt/zero/runtime/pids`، `/opt/zero/runtime/office_jobs` و `/opt/zero/runtime/office_ingest`. داده‌های این مسیرها حساس‌اند و باید خارج از archive/docs بمانند.

## Health و fail-closed

`office_health.py` در حالت disabled فقط feature check را گزارش می‌کند و `office_feature_enabled.py` برای systemd شرط اجرای worker است. listener در حالت disabled Office repository/bridge/coordinator را نمی‌سازد (`run_listener.py:91-126،586-598`). این دو gate را هنگام هر تغییر Office با هم تست کنید.

## Rollout امن

Office باید در YAML/env خاموش بماند مگر rollout صریح و موقت. فعال‌سازی باید شامل backup، scope محدود، health، focused tests، E2E کنترل‌شده و rollback باشد. این سند هیچ rollout یا تغییر runtime انجام نمی‌دهد.

## اسکریپت‌های utility

`login_tgsearch.py`، `join_tgsearch_channels.py`، `real_tests.py`، `live_search_e2e.py`، prototypeها و migration/benchmark scriptها ممکن است network، session یا DB mutation داشته باشند. آن‌ها را production service فرض نکنید؛ قبل از اجرا source همان script و targetهای آن را بخوانید.
