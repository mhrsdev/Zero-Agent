# Zero — Independent Telegram AI Companion

## License

**English**

Proprietary software. All rights reserved.

Access to this repository does not grant permission to use, copy,
modify, deploy, publish, or redistribute this project.

Written permission from the copyright holder is required for any use.

**فارسی**

این نرم‌افزار اختصاصی است و کلیه‌ی حقوق آن محفوظ است.

دسترسی به این repository هیچ اجازه‌ای برای استفاده، کپی، تغییر، استقرار،
انتشار یا توزیع مجدد این پروژه ایجاد نمی‌کند.

هرگونه استفاده نیازمند اجازه‌ی کتبی صاحب حق نشر است.

<p align="center">
  <b>یک همراه هوشمند، طبیعی و حافظه‌دار برای گروه‌های تلگرام</b><br>
  <i>An independent, safety-first Telegram AI companion built for natural group interaction</i>
</p>

---

## 📌 درباره پروژه

**Zero** یک ایجنت مستقل تلگرامی است که به‌عنوان یک عضو طبیعی در گروه‌ها فعالیت می‌کند؛ پیام‌ها را در محدوده‌ی مجاز می‌خواند، در زمان مناسب پاسخ می‌دهد، زمینه‌ی گفتگو را درک می‌کند و از حافظه‌های جداگانه برای شناخت بهتر گفتگوها استفاده می‌کند.

Zero یک چت‌بات ساده یا بخشی از Hermes نیست. Listener، روتینگ مدل، حافظه، تنظیمات، لاگ‌ها، rate limit و پنل مدیریتی آن کاملاً مستقل نگه داشته شده‌اند.

معماری Zero دو سطح جدا دارد:

1. **Zero Listener** — یک Telegram user-session با Telethon برای حضور و پاسخ‌گویی در گروه‌های مجاز.
2. **Zero Management Bot** — یک ربات مدیریتی محدود به Owner برای مشاهده‌ی وضعیت و کنترل سرویس.

---

## ✨ ویژگی‌های کلیدی

### 🧠 هسته‌ی هوش مصنوعی

- پاسخ‌گویی طبیعی و کوتاه با پشتیبانی از فارسی و گفتگوهای گروهی
- چند حالت شخصیتی: `normal`، `funny`، `sarcastic`، `serious`، `assistant`، `teacher` و `debate`
- تشخیص trigger، پاسخ مستقیم، reply، interjection و پیام‌های شروع‌کننده‌ی گفتگو
- روتینگ مستقل بین Gemini و OpenRouter
- چرخش امن چند API Key با weighted LRU، کنترل quota، cooldown و fallback
- retry و timeout برای خطاهای شبکه و provider
- عدم ثبت secretها در state یا لاگ‌ها

### 💾 حافظه‌ی چندلایه

Zero حافظه‌ها را به‌صورت ماژولار نگه می‌دارد تا داده‌های مختلف با هم مخلوط نشوند:

- حافظه‌ی کوتاه‌مدت پیام‌های اخیر
- پروفایل و علایق کاربران
- حافظه‌ی معنایی و حقایق تأییدشده
- حافظه‌ی تجربه و روندهای قبلی
- حافظه‌ی رویه‌ای برای الگوهای کاری
- مدل جهان و زمینه‌ی گروه
- خلاصه‌ی معنایی گفتگو و گزارش‌های دوره‌ای
- هویت هر کاربر بر اساس ترکیب `chat_id` و `sender_id`
- کنترل‌های فراموشی، اصلاح و محدودسازی حافظه

### 👥 آگاهی اجتماعی گروه

- شناخت اعضای گروه و زمینه‌ی تعامل‌های قبلی
- پاسخ‌های محدود و کنترل‌شده به پیام‌های دیگر بات‌ها
- رعایت opt-out برای تعاملات اجتماعی
- تشخیص لحن و احساس کلی پیام
- interjection تصادفی با فاصله‌ی زمانی و احتمال قابل تنظیم
- پیام شروع‌کننده‌ی idle با محدودیت زمانی
- جلوگیری از زنجیره‌ی بی‌پایان بات‌به‌بات

### 🔎 جست‌وجو و دانش

