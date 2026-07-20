# مهندسی معکوس Grok Build: Memory و Context Management

**منبع implementation:** clone عمومی `xai-org/grok-build` در revision `8adf901`؛ سرچ مفهومی repository و دنبال‌کردن source/tests.  
**تصحیح فرض اولیه:** این repository واقعاً یک memory subsystem دارد؛ صرفاً session/context management نیست. با این حال، آن subsystem experimental و workspace-centric است، نه طراحی‌شده برای identity/scope Telegram group.

## معماری واقعی

```text
experimental-memory flag / GROK_MEMORY=1
→ xai-grok-memory::MemoryStorage
   → global MEMORY.md
   → workspace-hash/MEMORY.md
   → workspace-hash/sessions/*.md
→ MemoryIndex (SQLite chunks + FTS5 + optional sqlite-vec)
→ MemoryBackendImpl
   → watcher sync-on-search
   → FTS candidates + evergreen supplemental candidates
   → optional embedding/KNN
   → temporal decay + source weight + access boost + MMR
→ tool / first-turn injection / post-compaction recovery

separately:
conversation state
→ token telemetry
→ threshold-triggered full-replace compaction
→ filter system/developer/tool material
→ retain user-query preamble + structured compact summary
→ host persists/resumes/checkpoints/sandbox state
```

## Long-term memory: واقعیت و قرارداد

### Storage

`crates/codegen/xai-grok-memory` نگه می‌دارد:

```text
~/.grok/memory/
  MEMORY.md                         global curated knowledge
  {workspace-hash}/MEMORY.md        workspace curated knowledge
  {workspace-hash}/sessions/*.md    session logs
  {workspace-hash}/index.sqlite     chunk + FTS5 + optional sqlite-vec
```

`MemoryStorage` workspace را با hash مسیر isolate می‌کند و workspace موقت را ephemeral می‌داند تا write project memory آنجا skip شود. Markdown برای edit انسانی است؛ index بر اساس hash chunkها incremental reindex می‌شود. write workspace/global memory simple overwrite/append است، اما index update transactionally chunks/FTS/vector rows را حفظ می‌کند.

### Retrieval

`MemoryBackendImpl` سه runtime call-site را با config یکسان پشتیبانی می‌کند: tool search، initial injection، post-compaction recovery. On-demand open-per-query SQLite دارد؛ watcher dirty fileها را قبل search reindex می‌کند و stale claim را reclaim می‌کند.

`hybrid_search`:

1. FTS5 candidate retrieval (always available)
2. supplemental query برای global/workspace curated files تا session logs آنها را crowd out نکنند
3. optional vector KNN اگر embedding/key/extension حاضر باشد
4. normalization/merge؛ FTS-only hit penalize نمی‌شود
5. boilerplate template filtering
6. temporal decay فقط برای `session`; global/workspace evergreen هستند
7. source weights + modest access boost
8. `min_score` hard filter
9. optional MMR diversity
10. `max_results` truncate

اگر vector/embedding unavailable باشد، **FTS-only graceful fallback** دارد؛ vector DB اجباری نیست.

### Configuration / tests

`MemorySearchConfig`: default max results 6, min score .35، temporal decay half-life 7 days، MMR opt-in. `MemoryIndexConfig`: 1600-char chunks, 320-char overlap. Tests FTS-only, vector fallback, threshold, source/decay, MMR, watcher/index sync را پوشش می‌دهند.

## Context/session management مستقل از long-term memory

### Compaction

`xai-grok-compaction` token counter host-specific را دارد و context size را به آستانهٔ درصدی window وصل می‌کند. Trigger نمونه: `last_prompt_tokens > context_window * threshold_percent`; failure non-fatal است.

full-replace/compact pipeline:

- system و developer prompt عادی را از compaction input حذف می‌کند.
- tool request/result را در inter-compaction حذف یا assistant visible text را strip می‌کند.
- user queryها را جدا در `<grok_user_queries>` حفظ می‌کند.
- prior compaction user-query blocks را پیش از LLM summary جدا می‌کند تا re-compaction snowball نشود.
- chunk summaries را structured assemble می‌کند.
- tool-result pruning config دارد: recent turns preserved؛ old huge output soft trim head/tail و سپس hard clear.

