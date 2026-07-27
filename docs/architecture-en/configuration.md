# Configuration and Precedence

## Base sources

- Public example: `config/zero.example.yaml`
- Real config: `config/zero.yaml` (private)
- Loader: `ZeroConfig.load` in `zero/config.py:398-407`
- Validation: Pydantic models in the same file
- Default secret file: `<config-parent>/runtime/secrets/zero.secrets.yaml`; `ZERO_SECRET_FILE` can override it.

Actual load order:

```text
YAML
 → protected secret-file merge
 → secret placeholder validation
 → OfficeConfig.from_env
 → ZeroConfig.model_validate
```

## Configuration groups

`ZeroConfig` owns management bot, listener, persona, policy, router, memory, reporting, web, Telegram search, vision, stickers, reactions, Office, and logs (`config.py:379-395`).

## Secret policy

`_private_file` rejects group/world permission bits (`config.py:16-22`). Secret values are loaded from protected files and unresolved placeholders fail validation (`:32-66`). Never reproduce secret values in logs, panel responses, or documentation.

## Multiple configuration authorities

Configuration is not centralized:

- YAML controls most features.
- Office has an independent environment overlay.
- SQLite `settings` overrides selected feature gates/runtime state; confirmed readers include `zero/web.py:113-118`, `zero/vision.py:283-288`, `zero/telegram_search.py:370-379`, `zero/brain.py:378-379`, and `zero/limit_challenge.py:76-85`.
- Memory V2 has an independent DB path and environment flags (`memory_v2/service.py:19-25`, `brain.py:239`).

Every new setting should document its authority, precedence, and real reader call sites.

## Office environment precedence

`OfficeConfig.from_env` (`config.py:307-370`) applies environment values over YAML. Important variables include:

- `ZERO_OFFICE_ENABLED`
- `ZERO_OFFICE_CLI_PATH`, `ZERO_OFFICE_WORKSPACE_ROOT`
- quota: `ZERO_OFFICE_USER_JOBS_PER_DAY`, `ZERO_OFFICE_USER_MAX_CHARACTERS`, `ZERO_OFFICE_TIMEZONE`
- admin/rollout IDs
- file/archive/runtime/repair limits
- global/per-user concurrency and retention

## Validation

- Office quota timezone is validated with `ZoneInfo` (`config.py:225-237`).
- Per-user concurrency cannot exceed global concurrency; CLI and workspace paths must be absolute (`:297-305`).
- Numeric limits use Pydantic bounds (`:246-265`).

## Example-config correction

The example config mismatch found during the architecture audit was corrected before final delivery:

- `office.quota.timezone` and `office.quota.jobs_per_user_per_day` now match the Pydantic model.
- `max_zip_entries` now matches `OfficeLimitsConfig`.
- Regression test: `test_example_office_config_uses_model_field_names`.

The models still do not set `extra='forbid'`; unknown keys may therefore be silently ignored in other config sections. That remains a separate hardening opportunity, not a hidden claim of completion.

## Deployment path split

The listener hardcodes `/root/zero/config/zero.yaml` (`run_listener.py:45`), while the panel uses `ZERO_CONFIG_PATH` with `/etc/zero/zero.yaml` as its default (`run_panel.py:40`). Deployment must make this split explicit.