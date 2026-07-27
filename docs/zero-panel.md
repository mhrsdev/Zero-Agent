# Zero Administration Panel

The administration panel is an English-only, local-first control surface served by the existing `aiohttp` panel process. It is intentionally separate from Zero's AI core.

## Local development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export ZERO_CONFIG_PATH=/path/to/zero.yaml
export ZERO_PANEL_HOST=127.0.0.1
export ZERO_PANEL_PORT=8787
python scripts/run_panel.py
```

Open `http://127.0.0.1:8787/panel`. The first-run page creates a local administrator account. The password is hashed with `scrypt`; session tokens are stored hashed and cookies are `HttpOnly`, `Secure`, and `SameSite=Strict`.

## Production boundary

Keep the service on loopback and place it behind an HTTPS reverse proxy. Do not expose the panel directly to the public internet. Do not restart or deploy production services from a development checkout.

The panel stores its own metadata in a sibling `panel.db` next to the configured Zero database. It does not rewrite Python source files or arbitrary YAML. Existing Zero runtime databases, Telegram sessions, secrets, and logs are not deleted or downloaded by the panel.

## Public feature boundaries

- Bot Mode, User Session Mode, and Hybrid Mode are separate configuration concepts.
- BotFather provides Bot Tokens.
- User Session Mode uses Telegram API ID, API Hash, phone number, Telegram verification code, and optional Telegram 2FA password.
- Telegram Search is not supported in the public release and is absent from the new UI.
- Web Search is external API only. Local SearXNG, scraping, browser fallback, DuckDuckGo scraping, and API-free search are not public features.

## Current implementation status

The first vertical slice is implemented: local admin bootstrap/login/logout, persistent setup state, secret masking at the panel-store response boundary, a new English responsive shell, route protection, and a truthful dashboard adapter. Telegram mode connections, provider CRUD, group CRUD, backups, and maintenance actions remain gated behind backend contracts and tests; the UI does not fake them as active.

## Verification

```bash
python3 -m pytest -q
python3 -m compileall -q zero scripts/run_panel.py
```

Do not use real Telegram accounts or paid provider calls in automated tests. Use mocks and fixtures.
