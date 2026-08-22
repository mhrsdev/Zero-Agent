# ممیزی Memory و Context فعلی Zero

**وضعیت:** فقط خواندنی؛ این سند پیش از هر تغییر V2 تهیه شد.  
**مبنای بررسی:** پیاده‌سازی و تست‌های repository در `/opt/zero`، به‌علاوه SQLite زنده در `runtime/state/zero.db` (بدون خواندن محتوای خصوصی پیام‌ها).

## نتیجهٔ اجرایی

Zero اکنون چند لایهٔ حافظه دارد، اما یک «قرارداد context» واحد و بودجه‌بندی‌شده ندارد. مسیر پاسخ ابتدا چندین منبع را می‌خواند، سپس `compose_memory_context()` تقریباً همهٔ بخش‌ها را با cap کاراکتری مستقل به prompt می‌چسباند. این کار از نظر scope نسبتاً خوب است، ولی برای پیام ساده هم context قابل‌توجهی می‌سازد و retrieval را به intent/relevance سخت محدود نمی‌کند.

اندازهٔ storage با context یکی گرفته نشده است: آرشیو خام ۵۰۰۰ پیام نگه می‌دارد و compositor می‌تواند به‌تنهایی حدود ۳۰٬۸۰۰ کاراکتر (تقریباً ۷٬۷۰۰ token با تخمین ۴ کاراکتر/token) بسازد؛ جدا از persona، prompt ثابت، پیام فعلی، وب/ابزار و خروجی vision.

## شواهد زنده (بدون محتوای حساس)

اجرای بررسی در `runtime/state/zero.db`:

- `PRAGMA integrity_check = ok`
- اندازهٔ DB: `11,141,120` bytes
- `recent_messages`: 5,000 (در سقف retention فعلی)
- `short_term_context`: 1؛ `short_term_media_context`: 365
- `medium_term_memory`: 43؛ `long_term_memory`: 2؛ semantic active: 4 از 35 رکورد
- `memory_rag_documents`/FTS: 49/49
- `memory_audit`: 15,986؛ `inside_jokes`: 1,074؛ `social_threads`: 41
- 55 رخداد `MEMORY_CONTEXT_COMPOSED` در log: min/median/p95/max کاراکتر = `5,172 / 8,233 / 17,172 / 20,927`، یعنی تقریباً `1,293 / 2,058 / 4,293 / 5,232` token فقط برای `memory_context`.
- در همان log، 2,273 رخداد `MEMORY_RETRIEVED` ثبت شده است.

این آمار **اندازهٔ واقعی prompt کامل نیست**؛ prompt ثابت، persona، user message، web/Telegram context و tool result را شامل نمی‌شود.

## جریان واقعی

```text
Telegram update
→ scripts/run_listener.py::_on_message
  → reserve_incoming_message() (dedup claim)
  → social/reaction observers
  → ZeroBrain.remember_message(incoming)
     → recent_messages raw archive
     → scoped user profile refresh
     → media short context
     → semantic candidate/extract/approve
     → chat-wide short_term_context merge
     → every-10-message daily aggregate
     → explicit long / heuristic medium writes
     → RAG full-chat refresh after medium/long mutations
  → DeferredMemory.capture_note()
  → ZeroBrain.maybe_reply[_with_media]()
     → policy/router/security pre-check
     → get_recent(chat, 100)
     → retrieve_layered_memory(...)
     → social-plus/media lookup
     → compose_memory_context(...)
        → current profile + semantic + notes
        → reply chain + target user resolution
        → recent group flow + lexical historical matches
        → group monthly summary
        → ordinary layered memory + cue-gated RAG
     → build_reply_prompt(..., memory_context=...)
     → router/model [possibly tool loop]
  → Telegram send
  → ZeroBrain.remember_reply()
     → raw assistant reply archive + counters only
```

## فایل‌ها، کلاس‌ها و مسئولیت‌ها

