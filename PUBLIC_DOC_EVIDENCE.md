# Evidence Report — Public Documentation Proposal

## Scope

Repository inspected read-only from:

```text
https://github.com/mhrsdev/Zero-Agent.git
branch: main
HEAD: 1f7f1efcc09d83876f4fe00e572fbd24da0608eb
```

`INSTALLATION.md` was absent from this HEAD. No repository file, commit, branch, or remote was modified.

## Verification legend

- **IMPLEMENTED:** source code or a runtime entry point exists.
- **VERIFIED LOCALLY:** same-commit tests or local commands passed in this audit environment.
- **LIVE E2E VERIFIED:** real external account/provider/service was exercised. None of the external Telegram or paid-provider paths received this status.

## Findings

### Provider Registry wiring

- **IMPLEMENTED:** `zero.providers.ProviderRegistry` supports symbolic secret references, provider implementations, rate limiting, fallback chains, redacted descriptions, health, and usage accounting.
- **VERIFIED LOCALLY:** `tests/test_providers.py` and `tests/test_provider_runtime_wiring.py` pass; `IndependentRouter` registry delegation tests also pass.
- **Not default-wired:** `scripts/run_listener.py` and `scripts/run_panel.py` instantiate `IndependentRouter(config)` without passing a registry. Their default runtime therefore remains the legacy Gemini/OpenRouter key-pool router.
- **LIVE E2E VERIFIED:** no.

### Docker

- **IMPLEMENTED:** pinned two-stage `Dockerfile`, unprivileged `zero` user, `/data` volume, read-only runtime, health check at `/api/health`, and Compose services `zero-panel` and `zero-listener`.
- **VERIFIED LOCALLY:** Docker contract/static tests pass; the CI workflow defines image build and clean-container smoke commands.
- **BLOCKED locally:** Docker, Podman, and Buildah were unavailable, so no local image build or container run was performed.
- **Configuration concern:** Compose sets `ZERO_CANONICAL_CONFIG=/data/config/zero.json`, while legacy composition roots still load YAML through `ZERO_CONFIG_PATH` and default to `/opt/zero/config/zero.yaml`. The Compose file does not itself provision that legacy YAML path.
- **LIVE E2E VERIFIED:** no.

### Telegram Bot/User Session/Hybrid

- **Bot:** `run_panel.py` creates the aiogram management bot from a protected `BOT_TOKEN` file and enforces owner/private-chat access. **IMPLEMENTED; VERIFIED LOCALLY** by panel/auth tests; no live Telegram E2E.
- **User session:** `run_listener.py` creates a Telethon client from the legacy listener API/session fields and requires authorization plus configured group allowlists. **IMPLEMENTED; VERIFIED LOCALLY** by source/contracts; no live Telegram E2E.
- **Hybrid:** canonical configuration validates `disabled`, `bot`, `user_session`, and `hybrid` plus symbolic references; same-commit tests cover validation and Office Telegram bridge behavior. The default runtime remains two separate processes, not a single unified mode switch. **IMPLEMENTED as contract; VERIFIED LOCALLY; not live E2E.**

### Backup, restore, upgrade, rollback

- **IMPLEMENTED:** `scripts/backup_restore.py` supports `backup`, `restore`, and `verify`; it handles SQLite integrity and WAL/SHM sidecars.
- **VERIFIED LOCALLY:** real backup/restore cycle tests pass.
- **IMPLEMENTED:** Memory V1→V3 migration supports `--dry-run`, `--apply`, `--verify`, `--rollback`, backup path, and SHA-256 proof. Canonical JSON saves create `.bak` and `ConfigStore.rollback()` exists.
- **VERIFIED LOCALLY:** migration and upgrade/rollback tests pass using temporary real SQLite databases.
- **Boundary:** there is no generic `zero upgrade` command; live deployment rollback is not claimed.

### Office rendering

- **IMPLEMENTED:** explicit DOCX/XLSX/PPTX intake, OOXML preflight, quota/state machine, worker, OfficeCLI adapter, validation, preview rendering, optional visual review, and delivery outbox.
- **VERIFIED LOCALLY:** Office worker, adapter, failure-injection, and real OfficeCLI integration tests pass in this environment. The external executable path used by the tests is `/usr/local/lib/zero-office/officecli`.
- **LIVE E2E VERIFIED:** no Telegram delivery or production Office deployment was exercised.

### Panel/TUI

- **Panel:** **IMPLEMENTED; VERIFIED LOCALLY** through local API/auth/contract tests. It is an owner-protected `aiohttp` panel process with default loopback host and port `8787`; not publicly exposed by this audit.
- **TUI:** **IMPLEMENTED; VERIFIED LOCALLY**. Entry point is `python -m zero tui`; panels are `status`, `doctor`, `groups`, `backup`, `logs`, and `setup`.
- **LIVE E2E VERIFIED:** no remote/reverse-proxy deployment.

### Memory V1→V3

- **IMPLEMENTED:** current `MemoryV3Service`, V1→V3 migration module/script, verification and rollback paths exist; tests assert the normal prompt path uses V3 data rather than legacy V1 markers.
- **VERIFIED LOCALLY:** migration, memory contract, prompt-isolation, and tenancy tests included in the audit pass.
- **Boundary:** legacy V1/V2 modules remain in the tree; no live production migration was performed.

### Active web search and public exposure

- **IMPLEMENTED:** `HybridWeb` wires Google Grounding as primary and a local SearXNG JSON fallback pipeline with ordered engine groups. Web search is `web.enabled: false` in the example config.
- **VERIFIED LOCALLY:** web architecture/provider/SSRF/pipeline tests pass; no external endpoint was required for the tested contracts.
- **SearXNG status:** source-level legacy/internal code; disabled and not a public Zero feature or supported public fallback.
- **Telegram Search:** code exists, but legacy config defaults it to `enabled: false` and `archived: true`; panel documentation excludes it from the public release/UI. It is not advertised by the proposed README.
- **LIVE E2E VERIFIED:** no web provider or Telegram Search live run.

### Licensing conflict

- `LICENSE` contains Apache License 2.0.
- `PROPRIETARY_LICENSE` states proprietary/confidential, all rights reserved, and grants no permission.
- `NOTICE` also describes Apache licensing.

This is a direct unresolved conflict in the current HEAD. The proposed README does not call the project open source and does not grant usage, copying, modification, deployment, or redistribution rights.

## Local checks performed

- Python AST parse: 249 Python files, zero syntax parse errors.
- Targeted same-commit tests covering providers, registry wiring, Docker contracts, Telegram adapter contracts, memory migration, backup/restore, upgrade/rollback, Office, panel, admin API, and TUI: passed.
- `python -m zero version`: returned `0.1.0-alpha`.
- `python -m zero status`: returned the CLI status JSON.
- `python -m zero tui --print --panel doctor`: rendered diagnostics and correctly reported missing unconfigured runtime home/canonical config.
- Docker engine availability: unavailable; build/run therefore not claimed.

## Proposed files

Only these three documentation files are included in the review ZIP:

- `README.md`
- `INSTALLATION.md`
- `PUBLIC_DOC_EVIDENCE.md` (this report)