- مسیر جست‌وجوی وب با providerهای قابل تعویض
- Google Grounding و SearXNG به‌عنوان مسیرهای قابل تنظیم
- استخراج محتوای صفحات عمومی وب
- cache، deduplication، ranking و محدودیت زمانی جست‌وجو
- محافظ صحت پاسخ برای داده‌های عددی و قیمت‌های لحظه‌ای
- جست‌وجوی تخصصی تلگرام در قالب ماژول مستقل و محدودشده
- جلوگیری از نمایش نتیجه‌ی نامرتبط، لینک ناامن یا داده‌ی بدون منبع

> قابلیت‌های جست‌وجوی وب و تلگرام به‌صورت configuration-driven فعال یا غیرفعال می‌شوند و در حالت عمومی پیش‌فرض باید با دقت بررسی شوند.

### 📈 قیمت و بازار

مسیرهای جداگانه برای منابع مختلف بازار:

- Binance برای داده‌های کریپتو
- Nobitex برای قیمت USDT و تومان
- Navasan برای طلا و سکه

داده‌ها cache می‌شوند، قیمت‌های غیرمنطقی رد می‌شوند و منبع پاسخ باید با نوع دارایی سازگار باشد.

### 🖼️ رسانه، تصویر و استیکر

- تشخیص و پردازش تصویر، GIF و sticker
- پاسخ به پرسش‌های مرتبط با تصویر از طریق vision model
- محدودیت حجم فایل و تعداد درخواست‌های رسانه‌ای
- کتابخانه‌ی استیکر با دسته‌بندی، امتیاز کیفیت و انتخاب تصادفی
- ذخیره‌ی کنترل‌شده‌ی استیکرهای مناسب
- cooldown، سقف ساعتی و فاصله‌ی حداقل بین ارسال‌ها
- رعایت بازخورد منفی کاربر و توقف موقت ارسال خودکار
- جلوگیری از نشت marker داخلی استیکر در پاسخ نهایی

### ⏰ یادآوری و کارهای زمان‌بندی‌شده

- تشخیص درخواست‌های زمانی از زبان طبیعی
- یادآوری‌های deferred با scope دقیق بر اساس گروه و کاربر
- jobهای template-based با زمان‌بندی محدود و اعتبارسنجی‌شده
- جلوگیری از اجرای job خارج از محدوده‌ی امنیتی
- گزارش‌های دوره‌ای قابل تنظیم برای Owner

### 📄 Office Agent

- ساخت، خواندن و ویرایش محدود DOCX، XLSX و PPTX با OfficeCLI رسمی
- فعال‌سازی فقط با فرمان صریح `/docx`، `/xlsx` یا `/pptx`
- preflight امنیتی OOXML، سهمیه اتمیک، صف persistent و delivery idempotent
- worker غیر-root بدون شبکه، validation، render و repair محدودشده
- قابلیت پیش‌فرض غیرفعال است؛ راهنمای نصب و rollback: [`docs/office-agent.md`](docs/office-agent.md)

## 🧭 مستندات معماری برای توسعه‌دهنده

برای نقشه‌ی مستند و مبتنی بر source از [`docs/architecture/README.md`](docs/architecture/README.md) شروع کنید. این مجموعه مسیرهای runtime، import/call-site boundaries، storage، configuration، systemd، تست‌ها، محل مناسب تغییر و ابهام‌های شناخته‌شده را پوشش می‌دهد.

### 👑 پنل مدیریت

Zero یک Management Bot و پنل وب فارسی RTL دارد که برای کنترل سرویس طراحی شده است:

- مشاهده‌ی وضعیت Listener
- کنترل start، stop و restart
- بررسی وضعیت providerها، مدل‌ها، key pool و cooldownها بدون نمایش secret
- مشاهده‌ی آمار و لاگ‌های سرویس
- مدیریت تنظیمات، حافظه و jobها از مسیرهای مجاز
- ورود با Telegram OTP
- session و کد تأیید به‌صورت خام در UI ذخیره نمی‌شوند
- Cookie امن، CSRF، CSP و هدرهای امنیتی
- اتصال پنل فقط از طریق reverse proxy و HTTPS در محیط production

صفحات پنل فقط زمانی عملیات تغییر وضعیت انجام می‌دهند که API واقعی همان بخش آماده و تست شده باشد؛ عملیات جعلی یا داده‌ی placeholder عمداً استفاده نمی‌شود.

---

## 🏗️ معماری فنی