| فایل | اجزای مرتبط | نقش فعلی |
|---|---|---|
| `zero/storage.py` | `ZeroStore`, `SCHEMA`, `_memory_key` | storage SQLite، raw archive، profile، short/medium/long، RAG/FTS، revisions/audit، summary و dedup claim |
| `zero/memory_context.py` | `compose_memory_context`, `_block`, `_line`, `_profile_lines` | composer اصلی context و target identity/reply-chain routing |
| `zero/memory.py` | sanitizers، `maybe_extract_memory`, `extract_*`, topic/mood | heuristic extraction و policy اولیهٔ حساس/کنترلی |
| `zero/semantic_memory.py` | `SemanticUserMemory` | factهای semantic per `(chat_id,sender_id)`، candidate/approve/supersede/forget و RAG sync |
| `zero/deferred_memory.py` | `DeferredMemory` | noteهای صریح و state/task/reminder مستقل؛ notes در composer خوانده می‌شود |
| `zero/experience_memory.py` | `ExperienceMemory` | root-cause/fix workflowهای تأییدشده؛ فقط plan `debug` آنها را render می‌کند |
| `zero/procedural_memory.py` | `ProceduralMemory` | workflow/runbook؛ مسیر عادی prompt فعلاً load نمی‌کند |
| `zero/world_model.py` | `WorldModel` | entities/relations معماری؛ فقط plan `world` load می‌کند |
| `zero/social_plus.py`, `zero/social_awareness.py` | social thread/joke context | group-social state؛ بخشی به context عادی اضافه می‌شود |
| `zero/memory_planner.py` | `plan_memory`, renderers | route بسیار سادهٔ keyword برای debug/world/semantic |
| `zero/brain.py` | `ZeroBrain._handle_no_media`, `remember_message`, `remember_reply`, summaries | orchestration retrieval/write/prompt/model |
| `zero/prompts.py` | `build_reply_prompt`, summary prompts | prompt injection و trusted/untrusted framing |
| `scripts/run_listener.py` | `_on_message`, monthly loop | Telegram handler، ordering و post-turn persistence |
| `scripts/run_panel.py`, `zero/panel_api.py` | memory commands/panel | owner inspection/correction/soft-clear/history |
| `zero/config.py` | `MemoryConfig` | DB path و retention limits؛ injection budget مستقل ندارد |

مرتبط ولی نه memory conversational: `zero/knowledge.py`/`knowledge_items` (public web knowledge)، `zero/telegram_search.py` (search state/cache)، `zero/stickers/*` (media library).

## Storage و schema

### Raw/session-like archive

`recent_messages` نگهدارندهٔ پیام‌های user و assistant است و با `(platform, account_scope, chat_id, telegram_message_id)` unique index دارد. `recent_messages_limit` پیش‌فرض config برابر 5000 **برای هر chat** است. `incoming_message_dedup` claim اتمیک برای update ورودی است.

### Identitiy/profile

- legacy `user_profiles`: sender-only و عمداً ambiguous باقی مانده است.
- `user_profiles_scoped`: canonical key برابر `(chat_id, sender_id)`.
- semantic tables نیز `(chat_id, sender_id)` دارند و candidate → approval → active/superseded lifecycle دارند.

### Memory layers

- `short_term_context`: **chat-wide**، TTL شش ساعت، یک row per chat. شامل active topic/mood و conversation pairs است.
- `short_term_media_context`: media per `(chat_id,message_id)`، TTL شش ساعت.
- `medium_term_memory`: event summary، participants JSON، TTL، importance/confidence/status.
- `long_term_memory`: category/content، subject optional، default expiry ۱۸۰ روز، revision/status.
- `memory_rag_documents` + `memory_rag_fts`: materialized index از active long/medium/semantic.
- `memory_revisions`, `memory_audit`: audit/soft-delete/restore.
- `user_memory_notes`: DeferredMemory table با retrieval lexical محدود.

### دیگر حافظه‌های مجزا

`experience_*`, `procedural_*`, `world_*`, social threads/jokes/profiles، knowledge memory و Telegram search cache همه DB مشترک دارند، ولی lifecycle و renderer جدا دارند.

## Extraction و write lifecycle

### زمان اجرا

`brain.remember_message()` پیش از تصمیم/مدل برای هر inbound message اجرا می‌شود. بنابراین write path با critical path پاسخ گره خورده است.

