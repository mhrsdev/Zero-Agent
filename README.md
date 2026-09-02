# Zero

Zero is a self-hosted Telegram AI companion in an alpha state. The `main` branch contains a Telegram user-session listener, an owner-only management bot, a local web administration panel, a terminal administration interface, SQLite-backed state, provider routing, controlled web search, memory services, and an optional OfficeCLI workflow.

Repository: <https://github.com/mhrsdev/Zero-Agent.git>

> **Alpha status:** This README describes the current `main`/`0.1.0-alpha` release line. It is not a production-readiness statement. `IMPLEMENTED` means source code exists; `VERIFIED LOCALLY` means the corresponding tests or local smoke command passed; `LIVE E2E VERIFIED` is reserved for a real external-account or provider run. This audit did not perform live Telegram or paid-provider E2E verification.

## Features

- Telegram user-session listener using Telethon, restricted by configured group allowlists.
- Owner-only Telegram management bot using aiogram.
- Local `aiohttp` administration panel with health, authentication, setup, and operational views.
- Curses-style TUI with status, doctor, groups, backup, logs, setup, chat, and sessions panels.
- SQLite storage and the current Memory V3 service, with migration tooling from older V1 data.
- Legacy runtime provider routing for Gemini and OpenRouter, including key pools, cooldowns, quotas, retries, and fallback.
- A separate Provider Registry component with symbolic secret references, fallback chains, rate limiting, health, and usage accounting.
- Google Grounding-backed live search through configured official external APIs.
- Legacy/internal SearXNG code remains in the tree but is disabled and is not a public Zero feature.
- Optional OfficeCLI document workflows for DOCX, XLSX, and PPTX, guarded by explicit commands, quotas, validation, isolated workspaces, rendering, and delivery state.

Feature status is intentionally conservative. Presence of a module or test is not treated as proof of a live external integration.

## Architecture

```text
Telegram user session ──▶ run_listener.py ──▶ ZeroBrain ──▶ SQLite / Memory V3
                                      │          ├── IndependentRouter
                                      │          ├── Google Grounding / web pipeline
                                      │          └── optional OfficeCLI workers

Telegram management bot ──▶ run_panel.py ──▶ PanelAPI ──▶ local aiohttp panel

python -m zero tui ──▶ read-only operational panels
```

The default composition roots instantiate `IndependentRouter(config)` directly. The newer `zero.providers.ProviderRegistry` is optional in `IndependentRouter`; it is tested and usable, but it is not the registry-backed default composition for the listener and panel in this commit.

## Telegram modes

### User Session Mode — implemented in the listener

`run_listener.py` creates a Telethon `TelegramClient` from the legacy runtime configuration. It requires a Telegram API ID, API hash, and authorized session file. Messages are processed only for configured allowed groups. A private message can be observed for permission and Office command handling, but the listener is not a general public bot account.

**Status:** `IMPLEMENTED`; local contract tests pass; `LIVE E2E VERIFIED: no`.

### Management Bot Mode — implemented as the management bot

`run_panel.py` creates an aiogram `Bot` from a protected `BOT_TOKEN=...` file. Management commands are owner-only and private-chat restricted. The panel process also serves the local HTTP panel.

**Status:** `IMPLEMENTED`; local panel and authentication tests pass; `LIVE E2E VERIFIED: no`.

### Hybrid Mode — configuration contract, not a unified runtime switch

The canonical setup schema accepts `disabled`, `bot`, `user_session`, and `hybrid` and validates the required symbolic secret references. Same-commit tests cover that contract and Office Telegram bridge behavior. The public listener and panel composition roots still run as two separate legacy processes; this commit does not provide a single canonical mode switch that replaces both processes.

**Status:** `IMPLEMENTED` as configuration and adapter contracts; `VERIFIED LOCALLY` by tests; not a claim of unified live deployment.

## Provider routing

The default runtime path is `zero.router.IndependentRouter`:

