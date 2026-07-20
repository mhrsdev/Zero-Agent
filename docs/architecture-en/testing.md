# Testing and Verification

## Actual structure

- `tests/` contains unit, integration, security, memory, proactive, web, panel, and Office tests.
- Last read-only collection: 541 tests collected.
- Full baseline result: 540 passed, 1 skipped.
- AST inventory found no parse errors in the 194 inspected Python files.
- `pyproject.toml` sets `pythonpath=["."]`, `asyncio_mode="auto"`, and `testpaths=["tests"]`.

## Important suites

- Identity/scope: `test_identity_*`, `test_cross_user_context_leakage.py`.
- Dedup/concurrency: `test_incoming_message_dedup.py` and storage tests.
- Memory security: `test_memory_v2_security.py`, `tests/memory_v2/security/`.
- Panel auth: `test_panel_api.py`, `test_security_hardening.py`.
- Office safety: preflight, command/config, queue, delivery, failure injection, Telegram bridge, worker, E2E, and real OfficeCLI integration tests.

## Canonical commands

```bash
cd /root/zero
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pip check
python3 - <<'PY'
import ast
from pathlib import Path
for base in ('zero','scripts','tests'):
    for p in Path(base).rglob('*.py'):
        if '__pycache__' not in p.parts:
            ast.parse(p.read_text(encoding='utf-8'))
print('AST parse: PASS')
PY
```

Live Telegram/provider checks require separate credentials, network, and state-safety evidence; local fixtures are not production E2E.

## Fixtures and documentation drift

Memory V2 fixtures include `tests/fixtures/memory_v2/regression_corpus.jsonl` and `real_anonymized_corpus.jsonl`. An older benchmark document refers to the nonexistent `tests/fixtures/memory_v2_cases.json` path.

`requirements.txt` mixes runtime and pytest dependencies while `requirements-dev.txt` labels pytest dependencies as test-only. The authoritative production/development installation split is not documented consistently.

## Coverage limitations

No coverage threshold or CI manifest was observed. Test count is not coverage. `real_tests.py` and `live_*` scripts depend on Telegram/provider credentials and must be reported separately from local pytest results.

New tests should assert boundary state and side effects, avoid secrets/raw user content, preserve identity scope, and distinguish fake/local integration from live external checks.