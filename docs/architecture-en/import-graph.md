# Import Inventory and Reproduction Method

## Current inventory

A read-only AST scan over `zero/`, `scripts/`, `tests/`, and `panel/` found:

- 194 Python files
- 26,527 parsed file lines
- 88 files under `zero/`
- 24 scripts
- 82 Python test/support files
- 0 parse errors

This includes tests and scripts; “pure source size” depends on the project’s definition and is not identical to this LOC count.

## Reproduce without changing the repository

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

For an internal dependency graph, inspect `ast.Import` and `ast.ImportFrom`. An import graph is not a runtime call graph: dynamic imports, decorators, callbacks, and Telegram event registration require manual call-site verification.

Interpretation rules:

- An import proves a compile-time dependency, not feature activation.
- Object construction in a composition root is evidence of runtime wiring.
- `asyncio.create_task` and `client.on` are evidence of runtime/background paths.
- systemd `ExecStart` is deployment-entrypoint evidence.
- Test imports and fixtures do not prove production wiring.