- Gemini is used for Google Grounding search calls.
- Gemini and OpenRouter are configured in the legacy YAML runtime config.
- Provider key pools expose redacted key IDs, quota state, cooldowns, retries, and fallback metadata.
- Secrets are loaded from protected files and are not intended to be committed.

`zero.providers.ProviderRegistry` is a separate, implemented component. It accepts `ProviderProfile` objects and symbolic `secret_ref` values, and its routing, fallback, redaction, health, cost, and rate-limit behavior is locally tested. The default `run_listener.py` and `run_panel.py` constructors do not pass a registry, so do not describe registry-backed routing as the default live runtime for this commit.

## Web search

Web search is disabled by default in `config/zero.example.yaml` (`web.enabled: false`). The public runtime documentation only describes configured official external APIs: Google Grounding through the Gemini route is the supported live-search path in this release line.

SearXNG modules remain in the source tree as legacy/internal code. They are disabled by default, are not a hosted Zero service, and are not advertised as a public fallback or supported public feature.

The repository also contains Telegram Search code, but the legacy configuration defaults it to disabled and archived, and the panel documentation excludes it from the public release/UI. It is not advertised here as a public feature.

## Memory system

The current runtime has a Memory V3 service and the normal prompt path is tested to use V3 data rather than legacy V1 markers. The repository still contains older V1/V2 storage and migration artifacts.

The direct migration tool is:

```bash
python scripts/migrate_memory_v1_to_v3.py \
  --source /path/to/v1.db \
  --target /path/to/v3.db \
  --run-id migration-001 \
  --dry-run
```

Apply mode requires a backup path and matching SHA-256 proof; the tool also supports `--verify` and `--rollback`. Migration behavior is locally tested with real temporary SQLite databases. No live production migration is claimed.

## Office rendering

Office support is optional and disabled by default. The listener starts Office services only when `office.enabled` is true. Supported formats in the source are DOCX, XLSX, and PPTX. The workflow uses explicit `/docx`, `/xlsx`, or `/pptx` commands, OOXML preflight, quotas, a persistent queue, an isolated worker, output validation, preview rendering, optional visual review, bounded repair, and idempotent delivery.

The external executable path configured by the example is `/usr/local/lib/zero-office/officecli`. Rendering also requires a supported Chrome/Chromium backend. The repository's real OfficeCLI integration tests passed in the audit environment, but no live Telegram delivery was performed.

## Administration panel and TUI

### Panel

Run the panel with `python scripts/run_panel.py`. It serves `http://127.0.0.1:8787` by default, controlled by `ZERO_PANEL_HOST` and `ZERO_PANEL_PORT`. Keep it on loopback and put it behind an authenticated HTTPS reverse proxy if remote access is required. The management bot in the same process is owner-only.

The panel is a real local adapter, not a promise that every maintenance action is available. The current documentation explicitly keeps Telegram mode connections, provider CRUD, group CRUD, backup actions, and maintenance actions behind backend contracts rather than faking them in the UI.

### TUI

```bash
python -m zero setup                         # interactive canonical setup
python -m zero setup --config /path/zero.json
python -m zero tui
python -m zero tui --print
python -m zero tui --print --panel setup
python -m zero tui --print --panel chat
python -m zero tui --print --panel sessions
```

On Windows CPython, where the optional `_curses` backend is normally absent,
`zero tui` starts a portable line-oriented console instead of printing one panel
and immediately exiting. It remains open until `q`/`quit`; `zero setup` uses a
matching portable wizard, so neither command requires installing `windows-curses`.

Unless `ZERO_CANONICAL_CONFIG` is set, setup writes the shared canonical file at
`~/.zero/config/zero.json` (normally under `%USERPROFILE%/.zero/config/zero.json`
on Windows), not a path relative to the current checkout. The legacy runtime
still requires `~/.zero/config/zero.yaml` or `ZERO_CONFIG_PATH`; `zero doctor`
reports either missing or invalid layer before a listener or panel is started.