```text
                         Telegram
                    ┌────────┴────────┐
                    │                 │
          User Session / MTProto   Management Bot
              Zero Listener          Owner-only Bot
                    │                 │
                    └────────┬────────┘
                             ▼
                    ┌──────────────────┐
                    │   Zero Core      │
                    │ Brain + Policies │
                    └───────┬──────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
  Independent Router   Memory Layer        Tool Layer
  Gemini/OpenRouter   SQLite + Modules     Web/Vision/Market
        │                   │                   │
        └───────────────────┴───────────────────┘
                            │
                       Runtime State
                    DB / Logs / Sessions

Browser ──HTTPS──▶ Reverse Proxy ──loopback──▶ Panel API ──▶ Zero Services
```

### پشته‌ی فناوری

- **Language:** Python 3.11+
- **Telegram Listener:** Telethon
- **Management Bot:** aiogram
- **AI Providers:** Gemini و OpenRouter
- **Storage:** SQLite با aiosqlite
- **Configuration:** YAML با Pydantic validation
- **Web Panel:** رابط فارسی RTL با API داخلی
- **Runtime:** Linux و systemd
- **Testing:** pytest و pytest-asyncio

---

## 🔒 امنیت و جداسازی

- Listener فقط در گروه‌های allowlist‌شده فعالیت می‌کند.
- دسترسی Management Bot فقط برای Owner در چت خصوصی مجاز است.
- توکن‌ها، API keyها، sessionها و فایل‌های secret خارج از README و فایل‌های عمومی نگه داشته می‌شوند.
- secretها با شناسه‌ی hash‌شده در وضعیت provider نمایش داده می‌شوند، نه مقدار واقعی.
- لاگ‌ها نباید شامل token، key، session، مسیر خصوصی یا محتوای حساس باشند.
- برای پیام‌های هم‌زمان گروه، مسیرهای state و memory باید اتمیک و کنترل‌شده باشند.
- rate limit در سطح کاربر، گروه، provider، رسانه و تعاملات اجتماعی اعمال می‌شود.
- سرویس‌های systemd با کاربر محدود، `NoNewPrivileges`، filesystem protection و مسیرهای write محدود اجرا می‌شوند.
- پنل production باید پشت HTTPS و reverse proxy باشد.

---

## 🚀 راه‌اندازی

### پیش‌نیازها

- Python 3.11 یا بالاتر
- یک Telegram user account اختصاصی برای Listener
- `api_id` و `api_hash` تلگرام
- یک Management Bot از BotFather
- حداقل یک API key برای Gemini یا OpenRouter
- در صورت نیاز: سرویس SearXNG، Google Grounding و منابع بازار

### نصب وابستگی‌ها