1. raw archive همیشه insert می‌شود (`append_recent`)؛ assistant reply نیز بعد از send archive می‌شود.
2. profile برای human refresh می‌شود.
3. bot messages از user-memory layers رد می‌شوند، ولی raw archive و group summary input می‌شوند.
4. untrusted memory-control text short/semantic/medium/long را skip می‌کند؛ raw archive از قبل نوشته شده است.
5. semantic extraction regex-based است؛ candidateهای چند category auto-approve می‌شوند.
6. short context برای هر human message با topic/mood heuristic merge می‌شود.
7. `message_id % 10 == 0` aggregate daily summary می‌سازد.
8. medium candidate از markerهای broad مثل «پروژه»، «باگ»، «فردا» ساخته می‌شود؛ text خام تا 1200 char summary می‌شود.
9. long candidate فقط explicit memory request و nickname/group preference دارد.
10. هر add/update medium/long، `refresh_rag_index(chat_id)` کل RAG همان chat را delete/rebuild می‌کند.

### Dedup/contradiction موجود

- medium: key `(chat_id, normalized topic, same participant set)`؛ content متفاوت با confidence بالاتر overwrite می‌شود.
- long: فقط یک active row برای `(chat_id, category, subject)`؛ same content dedup، higher/equal confidence in-place revision.
- semantic: `(chat_id,sender,category,key)` versioned؛ active predecessor `superseded` می‌شود.
- raw: scoped unique Telegram message index و incoming claim.

## Retrieval و scoring

`retrieve_layered_memory()` ابتدا scope user را برای medium/long اعمال می‌کند. سپس lexical term overlap را محاسبه می‌کند:

```text
retrieval_score = 0.55 * relevance + 0.30 * confidence + 0.15 * recency + 0.05 * importance
```

مشکلات فرمول:

- additive است؛ relevance صفر می‌تواند با confidence/recency score مثبت بگیرد، هرچند بعداً برای query دارای term فیلتر relevance>0 اجرا می‌شود.
- `importance` عملاً در rowهای long وجود ندارد و semantics یکسان نیست.
- `scope_match` شرط سختِ یکپارچه در pipeline ندارد؛ هر layer جدا policy دارد.
- decay، threshold، diversity و token packing query-aware واقعی ندارد.

RAG فقط با cueهای محدود مثل «یادت/قبلاً/اسم من» فعال می‌شود و FTS BM25 را با personal/current sender filter اجرا می‌کند. Target RAG جداست. Semantic retrieval در composer **بدون query scoring** و تا شش item برای current/target inject می‌شود.

## Prompt injection و اندازه

`build_reply_prompt()` memory context را در بخش `کانتکست حافظهٔ جدید` قرار می‌دهد و rules می‌گوید memory داده است، نه identity/control plane. Web evidence wrapper دارد، اما wrapper مشابهِ صریح «reference-only / non-instructional / provenance» برای همهٔ memory records وجود ندارد.

`compose_memory_context()` همیشه این sectionها را می‌سازد، حتی اگر خالی باشند:

- current identity: 1,800 char
- current user memory: 2,200
- reply chain: 3,200
- target user: 4,600
- recent group flow: 5,200
- relevant historical messages: 3,200
- monthly group summary: 2,200
- ordinary: 4,200
- RAG: 4,200

جمع capها ≈ 30,800 char. `_block` از cap عبور نمی‌کند ولی برای item oversized، آن را skip می‌کند؛ token-aware نیست و balance بین sectionها ندارد.

مسیر اضافی در `_handle_no_media()` ابتدا `memory_lines` را با budgetهای بسیار بزرگ 10k/8k/7k char می‌سازد، اما سپس فقط media/social lines به composer منتقل می‌شوند؛ medium/long lines دوباره از `layered` render می‌شوند. این مرحله redundant است و log/token telemetry آن با context نهایی یکی نیست.

## تفکیک scope فعلی

- personal semantic/profile: chat + sender صحیح.
- target user: reply chain و exact mention resolve، جدا render می‌شود؛ ambiguity به model داده می‌شود.
- group: recent flow، short context و monthly summary chat-wide.
- project/procedural/experience/world: separate stores، اما routing keyword-based و فاقد unified scope model.
- session: raw recent + chat-wide short; session boundary مستقل و structured session summary وجود ندارد.

## نقاط پرخطر / علت رشد و آلودگی