The interactive TUI also has a conversational `Chat` panel. It uses the real
ZeroBrain policy/router/memory path and keeps sessions in the current process:

```bash
python -m zero tui --panel chat --runtime-config /path/to/zero.yaml
```

Chat commands are `/help`, `/new`, `/clear`, `/sessions`, `/use <id>`, and
`/quit`. Zero's current router returns completed responses rather than a token
stream, so the TUI reports truthful thinking/completion progress and never
simulates token streaming. Provider credentials remain in the protected runtime
secret configuration and are never entered into the chat UI.

Curses controls: `1`–`8` select panels, `Tab` or `←/→` navigates,
`↑/↓`/`j`/`k` scroll, `PageUp`/`PageDown` jump, `Home`/`End` move to the
bounds, `r` refreshes (and explicitly creates a backup only on the Backup
panel), `Enter` starts the Setup wizard from the Setup panel, and `q`/`Esc`
exits. In the portable Windows console, use `1`–`8` or a panel name to
navigate, `setup` to start the wizard, `chat <prompt>` to send a message, `r`
to refresh (or create a backup on the Backup panel), and `q`/`quit` to exit.
`zero tui --print --panel backup` is read-only; opening or printing the Backup
panel never creates a snapshot.
The setup wizard persists only canonical settings and symbolic secret
references; it never asks for or stores raw provider, Telegram, or session
credentials.


## Installation

See [`INSTALLATION.md`](INSTALLATION.md) for system requirements, OS boundaries, credentials, Telegram modes, provider configuration, database/migration commands, Docker caveats, health checks, backups, rollback, troubleshooting, and removal.

One-line install (idempotent, safe to re-run; bootstraps venv, dependencies,
config from the example, database schema, then runs a health check):

PowerShell (Windows):

