# Installation

This guide targets the current `main` / `0.1.0-alpha` release line:

```text
https://github.com/mhrsdev/Zero-Agent.git
```

Zero is alpha software. The commands below are source-derived, but a successful local install does not prove live Telegram, provider, or production E2E behavior.

## One-line install

Fastest path — one command per platform (idempotent; bootstraps the venv,
installs locked dependencies, copies the example config to
`<ZERO_HOME>/config/zero.yaml`, initializes the database schema, then runs
`scripts/doctor.py`):

- Windows PowerShell: `powershell -ExecutionPolicy Bypass -File .\install.ps1`
- Linux/macOS/WSL bash: `bash install.sh`

Then edit the generated config (Telegram api_id/api_hash, allowed groups) and
start with `.venv\Scripts\python.exe scripts\run_listener.py` (Windows) or
`.venv/bin/python scripts/run_listener.py` (POSIX). The sections below cover
manual installation and every operational detail.

## System requirements

- Python 3.11 or newer. The repository CI runs Python 3.11 and 3.12.
- A virtual environment is strongly recommended.
- SQLite with FTS5 support for the normal diagnostics and memory search path.
- Network access to Telegram and to any configured AI/search provider.
- Linux systemd is required only for the bundled systemd deployment units; it is not required for direct foreground commands.

### Optional Office runtime

Office mode is disabled by default. Enabling it additionally requires:

- the external executable at the configured `office.cli_path` (the example uses `/usr/local/lib/zero-office/officecli`);
- a supported Chrome/Chromium rendering backend;
- writable Office workspace paths;
- the unprivileged worker boundary recommended by `docs/office-agent.md`.

## Supported operating systems

- **Linux:** primary supported deployment target. The source includes Linux-oriented paths, systemd units, and Docker files.
- **Windows:** the Python commands may be run in a normal Python environment, and `zero setup` / `zero tui` automatically use a portable line console when Python does not provide `_curses`. The repository does not provide a native Windows service definition; use WSL2 for the Linux-oriented listener, panel, paths, and optional systemd workflow. Windows/WSL2 live Telegram operation was not verified in this audit.
- **macOS:** manual Python execution may be possible, but the repository does not provide a macOS service definition. Docker Desktop is the only container route described by the repository. macOS live Telegram operation was not verified in this audit.

Do not copy the Linux `/opt/zero` paths into Windows or macOS configuration. Set `ZERO_CONFIG_PATH`, `ZERO_HOME`, and the file paths in the YAML config to real paths for the host.

## Repository clone command

```bash
git clone https://github.com/mhrsdev/Zero-Agent.git
cd Zero-Agent
git rev-parse HEAD
```

## Manual installation

From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The repository also contains `requirements.lock` with hashes. The Docker builder installs that lock file with `pip install --require-hashes`; the manual requirements file is the source-provided developer entry point.

Copy the example runtime configuration without putting real credentials into Git:

```bash
cp config/zero.example.yaml config/zero.yaml
mkdir -p runtime/secrets runtime/state runtime/logs
chmod 700 runtime runtime/secrets runtime/state runtime/logs
```

Set the runtime path explicitly for a non-`/opt/zero` checkout:

```bash
export ZERO_CONFIG_PATH="$PWD/config/zero.yaml"
export ZERO_HOME="$PWD/runtime"
```

`ZERO_HOME` controls canonical private runtime-home discovery used by the CLI/TUI. The legacy listener and panel configuration still comes from `ZERO_CONFIG_PATH`.

## Configuration file locations

This HEAD has two configuration layers:

### Legacy runtime YAML

`zero.runtime_config.runtime_config_path()` reads `ZERO_CONFIG_PATH`. Its default is `/opt/zero/config/zero.yaml`. The listener, panel, Office worker, and database initializer load the legacy `ZeroConfig` from that path.

The checked-in template is:

```text
config/zero.example.yaml
```

A real local copy is ignored by Git:

```text
config/zero.yaml
```

### Canonical setup JSON

`zero.configuration.canonical_config_path()` reads `ZERO_CANONICAL_CONFIG`. When that variable is unset, every CLI/TUI/panel composition root uses the runtime-home default:

```text
~/.zero/config/zero.json
```

On Windows this is normally under `%USERPROFILE%/.zero/config/zero.json`. Set
`ZERO_CANONICAL_CONFIG` only when deliberately using a different shared path.
The canonical store writes restrictive permissions and creates a sibling backup:

```text
~/.zero/config/zero.json.bak
```

If a canonical JSON file exists, the runtime validates it before loading the legacy YAML adapter. It does not replace the legacy YAML loader in this alpha commit; configure `~/.zero/config/zero.yaml` (or `ZERO_CONFIG_PATH`) before starting the listener or panel.

### Runtime data

The exact database and log paths are values in the legacy runtime YAML (by default `~/.zero/config/zero.yaml`, or `ZERO_CONFIG_PATH`), including `memory.db_path` and the three log paths. Do not assume that `runtime/` paths in the repository are already populated. Session files, databases, logs, backups, and secrets are local runtime data and must stay out of commits.

## Required and optional credentials

### Required for the user-session listener

- Telegram API ID: `listener.telegram_api_id`.
- Telegram API hash: `listener.telegram_api_hash`, preferably supplied through the protected secret overlay.
- An authorized Telethon session at `listener.session_path`.
- At least one allowed group ID, username, or title if the listener is expected to serve a group.

### Required for the management bot/panel

- `management_bot.token_file` pointing to a private file containing exactly one usable line in this form:

```text
BOT_TOKEN=<set-this-locally>
```

Do not place the real value in this document, the example config, or Git. The loader requires the token file to have no group/world permission bits.

### Required for AI responses

At least one configured provider key in the legacy `router.providers.gemini.keys` or `router.providers.openrouter.keys` list. Put real keys in the protected secret file and keep the YAML template free of them.

### Optional credentials and services

- OpenRouter key, if OpenRouter fallback is desired.
- Telegram Search API ID, API hash, and session path. Telegram Search is archived/disabled in the public runtime path and is not required for normal installation.
- Google Grounding uses a configured Gemini key through the router; no separate credential type is defined by this repository.
- OfficeCLI itself does not require a Zero API credential, but it requires the external binary and browser backend described above.

## Management Bot Mode setup

In the legacy runtime, Bot Mode is the management process, not a replacement for the Telethon listener:

1. Create a bot with BotFather.
2. Store its token in a private file whose path is `management_bot.token_file`.
3. Set `owner_user_id` to the numeric Telegram owner ID.
4. Set `ZERO_CONFIG_PATH` to the effective YAML file.
5. Start the panel process:

```bash
ZERO_CONFIG_PATH="$PWD/config/zero.yaml" python scripts/run_panel.py
```

The management bot accepts owner commands only in a private chat. The same process serves the local panel. A real Telegram login and message delivery were not performed by this installation guide.

The canonical setup schema also accepts:

```json
{
  "schema_version": 1,
  "installation_id": "local",
  "telegram": {
    "mode": "bot",
    "bot_token_ref": "telegram.bot_token"
  }
}
```

This stores a symbolic reference, not a token. The current panel/listener runtime still loads the legacy YAML adapter as described above.

## User Session Mode setup

1. Create Telegram API credentials at <https://my.telegram.org>.
2. Put the API ID and protected API hash into the legacy listener configuration.
3. Set `listener.session_path` to the authorized Telethon session file.
4. Set `listener.account_username` if Office command routing needs the account name.
5. Configure allowed groups.
6. Initialize the database and start the listener:

```bash
ZERO_CONFIG_PATH="$PWD/config/zero.yaml" python scripts/init_db.py
ZERO_CONFIG_PATH="$PWD/config/zero.yaml" python scripts/run_listener.py
```

The listener fails closed if the session is not authorized. It does not expose every Telegram chat; `_allowed_chat()` checks configured group IDs, usernames, and titles.

The canonical setup schema represents this transport as:

```json
{
  "telegram": {
    "mode": "user_session",
    "api_id": 123456,
    "api_hash_ref": "telegram.api_hash",
    "session_ref": "telegram.session"
  }
}
```

Use symbolic references only in canonical JSON. The legacy runtime still needs the actual protected-file resolution used by `ZeroConfig`.

