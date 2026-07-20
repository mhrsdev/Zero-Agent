# الإعدادات والأولوية

## المصادر الأساسية

- المثال العام: `config/zero.example.yaml`
- الإعداد الحقيقي: `config/zero.yaml` وهو خاص
- loader: `ZeroConfig.load` في `zero/config.py:398-407`
- التحقق: نماذج Pydantic في الملف نفسه
- ملف الأسرار الافتراضي: `runtime/secrets/zero.secrets.yaml`؛ ويمكن لـ `ZERO_SECRET_FILE` تغييره.

ترتيب التحميل الفعلي:

```text
YAML
 → دمج ملف secrets المحمي
 → فحص placeholders
 → OfficeConfig.from_env
 → ZeroConfig.model_validate
```

## مجموعات الإعداد

يمتلك `ZeroConfig` إعدادات management bot وlistener وpersona وpolicy وrouter وmemory وreporting وweb وTelegram search وvision وstickers وreactions وOffice وlogs (`config.py:379-395`).

## سياسة الأسرار

ترفض `_private_file` صلاحيات group/world (`config.py:16-22`). تؤدي placeholders غير المحلولة إلى فشل validation (`:32-66`). لا يجب عرض الأسرار في logs أو panel أو docs.

## تعدد سلطات الإعداد

- YAML يتحكم بمعظم الميزات.
- Office لديه environment overlay مستقل.
- جدول SQLite `settings` يتجاوز بعض feature gates وحالة runtime؛ readers مؤكدة في `zero/web.py:113-118` و`zero/vision.py:283-288` و`zero/telegram_search.py:370-379` و`zero/brain.py:378-379` و`zero/limit_challenge.py:76-85`.
- Memory V2 لديه DB وenv flags مستقلان (`memory_v2/service.py:19-25` و`brain.py:239`).

## Office environment precedence

يطبق `OfficeConfig.from_env` (`config.py:307-370`) env فوق YAML، بما في ذلك enablement والمسارات والـ quotas والـ limits والـ rollout والـ concurrency والـ retention.

## Validation

- يتم التحقق من timezone عبر `ZoneInfo` (`config.py:225-237`).
- لا يجوز أن تتجاوز per-user concurrency قيمة global، ويجب أن تكون مسارات CLI/workspace مطلقة (`:297-305`).
- الحدود الرقمية لها قيود Pydantic (`:246-265`).

## تصحيح مثال config

تم إصلاح عدم التطابق الذي ظهر أثناء تدقيق المعمارية قبل التسليم النهائي:

- أصبحت `office.quota.timezone` و`office.quota.jobs_per_user_per_day` مطابقة لنموذج Pydantic.
- أصبح `max_zip_entries` مطابقاً لـ `OfficeLimitsConfig`.
- اختبار regression: `test_example_office_config_uses_model_field_names`.

لا تزال النماذج لا تستخدم `extra='forbid'`، لذلك قد يتم تجاهل مفاتيح غير معروفة في أقسام أخرى بصمت. هذه فرصة hardening مستقلة وليست ادعاءً مخفياً باكتمال كل الإعدادات.

## اختلاف مسار config

يستخدم listener `/root/zero/config/zero.yaml` (`run_listener.py:45`)، بينما يستخدم panel `ZERO_CONFIG_PATH` و`/etc/zero/zero.yaml` افتراضياً (`run_panel.py:40`). يجب جعل ذلك واضحاً في النشر.