```powershell
git clone https://github.com/mhrsdev/Zero-Agent.git
cd Zero-Agent
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

bash (Linux/macOS/WSL):

```bash
git clone https://github.com/mhrsdev/Zero-Agent.git
cd Zero-Agent
bash install.sh
```

Manual entry point for developers:

```bash
git clone https://github.com/mhrsdev/Zero-Agent.git
cd Zero-Agent
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
```

`requirements.txt` is the runtime set; `requirements-dev.txt` adds the test
framework. Runtime-only installs (Docker, both one-line installers) use the
hash-pinned `requirements.lock` and do not ship pytest.

## Docker status — not turnkey in this release line

The repository contains a pinned two-stage Dockerfile, an unprivileged `zero` user, a read-only filesystem, a `/data` volume, a health check at `/api/health`, and Compose services `zero-panel` and `zero-listener`.

**Important:** the current Compose setup requires both configuration layers: canonical JSON at `/data/config/zero.json` and legacy YAML selected through `ZERO_CONFIG_PATH`. The Compose file does not provision the legacy YAML path itself. Docker build/run was not locally verified in the audit environment, so Compose must not be treated as a turnkey installation until both layers are provisioned and the path boundary is resolved. See [`INSTALLATION.md`](INSTALLATION.md).
## Automation controls (kill switch / observe mode)

Every autonomous action — emoji reactions, autonomous interjection, and
proactive follow-up messages — passes through a shared gate in
[`zero/automation.py`](zero/automation.py):

| Control | Effect |
| --- | --- |
| `ZERO_AUTOMATION_DISABLED=true` (env) | Emergency stop: no reactions, no interjections, no proactive sends. Checked before any DB access. |
| `automation_enabled=false` setting | Same stop, persisted; editable from the panel (`POST /api/settings/automation_enabled`). |
| `ZERO_PROACTIVE_OBSERVE_ONLY=true` (env) | Observe mode: the proactive pipeline computes and logs each decision (`action=observe`, `would_send=true`) but postpones the candidate instead of sending. |

The kill switch fails CLOSED: any invalid or unreadable stop-signal source
(invalid setting value, storage read error) blocks autonomous actions until the
signal is readable again. Only an explicit `true` enables automation; an env
value of `false` is neutral and can never re-enable a stopped setting. The full
precedence matrix is pinned in
[`tests/test_automation_precedence.py`](tests/test_automation_precedence.py).
Decisions are auditable in the logs (`AUTOMATION_KILLED`, `PROACTIVE_OBSERVE`,
`REACTION_DECISION`, `REACTION_SKIPPED`). See
[`tests/test_automation_switch.py`](tests/test_automation_switch.py) for the
guaranteed behaviour.

## Health checks

```bash
python -m zero version
python -m zero status
python -m zero doctor
curl -fsS http://127.0.0.1:8787/api/health
```

`zero doctor` performs local checks and does not contact Telegram or an AI provider. It reports Python, runtime-home, canonical JSON, the legacy runtime YAML, SQLite FTS5, and dependency observations. A clean-container Docker health check targets the panel endpoint.

## Testing

The repository configures pytest with `tests/` as its test path. The CI workflow runs compilation, pytest, migration and tenancy checks, CLI smoke checks, lint/security/artifact checks, and a Docker build smoke test. Use:

```bash
python -m pytest -q -p no:cacheprovider
```

Install `requirements-dev.txt` first; `requirements.txt` carries the runtime
dependencies only.

Tests with names such as `live_e2e`, `real_integration`, or external-account behavior should not be read as proof that this checkout has successfully authenticated to Telegram, called a paid provider, or delivered a live message.

## Security model

- Keep bot tokens, Telegram API hashes, session files, provider keys, databases, logs, and runtime state outside version control.
- The bot token file must be private and contain a `BOT_TOKEN=...` line; the loader rejects group/world-readable files.
- Secret overlays are selected with `ZERO_SECRET_FILE` and are permission-checked.
- Canonical config stores symbolic secret references, not credential values.
- The listener uses configured group allowlists; the management bot requires the configured owner in a private chat.
- Keep the panel on loopback and use an HTTPS reverse proxy for remote access.
- Office workers are intended to run unprivileged and isolated; keep OfficeCLI and the browser backend patched.

## Known limitations

- This alpha has separate legacy YAML runtime and canonical JSON setup layers.
- Provider Registry is optional and not the default listener/panel composition.
- Bot, user-session, and hybrid mode contracts exist, but hybrid is not a single unified production switch.
- SearXNG code is legacy/internal and disabled; it is not a public feature or supported public fallback.
- Telegram Search remains archived/disabled in the public runtime path.
- Docker build/run was not locally verified in this audit because no Docker-compatible engine was installed.
- Office live rendering depends on an external pinned OfficeCLI binary and a compatible browser backend.
- No live Telegram or paid-provider E2E verification is claimed.

## Roadmap

The source and tests point to the following evidence-backed work, without implying completion:

- make canonical setup and legacy runtime configuration one coherent path;
- wire Provider Registry profiles into the default composition roots;
- finish and document the supported Telegram transport lifecycle;
- keep public search exposure explicit and separately gated;
- complete operational migration/recovery verification with real deployment artifacts;
- publish the canonical CLA document referenced by [`CLA.md`](CLA.md).

## Contributing

Contributions are welcome under the process in [`CONTRIBUTING.md`](CONTRIBUTING.md). All contributions require a signed Contributor License Agreement (CLA) before merge; see [`CLA.md`](CLA.md) for the acceptance process. Community behavior is governed by the [Contributor Covenant](CODE_OF_CONDUCT.md).

## License

Zero is licensed under the [Apache License 2.0](LICENSE); [`NOTICE`](NOTICE) carries the corresponding attribution. You may use, copy, modify, and distribute the project under that license's terms. Contributing changes back to this repository additionally requires the CLA described above.
