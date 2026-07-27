# نمای کلی سیستم Zero

## هویت پروژه

README پروژه Zero را یک همراه مستقل Telegram معرفی می‌کند، نه بخشی از Hermes. دو سطح عملیاتی دارد:

- **Listener**: حساب کاربری Telegram با Telethon برای دریافت update و پاسخ در گروه‌های مجاز.
- **Management/Panel**: ربات مدیریت با aiogram و API/پنل aiohttp برای Owner.

منبع: `README.md:10-20`، `scripts/run_listener.py:20-43` و `scripts/run_panel.py:16-40`.

## نمودار مرزی واقعی

```text
Telegram MTProto user-session
        │
        ▼
 scripts/run_listener.py
        │  event handlers + global message lock
        ├── command/Office bridge (فقط اگر office.enabled)
        ├── ZeroBrain
        │     ├── trigger/security/policy
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
        ├── aiogram owner handlers
        ├── PanelAPI (aiohttp)
        ├── ZeroStore + memory services
        └── runtime_control → listener process

Office path (feature-gated)
 listener event → TelegramOfficeBridge → OfficeIntakeService
 → OfficeRepository / persistent job → listener coordinator
 → OfficePlanner → OfficeWorker → OfficeCLI adapter
 → validation/render/review → DeliveryCoordinator → Telegram + quota/outbox
```

نمودار بالا از importهای `scripts/run_listener.py:23-43`، ساخت سرویس‌ها در `:79-126`، taskهای runtime در `:685-696`، و `scripts/run_panel.py:57-79` استخراج شده است.

## مالکیت مسئولیت‌ها

| مرز | مالک فعلی | مسئولیت مشاهده‌شده |
|---|---|---|
| Telegram دریافت | `run_listener.py` | اتصال، authorization، event handlers، allowed chat، dedup و dispatch |
| تصمیم پاسخ | `zero/brain.py` | orchestration سطح پیام، prompt، security، memory، media و ارسال |
| provider | `zero/router.py` | pool کلید، quota/cooldown، fallback و HTTP call |
| ذخیره‌سازی اصلی | `zero/storage.py` | schema بزرگ SQLite و عملیات async حافظه/پیام/گروه/cron |
| پنل | `zero/panel_api.py` + `run_panel.py` | OTP، session، owner authorization، read/update API |
| Office persistence | `zero/office/db.py` | job، state transition، quota، lease، event، outbox |
| Office runtime | `zero/office/*` + `run_office_worker.py` | preflight، planning، adapter، worker، delivery و cleanup |
| configuration | `zero/config.py` | YAML، secret file، Pydantic و env override |

## مرزهای خارجی

- Telegram MTProto از طریق Telethon در listener.
- Telegram Bot API از طریق aiogram در panel و helper management.
- Gemini و OpenRouter در `zero/router.py` با `urllib.request`؛ هر provider متد HTTP مستقل دارد (`router.py:129-148`).
- Web/search providerها در `zero/web_search/` و `zero/web.py`.
- Market APIs در `zero/market_prices.py`.
- OfficeCLI یک subprocess خارجی است؛ اجرای آن از مسیر Office adapter انجام می‌شود، نه از prompt خام.

## نکته‌ی مهم درباره هم‌زمانی

Listener یک `asyncio.Lock` سراسری برای `_on_message` می‌سازد (`run_listener.py:149-150` و `:427-430`). این قفل جلوی پردازش هم‌زمان eventها در همان process را می‌گیرد، اما جایگزین constraint/transaction SQLite، quota اتمیک یا idempotency نیست. هر تغییر در این ناحیه باید هم تست تک‌پردازشی و هم تست restart/multi-worker داشته باشد.
