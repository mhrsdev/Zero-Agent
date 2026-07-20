# مسارات التشغيل الفعلية

## 1. بدء listener

1. يقوم `scripts/run_listener.py:66-79` بتحميل `ZeroConfig` وتهيئة logging/storage.
2. يقوم `ZeroStore` بإنهاء claims القديمة (`:79-82`).
3. تُنشأ خدمات social وweb وrouter وknowledge وdeferred-memory (`:83-90`).
4. يتم اتصال Telethon والتحقق من authorization (`:89-100`).
5. تُنشأ `ZeroBrain`، وعند التفعيل تُنشأ Office repository/bridge/planners (`:101-126`).
6. تُحلّ IDs للمجموعات ويُعاد بناء short memory (`:128-142`).
7. تُسجل handlers والمهام الخلفية، ثم يتوقف البرنامج داخل `run_until_disconnected` (`:427-516` و`:685-696`).

## 2. مسار رسالة Telegram

يستدعي `events.NewMessage` في `run_listener.py:427-430` الدالة `_on_message` خلف القفل العام.

المراحل الفعلية:

- تسجيل صلاحية DM وإعطاء Office bridge فرصة استهلاك الرسائل الخاصة أولاً (`:152-165`).
- رفض المحادثات خارج allowlist الخاص بالـ ID أو username أو title (`:53-63` و`:167-169`).
- claim/dedup باستخدام `(platform, account_scope, chat_id, message_id)` (`:171-181`).
- مراقبة GIF وsticker قبل المسار العادي (`:183-205`).
- تجاهل رسائل Zero الذاتية للرد العادي (`:206-220`).
- إعطاء Office bridge فرصة ثانية في المجموعات المسموحة (`:222-229`).
- حل هوية المرسل وسياق reply والوسائط وthread وscope (`:231-303`).
- حفظ النشاط وsocial والfeedback والذاكرة (`:305-334`).
- معالجة deferred memory وإمكانية إنشاء reminder (`:335-345`).
- تحديث الإحصاءات وقد يأتي الرد من deferred memory مباشرة (`:346-360`).
- وإلا يتم استدعاء `brain.maybe_reply_with_media` (`:362-371`).
- تطبيق policy وno-reply وsupersession/delay، ثم إرسال الرد وحفظ delivery/social/memory (`:373-422`).

رسائل التعديل لا تعاد معالجتها إلا إذا خاطبت Zero حديثاً أو احتوت trigger أو كانت reply إلى Zero (`:432-447`). أحداث العضوية هي `ChatAction` حقيقية (`:449-507`). تحديثات reactions الخام تغذي social awareness (`:508-528`).

## 3. مسار النموذج

ينشئ `ZeroBrain` خدمات الذاكرة والويب والسوق والرؤية والملصقات وsocial وproactive (`brain.py:226-256`). يتبع `IndependentRouter.complete` primary/fallback من الإعدادات (`router.py:182-200`).

- `KeyPool.reserve` يستخدم قفلاً داخلياً لاختيار مفتاح سليم (`router.py:50-77`).
- أخطاء المزود تتحول إلى error types وcooldowns أو تعطيل المفتاح (`router.py:120-180`).
- لا تظهر قيم الأسرار في state/log بل معرفات مفاتيح مختصرة (`router.py:52-56` و`:92-94`).

## 4. مهام listener الخلفية

كل المهام التالية تعمل داخل عملية listener نفسها:

- Telegram health/reconnect كل 30 ثانية (`run_listener.py:664-683`).
- idle starter كل 300 ثانية (`:530-550`).
- inactive-member ping كل ست ساعات (`:552-573`).
- template jobs كل 30 ثانية (`:575-584`).
- Office coordinator كل ثانيتين عند التفعيل (`:586-598`).
- proactive followups مع interval/batch من env بعد clamp (`:600-616`).
- social reflection كل 24 ساعة (`:618-625`).
- group-memory يبدأ فوراً ثم كل 24 ساعة، رغم اسم الدالة monthly (`:627-637`).
- تقرير المالك اليومي يفحص كل دقيقة (`:639-662`).

## 5. اللوحة

ينشئ `run_panel.py:56-80` الخدمات و`PanelAPI`. المسارات في `panel_api.py:46-62` تشمل health، authentication، dashboard، chats، memory، knowledge، router، logs، jobs، users، sessions وsettings.

- صلاحية المالك تتطلب owner ID ومحادثة Telegram خاصة (`run_panel.py:43-48`).
- جلسات اللوحة وCSRF محفوظة في ذاكرة العملية (`panel_api.py:42-45` و`:86-98`).
- security headers وrate limiting عبر middleware (`:64-82`).

## 6. مسار Office

لا تُنشأ خدمات Office إلا إذا كان `config.office.enabled` مفعلاً. يحصل bridge على أولوية الرسائل الخاصة ويُستدعى أيضاً للمجموعات المسموحة (`run_listener.py:101-126` و`:158-165` و`:222-229`). يقوم coordinator كل ثانيتين باستعادة leases والتخطيط والإصلاح والمراجعة والتسليم (`:586-598`). worker المنفصل يبدأ من `scripts/run_office_worker.py:21-46`.