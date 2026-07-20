# Known Uncertainties, Drift, and Unconfirmed Areas

This page deliberately separates direct facts from unresolved questions.

## Confirmed from source

- `.git` is absent, so Git history/diff/branch cannot be verified.
- `run_listener.py` is the main user-session composition root; `run_panel.py` is the management composition root.
- OfficeRepository receives the same configured DB path as ZeroStore in runtime.
- Office is feature-gated in both listener and worker.
- Panel sessions are process-memory state rather than durable DB rows (`panel_api.py:42-45`).
- The repository contains archive, prototype, live-test, migration, and benchmark scripts; they are not all production paths.

## Uncertain or stale areas

- Multiple listener units exist under `deploy/systemd/` and `deploy/`; the canonical deployment unit must be chosen explicitly.
- Panel and listener default to different config paths; this may be deliberate deployment behavior or drift.
- Primary V1 migration state is distributed rather than centrally versioned.
- Several Panel-allowlisted settings have no confirmed runtime reader: reaction settings, `social_enabled`, and knowledge settings.
- `MemoryConfig` fields such as `memory_items_limit`, `summary_trigger_messages`, and `per_user_profile_limit` had no confirmed effective consumer in the audit.
- `TelegramSearchClient.enabled()` is hardcoded false while `is_tool_enabled()` has a DB/config gate (`zero/telegram_search.py:368-379`).
- `config/zero.example.yaml` has Office field names that do not match the Pydantic model, and extra fields are not forbidden.
- An old benchmark document references a missing fixture path `tests/fixtures/memory_v2_cases.json`.
- `requirements.txt` and `requirements-dev.txt` give conflicting signals about dependency ownership.
- Runtime test/live scripts may use private state and network access; their existence does not prove CI or production wiring.

## Not done in this phase

- No source behavior, runtime configuration, DB, migration, service, or network behavior was changed.
- No provider/Telegram live E2E or migration was executed.
- No secret, token, session, DB content, or raw log content was reproduced.
- This documentation does not claim production health or code coverage; it is an evidence-backed map for future changes.