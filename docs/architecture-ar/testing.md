# الاختبارات والتحقق

## البنية الفعلية

- يحتوي `tests/` على اختبارات unit وintegration وsecurity وmemory وproactive وweb وpanel وOffice.
- آخر collection read-only: تم جمع 541 اختباراً.
- النتيجة الكاملة: 540 ناجحاً و1 متجاوزاً.
- لم تظهر أخطاء AST parsing في 194 ملف Python مفحوص.
- يحدد `pyproject.toml` مسار Python وasyncio auto و`tests` كمسار discovery.

## المجموعات المهمة

- الهوية والنطاق: `test_identity_*` و`test_cross_user_context_leakage.py`.
- dedup والتزامن: `test_incoming_message_dedup.py` واختبارات storage.
- أمان Memory V2: `test_memory_v2_security.py` و`tests/memory_v2/security/`.
- صلاحية panel: `test_panel_api.py` و`test_security_hardening.py`.
- Office: preflight وcommand/config وqueue وdelivery وfailure injection وbridge وworker وE2E وOfficeCLI integration.

## الأوامر القياسية

```bash
cd /root/zero
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pip check
```

فحوص Telegram/provider الحية تحتاج credentials وشبكة ودليل أمان للحالة؛ fixtures المحلية ليست production E2E.

## Fixtures وdrift

Fixtures الخاصة بـ Memory V2 هي `tests/fixtures/memory_v2/regression_corpus.jsonl` و`real_anonymized_corpus.jsonl`. توجد وثيقة benchmark قديمة تشير إلى `tests/fixtures/memory_v2_cases.json` غير الموجود.

يخلط `requirements.txt` بين runtime وpytest، بينما يصف `requirements-dev.txt` pytest بأنه test-only. مصدر التثبيت canonical للـ production/development غير موثق بشكل موحد.

## حدود التغطية

لم تتم ملاحظة threshold للتغطية أو CI manifest. عدد الاختبارات ليس coverage. تعتمد `real_tests.py` و`live_*` على credentials خارجية ويجب فصل نتائجها عن pytest المحلي.