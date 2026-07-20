# فهرس الاستيرادات ومنهجية إعادة الإنتاج

## الفهرس الحالي

فحص AST read-only على `zero/` و`scripts/` و`tests/` و`panel/` وجد:

- 194 ملف Python
- 26,527 سطراً
- 88 ملفاً تحت `zero/`
- 24 script
- 82 ملف اختبار/دعم
- 0 parse errors

يشمل ذلك الاختبارات والـ scripts؛ حجم source الخالص يعتمد على تعريف المشروع.

## إعادة الإنتاج دون تغيير المشروع

```bash
cd /root/zero
python3 - <<'PY'
import ast
from pathlib import Path
rows=[]
for base in ('zero','scripts','tests','panel'):
    p=Path(base)
    if not p.exists(): continue
    for f in p.rglob('*.py'):
        if '__pycache__' in f.parts: continue
        text=f.read_text(encoding='utf-8')
        ast.parse(text)
        rows.append((str(f), len(text.splitlines())))
print('files=', len(rows), 'loc=', sum(n for _,n in rows))
PY
```

import graph ليس runtime call graph. الـ dynamic imports والـ decorators والـ callbacks وتسجيل Telegram events تحتاج فحص call sites يدوياً.

قواعد التفسير:

- import يثبت dependency compile-time ولا يثبت تفعيل feature.
- إنشاء object في composition root دليل wiring فعلي.
- `asyncio.create_task` و`client.on` دليل مسار background/runtime.
- `ExecStart` في systemd دليل entrypoint للنشر.
- imports وfixtures في الاختبارات لا تثبت wiring production.