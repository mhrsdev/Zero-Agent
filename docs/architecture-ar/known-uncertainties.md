# نقاط عدم اليقين وdrift

تفرق هذه الصفحة بين الحقائق المباشرة والأسئلة غير المحسومة.

## مؤكد من المصدر

- لا يوجد `.git`، لذلك لا يمكن التحقق من history/diff/branch.
- `run_listener.py` هو composition root لجلسة المستخدم، و`run_panel.py` هو composition root للإدارة.
- OfficeRepository يستخدم في runtime مسار DB نفسه الخاص بـ ZeroStore.
- Office gated في listener وworker.
- جلسات panel موجودة في ذاكرة العملية وليست صفوف DB دائمة (`panel_api.py:42-45`).
- يحتوي المستودع على archive وprototype وlive-test وmigration وbenchmark scripts، وليست كلها production paths.

## مناطق قديمة أو غير مؤكدة

- توجد وحدات listener متعددة تحت `deploy/systemd/` و`deploy/`؛ يجب تحديد الوحدة canonical.
- تختلف مسارات config الافتراضية بين panel وlistener؛ قد يكون ذلك مقصوداً أو drift.
- migration V1 الرئيسية موزعة وليست ذات version مركزي.
- بعض إعدادات panel القابلة للكتابة لا يوجد لها reader مؤكد: reactions و`social_enabled` وknowledge.
- لم يتم تأكيد consumer فعال لـ `memory_items_limit` و`summary_trigger_messages` و`per_user_profile_limit`.
- `TelegramSearchClient.enabled()` ثابت على false، رغم وجود gate آخر في `is_tool_enabled()` (`zero/telegram_search.py:368-379`).
- `zero.example.yaml` يحتوي Office keys لا تطابق نموذج Pydantic، ولا يوجد `extra='forbid'`.
- benchmark قديم يشير إلى fixture مفقود `tests/fixtures/memory_v2_cases.json`.
- توجد إشارات متعارضة حول ملكية dependencies بين `requirements.txt` و`requirements-dev.txt`.
- وجود scripts حية لا يثبت ربطها بـ CI أو production.

## ما لم ينفذ

لم يتم تغيير source أو runtime config أو DB أو migration أو service أو network behavior. لم يتم تنفيذ E2E خارجي أو migration، ولم يتم إعادة نشر أي secret أو token أو session أو محتوى DB/log.