```bash
git clone git@github.com:mhrsdev/ZeroAgent.git
cd ZeroAgent
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

### تنظیمات

فایل نمونه را کپی کنید و فقط مقادیر محیط خودتان را وارد کنید:

```bash
cp config/zero.example.yaml config/zero.yaml
```

مقادیر حساس را در فایل secret جداگانه و خارج از repository قرار دهید. این فایل‌ها را هرگز commit نکنید:

```text
config/zero.yaml
runtime/secrets/
runtime/state/
runtime/logs/
*.session
.env
```

تنظیمات اصلی شامل این بخش‌هاست:

- Owner و دسترسی Management Bot
- Telegram Listener و گروه‌های مجاز
- Persona و triggerها
- policy و rate limit
- providerها و quotaها
- حافظه و SQLite
- web search و Telegram search
- vision، sticker و reaction policy
- مسیر لاگ‌ها
- Office Agent، quota، sandbox و retention

### آماده‌سازی دیتابیس

```bash
python scripts/init_db.py
```

### اجرای Listener

```bash
python scripts/run_listener.py
```

### اجرای پنل و Management Bot

```bash
export ZERO_CONFIG_PATH=/path/to/zero.yaml
export ZERO_PANEL_HOST=127.0.0.1
export ZERO_PANEL_PORT=8787
python scripts/run_panel.py
```

در production، Listener و Panel را با systemd اجرا کنید و پنل را مستقیماً روی اینترنت expose نکنید؛ از reverse proxy و HTTPS استفاده کنید.

---

## 💬 تعامل با Zero

Zero برای کار در گروه طراحی شده و معمولاً با این روش‌ها فعال می‌شود:

- reply مستقیم به پیام Zero
- mention یا trigger تعریف‌شده
- پرسش واضح در گروه
- درخواست جست‌وجو، قیمت، یادآوری یا توضیح تصویر
- درخواست تغییر حالت گفتگو، در صورت مجاز بودن

رفتارهای خودکار مثل interjection، idle starter، sticker و reaction با policy و cooldown کنترل می‌شوند و نباید به‌عنوان پاسخ قطعی یا دائمی در نظر گرفته شوند.

---

## 📂 ساختار پروژه

```text
zero/
├── zero/                    # هسته‌ی اصلی
│   ├── brain.py             # تصمیم‌گیری و پردازش پاسخ
│   ├── router.py            # روتینگ مستقل providerها
│   ├── storage.py           # SQLite و state persistence
│   ├── memory.py            # استخراج و خلاصه‌سازی حافظه
│   ├── semantic_memory.py   # حافظه‌ی معنایی
│   ├── experience_memory.py # حافظه‌ی تجربه
│   ├── procedural_memory.py # حافظه‌ی رویه‌ای
│   ├── world_model.py       # مدل زمینه‌ی جهان/گروه
│   ├── social.py            # تعاملات اجتماعی
│   ├── reactions.py         # reaction policy
│   ├── vision.py            # تصویر، GIF و vision
│   ├── knowledge.py         # worker دانش و اعتبارسنجی منبع
│   ├── web.py               # لایه‌ی جست‌وجوی وب
│   ├── telegram_search.py   # جست‌وجوی تلگرام
│   ├── market_prices.py     # منابع قیمت بازار
│   ├── template_jobs.py     # jobهای زمان‌بندی‌شده
│   ├── panel_api.py         # API پنل مدیریت
│   └── config.py            # بارگذاری و اعتبارسنجی config
├── scripts/
│   ├── run_listener.py      # اجرای Listener
│   ├── run_panel.py         # اجرای Panel و Management Bot
│   └── init_db.py           # آماده‌سازی دیتابیس
├── panel/                   # رابط وب فارسی RTL
├── config/
│   └── zero.example.yaml   # تنظیمات نمونه بدون secret
├── docs/
│   └── zero-panel.md       # مستندات پنل و deployment
├── tests/                   # تست‌های focused
├── runtime/                 # state و log؛ عمومی نیست
├── requirements.txt
└── pyproject.toml
```

---

## 🧪 تست

```bash
pytest
```

برای بررسی type و syntax نیز می‌توانید از ابزارهای استاندارد Python استفاده کنید. تست‌های موجود بخش‌هایی مانند router، memory، search، policy، template jobs، market routing و integration را پوشش می‌دهند.

---

## ⚙️ اجرای production

نمونه‌ی سرویس‌های systemd در مسیر `deploy/` قرار دارند. قبل از فعال‌سازی:

- secretها و permission فایل‌ها را بررسی کنید.
- user اختصاصی سرویس و مسیرهای write را تنظیم کنید.
- گروه‌ها و کاربران مجاز را صریحاً در allowlist قرار دهید.
- پنل را فقط روی loopback bind کنید.
- HTTPS، reverse proxy، firewall و log rotation را فعال کنید.
- قبل از هر migration یا تغییر حساس، backup رمزنگاری‌شده و قابل‌تأیید تهیه کنید.
- لاگ‌ها را از نظر نشت secret و داده‌ی شخصی بررسی کنید.

---

## 🛡️ مالکیت و فایل‌های محلی

این پروژه اختصاصی است و تحت `PROPRIETARY_LICENSE` با عبارت **All Rights Reserved** ارائه می‌شود. هرگونه استفاده، کپی، تغییر، انتشار، استقرار یا توزیع بدون اجازه‌ی کتبی مالک ممنوع است.

فایل‌های زیر عمداً بخشی از مخزن نیستند و باید فقط روی محیط امن اجرا نگه‌داری شوند:

- `.env` و فایل‌های تنظیمات واقعی
- `runtime/` شامل دیتابیس، memory، session، log و state
- `backups/`، `archive/` و خروجی‌های فشرده یا رمزنگاری‌شده
- کلیدها، passwordها، tokenها و API keyها
- داده‌های واقعی کاربران و خروجی‌های runtime

برای شروع، از `config/zero.example.yaml` کپی بگیرید، مسیرها و شناسه‌های محیط خودتان را تنظیم کنید و secretها را خارج از repository قرار دهید. `.env.example` فقط فهرست نام متغیرهای محیطی را دارد و هیچ مقدار واقعی در آن نیست.

## 📜 License

See [`PROPRIETARY_LICENSE`](PROPRIETARY_LICENSE). This repository is not open-source and no rights are granted without the owner's prior written permission.

---

<p align="center">
  Built for focused, natural and safer Telegram interaction.
</p>
