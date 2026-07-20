# دليل الوحدات وحدود التغيير

هذا الدليل مبني على فحص AST والاستيرادات ومواقع الاستدعاء. المسؤوليات تصف السلوك الحالي، لا تصميماً مستقبلياً.

## النواة

- `zero/brain.py` — orchestration للرد، prompts، أمن الخرج، الذاكرة، السوق، الويب، الوسائط والملصقات. هو أكثر أجزاء النواة ترابطاً.
- `zero/models.py` — data classes: `IncomingMessage` و`Decision` و`RouteResult` و`UserProfile`.
- `zero/prompts.py` — builders للرد وstarter وsummary.
- `zero/persona.py` — بناء persona block.
- `zero/triggers.py` — normalization وtriggers وقرارات الرد.
- `zero/security.py` — تصنيف النية والطلبات الخطرة والردود الأمنية الثابتة.
- `zero/moderation.py` — مساعدات spam/abuse.

## الإعدادات والعمليات

- `zero/config.py` — نماذج Pydantic، تحميل YAML، secret-file وOffice env overrides. أضف الإعدادات هنا.
- `zero/logging_utils.py` — تهيئة logging.
- `zero/management.py` — تحميل bot token من ملف خاص وإرسال رسائل bot.
- `zero/runtime_control.py` — هوية العملية والتحكم في listener.
- `scripts/office_health.py` و`scripts/office_feature_enabled.py` — health وfail-closed لـ Office.

## Telegram وsocial والوسائط

- `scripts/run_listener.py` — composition root ومعالجات Telethon والمهام الخلفية.
- `scripts/run_panel.py` — composition root لـ aiogram/panel.
- `zero/social.py` و`social_awareness.py` و`social_plus.py` — سياسة التفاعل وfeedback.
- `zero/reactions.py` — اختيار reactions وcooldown.
- `zero/vision.py` — تنزيل الوسائط والرؤية والحدود.
- `zero/stickers/` — observer وclassifier وlibrary وsender وpanel وaccount saver وmodels.
- `zero/telegram_search.py` — عميل وبوابة وسياق بحث Telegram.

## الذاكرة والسياق

- `zero/storage.py` — `ZeroStore` ومخطط SQLite المركزي.
- `zero/memory.py` — استخراج مرشحي الذاكرة والسياسة النصية.
- `zero/memory_context.py` و`memory_planner.py` — تركيب سياق الذاكرة للـ prompt.
- `semantic_memory.py` و`experience_memory.py` و`procedural_memory.py` و`world_model.py` — طبقات دائمة منفصلة.
- `memory_v2/service.py` و`retrieval_planner.py` — Memory V2 والتخطيط للاسترجاع.
- `deferred_memory.py` — الذاكرة المؤجلة والتوقيت/الموافقة.
- `group_context.py` و`document_bundles.py` — سياق المجموعة وحزم المستندات.
- `identity.py` — مفاتيح الهوية canonical.

## الويب والمعرفة والسوق

- `zero/web.py` — facade وكاشف نية البحث.
- `zero/web_search/` — النماذج، إعادة كتابة query، pipeline، providers، extraction، ranking، dedup، cache، truth guard والحالة.
- `google_grounding.py` — Google grounding.
- `knowledge.py` — policy والاسترجاع والـ backends وworker المعرفة.
- `market_prices.py` — Binance/Navasan/Nobitex وcache.
- `tg_source_manager.py` و` scripts/tg_source_manager.py` — اكتشاف المصادر والـ manifest.

## Proactive والمهام المجدولة

- `proactive_followups.py` — orchestration للمتابعات.
- وحدات `proactive_*` — scheduler/policy/outcome/feedback/transport/rollout.
- `template_jobs.py` — jobs مبنية على templates مع parsing أمني.

## Office

- `command_gate.py` — parser deterministic للأوامر المدعومة.
- `telegram.py` — حد attachment/reply/user/chat إلى intake.
- `intake.py` — request relationship والملف وpreflight وquota.
- `text.py` — normalization وحساب تاريخ quota.
- `preflight.py` — فحوص OOXML وMIME وarchive والأمن.
- `workspace.py` — حصر المسارات والعزل.
- `db.py` — state machine وquota وlease وevents وoutbox وmigration.
- `planner.py` — JSON plan وcanonicalization المحدود.
- `adapter.py` — schema للعمليات وحد OfficeCLI subprocess.
- `worker.py` — التخطيط والتنفيذ والتحقق والإصلاح.
- `delivery.py` — التسليم idempotent وreceipt/quota وvisual review.
- `cleanup.py` — تنظيف retention.

## الأرشيف والنماذج الأولية

`archive/` و`prototype` و`real_tests.py` و`live_*` وbenchmark/migration scripts ليست بالضرورة مسارات production. يجب فحص آثارها الجانبية قبل التشغيل.