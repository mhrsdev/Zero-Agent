# مهندسی معکوس Hermes Agent: Memory و Context

**منبع implementation:** clone عمومی `NousResearch/hermes-agent` در revision `10b6d1a`، بررسی مستقیم source و tests؛ نه وابستگی runtime برای Zero.

## نقشهٔ call graph

```text
agent_init
→ MemoryStore.load_from_disk()               # builtin MEMORY.md / USER.md
→ MemoryManager.initialize_all()             # builtin + حداکثر یک external provider
→ system_prompt.build_system_prompt_parts()
   → frozen builtin snapshot + provider static block

turn_context setup
→ MemoryManager.on_turn_start()
→ MemoryManager.prefetch_all(current user instruction)
→ conversation_loop injects fenced recall block near API call
→ LLM/tool loop
→ turn_finalizer
   → MemoryManager.sync_all(user, assistant, messages) [single background worker]
   → queue_prefetch_all(next-turn query)

session boundary/compression
→ commit_session_boundary_async(old transcript, new id)
   → on_session_end(old transcript)
   → on_session_switch(new id) in FIFO order
→ on_pre_compress(messages) contributes preservation hints to compression
```

## Built-in persistent memory

| قابلیت | رفتار واقعی | قوت | ضعف | مناسب Telegram group؟ | تصمیم Zero V2 |
|---|---|---|---|---|---|
| دو store مجزا | `MEMORY.md` برای environment/project facts و `USER.md` برای user preferences | جداسازی روشنِ fact پروژه از profile | markdown/file store و scope تک‌کاربره؛ fact atomic/query-aware نیست | فقط با scope بسیار سخت؛ فایل global برای group مناسب نیست | **اقتباس مفهومی:** Core User Profile و Project Memory جدا، اما SQLite scoped |
| frozen snapshot | memory files در start load می‌شوند، system prompt تا session/compression تغییر نمی‌کند؛ write همان لحظه durable است ولی injection turn جاری را عوض نمی‌کند | prompt cache پایدار و جلوگیری از self-amplification | correction فوری در turn بعدی هم تا rebuild دیده نمی‌شود | برای sessionهای بلند bot کامل نیست | **تغییر:** immutable identity/policy frozen بماند؛ retrieved memory per-turn و bounded باشد |
| hard cap | default `MEMORY.md=2200` و `USER.md=1375` char، entry delimiter `§` | size bound ساده و مؤثر | character cap، نه token/scoped relevance | Profile کوچک بسیار مناسب | **اقتباس:** Core profile bounded؛ cap token-based در renderer |
| add/replace/remove atomically | batch operation روی final budget، duplicate/ambiguous substring guard، atomic replace/file lock، drift backup | update/in-place conflict UX خوب و data-loss guard | substring identifiers شکننده؛ فایل semantics ندارد | owner panel مناسب است | **اقتباس:** add/update/supersede/soft-delete transactionally؛ ID/provenance جای substring |
| injection scan | strict threat scan هنگام write و snapshot؛ entry مشکوک در prompt با placeholder جایگزین می‌شود | poisoning persistence را fail-safe می‌کند | pattern-only، memory context را authoritative معرفی می‌کند | لازم | **اقتباس با تغییر:** sanitizer + reference-only wrapper، نه authoritative instruction |

### محل‌ها

- `tools/memory_tool.py::MemoryStore`: load/snapshot/write/batch/file locking.
- `agent/system_prompt.py::build_system_prompt_parts`: memory/user snapshot در volatile prompt tier.
- `agent/system_prompt.py::invalidate_system_prompt`: فقط پس از compression snapshot را reload می‌کند.

## Provider abstraction و lifecycle

`agent/memory_provider.py::MemoryProvider` قرارداد واضحی دارد: `initialize`, static `system_prompt_block`, per-turn `prefetch`, post-turn `sync_turn`, optional `on_session_end`, `on_session_switch`, `on_pre_compress`, `on_memory_write`, `on_delegation`, `shutdown`.

`agent/memory_manager.py::MemoryManager`:

- builtin + **فقط یک external provider** را نگه می‌دارد تا schema/tool conflict و context bloat کنترل شود.
- prefetch external را timeout-bound (8s) می‌کند؛ failure non-fatal است.
- `sync_all` و queued prefetch را در یک background executor تک‌worker انجام می‌دهد تا write order حفظ و response path block نشود.
- session-end extraction و session switch را در یک FIFO task انجام می‌دهد تا transcript قدیمی به session جدید misattribute نشود.
- mirror فقط بعد از memory tool write موفق/committed انجام می‌دهد، نه staged/failed result.
- subagent context را مستقل می‌داند؛ provider observation فقط parent-side `on_delegation` است.

**برای Zero:** interface provider مفید است، ولی V2 اکنون local SQLite only است. بنابراین `MemoryStore` protocol را برای backend swap نگه می‌دارد، اما plugin/external provider/tool schema اضافه نمی‌کند.

## Prefetch و prompt injection

`agent/turn_context.py` قبل از tool loop `prefetch_all(original_user_message)` را یک‌بار انجام می‌دهد. `conversation_loop.py` نتیجه را با `build_memory_context_block()` inject می‌کند.

Wrapper شامل `<memory-context>` و note است؛ manager fenceهای provider را sanitize می‌کند تا nested/injected context به UI leak نکند. Failure prefetch جواب اصلی را متوقف نمی‌کند.

**اقتباس:** one retrieval pass per turn، timeout/failure-safe، context wrapper واضح.

**کنارگذاشته می‌شود:** phrase «authoritative reference data»؛ در Zero memory ممکن است stale/disputed باشد، پس wrapper می‌گوید reference-only, non-instructional, provenance/confidence-aware.

## Session boundary و compression

- `on_session_end` فقط در boundary واقعی اجرا می‌شود، نه هر turn.
- `on_pre_compress` پیش از حذف turns preservation hints بازمی‌گرداند.
- `agent/context_compressor.py` memory files را authoritative خارج از compacted transcript نگه می‌دارد.
- session switch/compression lineage به provider اطلاع داده می‌شود.

**اقتباس:** Session Summary V2 فقط پس از state transition / boundary update می‌شود؛ raw archive summary نیست. Write pipeline async/serialized می‌شود.

## Conversation search و FTS5

`tools/session_search_tool.py` روی SQLite state DB کار می‌کند، FTS5 discover دارد، matched sessions را با lineage dedupe می‌کند و ±window + bookends می‌دهد. Tool call LLM ندارد، cross-profile read-only path هم دارد، automation sources demote و subagent/tool sessions hidden هستند.

**اقتباس:** FTS5 lexical search از archive، bounded anchored evidence، source-aware ranking/demotion، raw archive هرگز prompt default نیست.

## Memory vs skills

Hermes memory tool برای durable facts/preferences/environment است. Skills procedural reusable workflow هستند و جدا load می‌شوند. `MemoryManager._strip_skill_scaffolding()` user instruction را از expanded skill body جدا می‌کند تا prompt scaffolding وارد provider memory نشود.

**اقتباس:** Procedural Memory Zero به runbook/skill-like records جدا می‌رود. Tool output، quoted prompt، forwarded content و bot prose candidate fact نیستند.

## نکات اقتباسی نهایی

1. snapshot/live-state distinction و bounded write API مفید است.
2. one serialized background writer برای durability بدون افزایش latency ضروری است.
3. prefetch failure باید empty context بدهد، نه response failure.
4. session boundary extraction باید ordered باشد.
5. FTS search باید evidence-on-demand باشد، نه injection پیش‌فرض.
6. dual user/project separation مفید است، اما Zero به scopeهای Telegram (`chat_id`,`sender_id`) نیاز دارد.

## عمداً اقتباس نمی‌شود

- import/package/runtime/config/path Hermes.
- global markdown file as Zero database.
- system-prompt injection کل profile در هر session.
- external provider lifecycle/plugin tool schemas در فاز اول.
- treating recalled memory as authoritative instructions.

## محدودیت مطالعه

این سند فقط رفتار source عمومی revision مذکور را توصیف می‌کند. هیچ فایل `~/.hermes`، executable، API یا dependency Hermes در Zero V2 استفاده نخواهد شد.