## Hybrid Mode setup

The canonical schema accepts hybrid only when all required symbolic references are present:

```json
{
  "telegram": {
    "mode": "hybrid",
    "bot_token_ref": "telegram.bot_token",
    "api_id": 123456,
    "api_hash_ref": "telegram.api_hash",
    "session_ref": "telegram.session"
  }
}
```

Same-commit tests validate this schema and the Office Telegram bridge's bot/user-session/hybrid event contract. The operational composition remains two processes: `run_listener.py` for the user session and `run_panel.py` for the management bot. Start them separately only after configuring both legacy runtime paths and their credentials:

```bash
ZERO_CONFIG_PATH="$PWD/config/zero.yaml" python scripts/run_listener.py
ZERO_CONFIG_PATH="$PWD/config/zero.yaml" python scripts/run_panel.py
```

Do not describe the canonical hybrid field as a complete single-process mode switch for this HEAD.

## Provider configuration

The default runtime provider configuration is in the legacy YAML under `router.providers`:

```yaml
router:
  normal_primary: openrouter
  normal_fallback: gemini
  providers:
    gemini:
      enabled: true
      model: <set-locally>
      keys: []
    openrouter:
      enabled: true
      model: <set-locally>
      keys: []
```

Keep the real `keys` values in the protected secret file. The runtime supports Gemini and OpenRouter key pools, retry/fallback behavior, cooldowns, and redacted status. The separate Provider Registry supports symbolic secret references but is not passed by the default listener and panel composition roots in this commit.

## Web search configuration

The public installation path documents only configured official external APIs. Search is disabled by default:

```yaml
web:
  enabled: false
  google_grounding_enabled: true
```

When intentionally enabled, Google Grounding through the configured Gemini route is the supported live-search path. Provider credentials and network access are required.

SearXNG modules remain in the source tree as legacy/internal code. They are disabled and are not a public Zero feature or supported public fallback. Do not install or expose a SearXNG service as part of the public Zero installation instructions.

Telegram Search remains `enabled: false` and `archived: true` by default. It is legacy/archived code, is not required for normal operation, and is not a public installation feature.

## Database initialization and migrations

Initialize the configured SQLite store:

```bash
ZERO_CONFIG_PATH="$PWD/config/zero.yaml" python scripts/init_db.py
```

For Memory V1→V3 migration, first create a verified backup and run a dry run:

```bash
python scripts/backup_restore.py backup /path/to/v1.db /path/to/v1.backup.db
python scripts/migrate_memory_v1_to_v3.py \
  --source /path/to/v1.db \
  --target /path/to/v3.db \
  --run-id v1-to-v3-001 \
  --dry-run
```

Apply only after reviewing the dry-run output and supplying matching backup proof:

```bash
sha256sum /path/to/v1.backup.db
python scripts/migrate_memory_v1_to_v3.py \
  --source /path/to/v1.db \
  --target /path/to/v3.db \
  --run-id v1-to-v3-001 \
  --apply \
  --backup /path/to/v1.backup.db \
  --backup-sha256 <sha256-from-local-command>
```

Verify or roll back the recorded migration run:

```bash
python scripts/migrate_memory_v1_to_v3.py --source /path/to/v1.db --target /path/to/v3.db --run-id v1-to-v3-001 --verify
python scripts/migrate_memory_v1_to_v3.py --source /path/to/v1.db --target /path/to/v3.db --run-id v1-to-v3-001 --rollback
```

The database/storage classes also perform additive initialization for feature tables. There is no generic `zero upgrade` command.

## Docker installation — **NOT TURNKEY IN THIS RELEASE LINE**

The repository supplies `Dockerfile` and `docker-compose.yml`:

```bash
docker compose build
docker compose up -d
docker compose ps
```

The Compose services are named:

```text
zero-panel
zero-listener
```

The image is Python `3.11.15-slim`, runs as the unprivileged `zero` user, mounts `/data`, exposes the panel on `127.0.0.1:8787`, and reads the Compose secret from `./runtime/secrets/zero.secrets.yaml`.

