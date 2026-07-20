# Import graph و روش بازتولید inventory

## خلاصه‌ی فعلی

AST scan read-only روی `zero/`، `scripts/`، `tests/` و `panel/` انجام شد:

- ۱۹۴ فایل Python
- ۲۶٬۵۲۷ خط فایل
- ۸۸ فایل زیر `zero/`
- ۲۴ script
- ۸۲ test
- ۰ syntax parse error در فایل‌های scan‌شده

این شمارش شامل test و script است؛ «حجم source خالص» به تعریف پروژه وابسته است و با LOC این سند یکی نیست.

## بازتولید بدون تغییر repository

```bash
cd /root/zero
python3 - <<'PY'
import ast
from pathlib import Path
root=Path('.')
rows=[]
for base in ('zero','scripts','tests','panel'):
    p=root/base
    if not p.exists(): continue
    for f in p.rglob('*.py'):
        if '__pycache__' in f.parts: continue
        text=f.read_text(encoding='utf-8')
        ast.parse(text)
        rows.append((str(f), len(text.splitlines())))
print('files=', len(rows), 'loc=', sum(n for _,n in rows))
PY
```

برای dependency graph، importها را با `ast.Import` و `ast.ImportFrom` بخوانید؛ import graph الزاماً runtime call graph نیست. Dynamic imports، decorators، callbacks و Telegram event registration باید از call site دستی verify شوند.

## اصول تفسیر

- import شدن یک module یعنی dependency compile-time، نه الزاماً فعال‌شدن feature.
- ساختن object در composition root evidence فعال‌شدن است.
- `asyncio.create_task` و `client.on` evidence مسیر runtime/background هستند.
- systemd `ExecStart` evidence entrypoint deployment است.
- test import/fixture evidence production wiring نیست.
