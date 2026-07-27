# مسیرهای اجرای واقعی

## ۱. startup listener

1. `scripts/run_listener.py:66-79`، `ZeroConfig.load` و logger/storage را می‌سازد.
2. `ZeroStore` stale incoming claims را expire می‌کند (`:79-82`).
3. `SocialService`، `SocialAwareness`، `HybridWeb`، `IndependentRouter`، `KnowledgeWorker` و `DeferredMemory` ساخته می‌شوند (`:83-90`).
4. Telethon client متصل می‌شود و authorization بررسی می‌شود (`:89-100`).
5. `ZeroBrain` و در صورت فعال بودن Office، repository/bridge/planner/coordinatorها ساخته می‌شوند (`:101-126`).
6. group IDs از config/username resolve و short memory rebuild می‌شوند (`:128-142`).
7. event handlerها و background taskها ثبت و `run_until_disconnected` اجرا می‌شود (`:427-516` و `:685-696`).

## ۲. پیام Telegram

`events.NewMessage` در `run_listener.py:427-430` با lock سراسری `_on_message` را صدا می‌زند.

در `_on_message`:

- DM ورودی ابتدا `dm_allowed` را ثبت می‌کند؛ اگر Office bridge فعال و event را consume کند، همان‌جا return می‌کند (`:152-165`).
- گروه باید `_allowed_chat` را پاس کند (`:53-63`، `:167-169`).
- پیام با کلید `(platform, account_scope, chat_id, message_id)` در `incoming_message_dedup` claim می‌شود (`:171-181` و schema در `storage.py:46-64`).
- media observerهای GIF/sticker پیش از مسیر اصلی اجرا می‌شوند (`:183-205`).
- پیام self از پاسخ‌گویی عادی حذف می‌شود (`:206-220`).
- ادامه‌ی مسیر، بسته به متن/رسانه/trigger، به Brain، social، search، memory و ارسال Telegram می‌رسد؛ برای جزئیات این بخش باید متدهای بعد از `run_listener.py:220` و `ZeroBrain` را با call site تغییر دوباره بخوانید.

## ۳. پاسخ مدل

`ZeroBrain` در `brain.py:226-256` همه‌ی serviceهای memory، web، market، vision، sticker، social و proactive را نگه می‌دارد. `IndependentRouter.complete` ترتیب providerهای `normal_primary` و `normal_fallback` را می‌سازد (`router.py:182-200`).

- `KeyPool.reserve` با lock داخلی یک key سالم را انتخاب می‌کند (`router.py:50-77`).
- failureها به error type، cooldown و disable شدن key ترجمه می‌شوند (`router.py:120-180`).
- secretها در state/log با key id کوتاه‌شده نمایش داده می‌شوند؛ مقدار واقعی فقط در `_secrets` process memory می‌ماند (`router.py:52-56،92-94`).

## ۴. جزئیات بیشتر مسیر پیام و eventها

پس از dedup و Office boundary، listener sender/reply/thread/platform scope را resolve می‌کند، activity/social/memory را ثبت می‌کند، deferred memory را بررسی می‌کند، stats را افزایش می‌دهد و سپس `brain.maybe_reply_with_media` را صدا می‌زند (`run_listener.py:231-371`). پاسخ policy‌شده قبل از send بررسی supersession/delay می‌شود و بعد reply، delivery state، social action و memory ثبت می‌شوند (`:373-422`).

`MessageEdited` فقط editهایی را دوباره وارد مسیر می‌کند که تازه Zero را address کنند، trigger داشته باشند یا reply به Zero باشند (`run_listener.py:432-447`). `ChatAction` membership updates واقعی را برای join/leave پردازش می‌کند (`:449-507`) و `events.Raw` reaction feedback را به social awareness می‌دهد (`:508-528`).

## ۴. Background taskهای listener

در انتهای `run_listener.py` این taskها ساخته می‌شوند:

- Telegram health/reconnect: هر ۳۰ ثانیه (`:664-683`)
- starter: هر ۳۰۰ ثانیه، با probability/gap/policy (`:530-550`)
- inactive ping: هر ۶ ساعت (`:552-573`)
- template jobs: هر ۳۰ ثانیه (`:575-584`)
- Office coordinator فقط وقتی `config.office.enabled` است (`:586-598،:689-690`)
- proactive followups: interval و batch از env با clamp (`:600-616`)
- social reflection: هر ۲۴ ساعت (`:618-625`)
- group memory: تابعی با نام `monthly_group_memory_loop` در startup بلافاصله یک snapshot می‌سازد و سپس هر ۲۴ ساعت اجرا می‌شود؛ این رفتار از نام monthly کوتاه‌تر است (`:627-637`).
- daily report: هر دقیقه بررسی ساعت local (`:639-662،:694-695`)

هر task خطا را log می‌کند و معمولاً loop را متوقف نمی‌کند؛ با این حال failure semantics هر task یکسان نیست و باید هنگام تغییر همان loop بررسی شود.

## ۵. پنل و کنترل مدیریتی

`run_panel.py:56-80` سرویس‌ها و `PanelAPI` را می‌سازد و روی host/port env اجرا می‌کند. `PanelAPI` در `panel_api.py:46-62` routeهای health، auth، dashboard، chats، memory، knowledge، router، logs، jobs، users، sessions و settings را ثبت می‌کند.

- Owner-only check در `run_panel.py:43-48` به private chat و owner ID وابسته است.
- Panel session و CSRF در حافظه‌ی process نگهداری می‌شوند (`panel_api.py:42-45،86-98`).
- middleware security header و rate limit دارد (`:64-82`).
- static path با `resolve()` محدود می‌شود (`:109-113`).

## ۶. Office flow

Office در listener فقط وقتی ساخته می‌شود که `config.office.enabled` true باشد. event bridge در DM نیز قبل از پاسخ معمولی فرصت consume دارد (`run_listener.py:101-126،158-165`). coordinator listener هر دو ثانیه lease recovery، planning، repair، review و delivery را tick می‌کند (`:586-598`). worker جداگانه از `scripts/run_office_worker.py:21-46` jobهای persistent را claim/process می‌کند.

جزئیات boundaryها در `zero/office/command_gate.py`، `intake.py`، `preflight.py`، `planner.py`، `adapter.py` و `db.py` است. هیچ توسعه‌ای نباید command gate را به intent detector عمومی منتقل کند.