**HEAD BLOCKER — Compose is not turnkey.** The Compose file sets `ZERO_CANONICAL_CONFIG=/data/config/zero.json`, but the legacy listener/panel loaders still require a legacy YAML path through `ZERO_CONFIG_PATH`; Compose does not provision that YAML path. Both layers must be provisioned and their path boundary resolved before treating Docker as usable. Docker build/run was not locally verified in this audit environment because Docker, Podman, and Buildah were unavailable.

## Starting the listener

```bash
ZERO_CONFIG_PATH="$PWD/config/zero.yaml" python scripts/run_listener.py
```

Equivalent CLI entry point:

```bash
ZERO_CONFIG_PATH="$PWD/config/zero.yaml" python -m zero listener
```

## Starting the admin panel

```bash
ZERO_CONFIG_PATH="$PWD/config/zero.yaml" \
ZERO_PANEL_HOST=127.0.0.1 \
ZERO_PANEL_PORT=8787 \
python scripts/run_panel.py
```

Equivalent CLI entry point:

```bash
ZERO_CONFIG_PATH="$PWD/config/zero.yaml" python -m zero panel
```

Open the local panel at:

```text
http://127.0.0.1:8787/panel
```

Keep it on loopback. The source's `deploy/zero-panel.service` is Linux/systemd-specific and contains deployment-local paths that must be adapted before use; it is not a portable manual-install command.

## Starting the Zero TUI

Interactive mode:

```bash
python -m zero tui
```

Non-interactive status or diagnostics:

```bash
python -m zero tui --print
python -m zero tui --print --panel doctor
python -m zero tui --print --panel groups
python -m zero tui --print --panel backup
python -m zero tui --print --panel logs --tail 50
python -m zero tui --print --panel setup
python -m zero tui --print --panel sessions
```

Interactive setup is available either as a standalone command or from the Setup
panel with `Enter`:

```bash
python -m zero setup
python -m zero setup --config /path/to/zero.json --panel-db /path/to/panel.db
```

When `_curses` is unavailable (the normal Windows CPython case), `zero setup`
uses the same line-oriented prompt flow and `zero tui` remains interactive rather
than printing a status screen once and exiting. In that portable console use
`1`–`8` or a panel name to navigate, `setup` to launch the wizard, `chat <prompt>`
to send a message, `r` to refresh or create a backup from the Backup panel, and
`q`/`quit` to exit.

The wizard writes canonical JSON atomically through `SetupService`, records
progress in `PanelStore`, validates Telegram mode requirements, and accepts
only symbolic references such as `telegram.bot_token`. Put actual values in
the protected secret store described above; raw credentials are never entered
into the TUI.

TUI controls: `1`–`8` select panels; `Tab`, `←`, and `→` navigate; `↑`, `↓`,
`j`, and `k` scroll; `PageUp`, `PageDown`, `Home`, and `End` move through long
panels; `r` refreshes; `Enter` starts setup from the Setup panel; and `q` or
`Esc` exits. Interactive redraws are read-only. Backup creation happens only
for the explicit `--print --panel backup` command or when `r` is pressed while
the Backup panel is active.


## Health checks

Local, side-effect-limited CLI checks:

```bash
python -m zero version
python -m zero status
python -m zero doctor
```

Panel health check:

```bash
curl -fsS http://127.0.0.1:8787/api/health
```

Office health check, when using the legacy YAML path:

```bash
ZERO_CONFIG_PATH="$PWD/config/zero.yaml" python scripts/office_health.py
```

The Office health script in this HEAD currently contains a hard-coded `/opt/zero/config/zero.yaml` load, so run it only after adapting that path or from the deployment layout it expects. Treat a non-zero result as a failed optional feature, not as proof that the core listener is broken.

## Verifying a successful installation

A minimally configured local installation should satisfy all of the following:

1. `python -m zero version` prints `0.1.0-alpha`.
2. `python -m zero status` prints the expected config/runtime paths.
3. `python -m zero doctor` reports Python/dependencies/SQLite checks and identifies any missing config honestly.
4. `python scripts/init_db.py` creates or opens the configured SQLite store.
5. `python -m zero tui --print --panel status` renders without an import or database exception.
6. With a private bot token file and valid YAML, the panel starts and `curl -fsS http://127.0.0.1:8787/api/health` returns HTTP 200.
7. With an authorized Telethon session and allowed group, the listener reaches its `STARTED authorized=true` path.