1. **Context همیشه-on:** composer group flow، current semantic/profile و monthly/ordinary sections را برای casual message هم ایجاد می‌کند؛ casual gate ندارد.
2. **Context budget بزرگ و char-based:** p95 حدود 4.3k token memory-only، بالاتر از هدف V2 500–900 token.
3. **Raw recent injection:** 20 recent group messages، 10 lexical historical message، و reply chain با متن raw در context می‌آیند؛ forwarded/quoted/bot/tool-origin policy به‌صورت canonical envelope/normalizer اجرا نشده است.
4. **Short context chat-wide:** `active_topic` و mood می‌تواند group state باشد ولی ordinary memory همراه personal items دیده می‌شود؛ risk attribution در prompt کم شده ولی data model separable نیست.
5. **Heuristic medium extraction aggressive:** هر متن با marker broad مانند پروژه/باگ/فردا و طول >=18، event می‌شود؛ quote/forward/tool output filtering در extractor وجود ندارد.
6. **RAG rebuild-on-write:** هر medium/long mutation کل chat RAG را rebuild می‌کند؛ latency write path و failure/caching inconsistency surface افزایش می‌یابد.
7. **دو writer family:** `ZeroStore` async lock و `SemanticUserMemory`/DeferredMemory synchronous connections/locks جدا دارند. SQLite WAL/busy_timeout کمک می‌کند، اما global transaction/serialization واحد ندارند؛ concurrent handler یا maintenance writer می‌تواند `database is locked`/index stale ایجاد کند.
8. **Global listener lock:** Telegram handler با `asyncio.Lock` serial است، اما monthly loop/panel/other processes بیرون آن‌اند.
9. **In-place long update:** revision/audit وجود دارد، ولی explicit `supersedes` و `contradicted_by` data model ندارد؛ semantic layer status را بهتر حفظ می‌کند ولی store layer نه.
10. **Summary drift:** periodic LLM summary input علاوه بر raw recent، memory items را هم می‌گیرد؛ merge summary در multi-chunk path دوباره summary را خلاصه می‌کند. این مسیر می‌تواند provenance و fidelity را تدریجاً ضعیف کند.
11. **Profile bloat:** `upsert_profile` arrays را union می‌کند و expiry ندارد؛ style_notes/topic/project که explicit نباشند در profile injection مستقیم نیستند، اما growth دائمی storage دارند.
12. **Duplicate/redundant retrieval path:** `_handle_no_media` layered context formats/dedups/trims، سپس composer همان layers را با section caps دوباره renders. این احتمال duplicate semantic content و telemetry mismatch دارد.
13. **Memory audit growth:** 15,986 audit rows و retention/rotation مشخصی در schema/config دیده نشد.

## Race/duplicate status

### محافظت موجود

- inbound dedup key scoped است و `reserve_incoming_message` atomic claim دارد.
- `recent_messages` scoped unique Telegram index دارد.
- `ZeroStore._lock` local-process serializes its methods; SQLite busy timeout/WAL فعال است.
- semantic candidate/approval از `BEGIN IMMEDIATE` استفاده می‌کند.

### باقی‌مانده

- `append_recent()` با `INSERT OR IGNORE` برای messageهای ingest می‌تواند duplicate raw جلوگیری کند، اما `remember_message()` پیش از/جدا از claim باید با همهٔ callerها audited بماند.
- source ids در memory rows bare Telegram IDs هستند، نه full `(platform,scope,chat,message)` provenance؛ cross-source audit/reconstruction ناقص می‌شود.
- RAG refresh whole-chat بعد از mutation ممکن است با separate semantic `_sync_rag_memory()` interleave کند.
- maintenance/monthly/panel و runtime همگی DB را می‌نویسند ولی transaction coordinator مشترک ندارند.

## تست‌های موجودِ مرتبط

- `test_memory_context_composer.py`: reply chain، target isolation، ambiguity، bounded sections، note retrieval، RAG failure.
- `test_memory_integrity.py`: medium/long/semantic dedup/conflict و deferred scope.
- `test_memory_identity_flow.py`, `test_memory_rag.py`, `test_memory_failure_states.py`, `test_cross_user_context_leakage.py`, `test_memory_security_layers.py`, `test_memory_gaps.py`: باید در فاز V2 به regression baseline تبدیل شوند.

## نتیجهٔ ممیزی

Zero از قبل اجزای مفید زیادی دارد: SQLite/WAL، scoped identity، FTS5، provenance محدود، soft-delete/revision، reply chain و target isolation. مشکل اصلی نبود storage نیست؛ **نبود pipeline انتخابی واحد بین storage و prompt** است. V2 باید archive را حفظ و write را از response path جدا کند، اما به‌جای هم‌زمان inject کردن چند «memory family»، ابتدا intent/scope/relevance gate و سپس packing سخت با budget token اعمال کند.