### Workspace/task/restart

Repository دارای durable session metadata، session resume/replica، checkpoint/sandbox hibernate/restore، workspace RPC، ACP/headless state و subagent plumbing است. این‌ها state اجرای coding agent هستند، نه user-memory Telegram. Public source نشان نمی‌دهد که این checkpoint‌ها برای profile/identity memory group bot طراحی شده‌اند.

## ارزیابی برای Zero

| قابلیت Grok Build | قوت | ضعف برای Zero group bot | تصمیم V2 |
|---|---|---|---|
| Global vs workspace memory | scope فکری روشن | workspace path ≠ Telegram user/group/project identity | **اقتباس مفهومی** به project vs user/group scopes |
| FTS-first, optional vector | dependency-light، graceful fallback | Markdown chunks برای atomic facts مناسب نیست | **اقتباس:** SQLite FTS5 first؛ vector adapter optional |
| Source-aware decay | session facts کهنه decay، curated knowledge evergreen | fact contradiction/status ندارد | **اقتباس با تغییر:** per-item TTL/status/validity؛ no blind evergreen |
| MMR | redundancy را کم می‌کند | lexical Jaccard روی short facts ممکن است overkill باشد | optional diversity gate، stdlib first |
| watcher/reindex claim | external edit + multi-process safety | Zero writes through DB، file watcher لازم نیست | کنارگذاشته می‌شود؛ transactional DB revision/index updates |
| initial injection | recall at session start | Telegram پیام casual/session-boundary دائمی ندارد | فقط query-gated retrieval، نه first-turn bulk injection |
| pre-compaction memory flush | پیش از حذف context preserve می‌کند | LLM flush می‌تواند noisy facts بسازد | session summary deterministic-first؛ async candidate review only |
| tool-result pruning | دقیقاً مشکل output bloat را حل می‌کند | needs envelope metadata | **اقتباس:** old tool results never injected; recent required only, hard cap |
| full-replace compaction | prevents linear history growth | summary may distort; not personal memory | **اقتباس:** structured Session Summary، update-on-state-change not prose chain |

## آیا Grok Build long-term memory دارد؟

**بله، اما experimental و workspace-centric.** Crate `xai-grok-memory` به‌صورت صریح cross-session knowledge persistence، global/workspace `MEMORY.md`، session logs، SQLite FTS5، optional vector similarity و auto-dream consolidation دارد. فعال‌سازی آن gate شده است (`--experimental-memory` یا `GROK_MEMORY=1`).

پس نتیجهٔ دقیق: Grok Build هم long-term memory **و هم** context/session management دارد؛ این long-term memory برای coding workspace است و مستقیماً مناسب multi-user Telegram identity isolation نیست.

## چه چیزی عمداً وارد Zero نمی‌شود

- هیچ crate/package/executable/API/config/path Grok Build.
- markdown file store و workspace hash به‌عنوان identity model.
- cloud embedding را prerequisite کردن.
- automatic full transcript-to-memory promotion.
- raw compaction summary به‌عنوان fact.

## اصول مستقل استخراج‌شده

1. archive زیاد باشد، injected candidateها محدود/score-gated باشند.
2. FTS5 baseline باید پیش از vector dependency کار کند.
3. vector failure نباید retrieval را fail کند.
4. curated knowledge، session events و raw history lifecycle متفاوت دارند.
5. compaction باید system/developer/tool payload را از summary input جدا کند.
6. prior summary metadata باید prevent-snowball داشته باشد.
7. token telemetry و thresholdها باید config/testable باشند.

## محدودیت مطالعه

این نتیجه فقط برای source عمومی revision ذکرشده است. بخش‌های backend/cloud که در public repository نیستند حدس زده نشده‌اند؛ از public code فقط type/protocol/telemetry boundaries توصیف شده است.