Items 6 and 7 require real external credentials and are not asserted by this documentation package.

## Backup and restore

```bash
python scripts/backup_restore.py backup /path/to/zero.db /path/to/zero.backup.db
python scripts/backup_restore.py verify /path/to/zero.backup.db
python scripts/backup_restore.py restore /path/to/zero.backup.db /path/to/zero.db
```

The backup command verifies SQLite integrity. Restore verifies the backup first, removes stale target WAL/SHM sidecars, copies the backup, and preserves an existing target as `/path/to/zero.db.bak`. Stop the listener, panel, and Office worker before restoring a live database.

## Upgrade and rollback

There is no generic upgrade subcommand. For this HEAD:

- storage and Office schema setup is additive/idempotent during initialization;
- Memory V1→V3 has `--dry-run`, `--apply`, `--verify`, and `--rollback`;
- canonical JSON saves create `~/.zero/config/zero.json.bak` by default, and `ConfigStore.rollback()` restores that backup;
- a deployment rollback should normally restore the verified pre-change database/config backup and restart the affected services.

For Linux units, the repository names include `zero-listener.service`, `zero-panel.service`, and optional `zero-office-worker.service`. Their embedded `/opt/zero` paths are deployment-specific and must not be copied unchanged to another host.

## Troubleshooting common errors

### `canonical_config: missing` or `legacy_runtime_config: missing`

`python -m zero doctor` checks both configuration layers without contacting Telegram or a provider. It looks for `ZERO_CANONICAL_CONFIG` or the default `~/.zero/config/zero.json`, plus `ZERO_CONFIG_PATH` or the default `~/.zero/config/zero.yaml`. Complete canonical setup at the shared path and configure the legacy YAML adapter before starting the listener or panel.

### `protected secret file is required`

Set `ZERO_SECRET_FILE` to an existing private YAML secret file and remove unresolved credential placeholders from the effective config. Ensure its mode has no group/world permission bits.

### `BOT_TOKEN not found in bot.env`

Check `management_bot.token_file`, verify the file exists, is private, and contains a non-empty line beginning with `BOT_TOKEN=`.

### `telegram listener session is not authorized`

The Telethon session at `listener.session_path` is not authorized for the configured API ID/hash. Complete Telegram authorization with the intended account before starting the listener.

### Panel cannot bind to port 8787

Set `ZERO_PANEL_PORT` to an unused local port and access the matching `/api/health` URL. Keep the host on `127.0.0.1` unless a controlled reverse proxy is in front of it.

### Web search returns disabled/unavailable

Check `web.enabled`, provider keys, and Google Grounding configuration. SearXNG is legacy/internal and disabled; it is not bundled or supported as a public service.

### Office health is failed

Confirm `office.enabled`, the executable at `office.cli_path`, writable workspace paths, the SQLite Office tables, the worker heartbeat, and a supported browser backend. Office is optional and disabled by default.

### Docker container exits during startup

Inspect `docker compose logs zero-panel zero-listener`. Check the `/data` canonical JSON path and the legacy YAML `ZERO_CONFIG_PATH` boundary described in the Docker section. Do not add real secrets to the image.

## Uninstallation

Stop foreground processes with `Ctrl-C`. For a Linux/systemd deployment, stop and disable only the units actually installed, for example:

```bash
sudo systemctl disable --now zero-office-worker.service
sudo systemctl disable --now zero-panel.service
sudo systemctl disable --now zero-listener.service
```

Remove the checkout and virtual environment only after preserving any needed configuration, session files, database, logs, and verified backups:

```bash
rm -rf /path/to/Zero-Agent
```

For Compose:

```bash
docker compose down
```

Do not add `--volumes` unless you intentionally want to delete the named `zero-data` volume and all data stored there. Remove runtime secrets separately and securely. Uninstallation does not revoke Telegram sessions or provider keys; revoke those credentials through their respective providers if the installation is no longer trusted.
