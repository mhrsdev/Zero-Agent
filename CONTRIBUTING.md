# Contributing to Zero

Thank you for considering a contribution.

## License and CLA

- Zero is licensed under the [Apache License 2.0](LICENSE).
- **All contributions require a signed Contributor License Agreement (CLA).**
  See [CLA.md](CLA.md) for the acceptance process. Pull requests cannot be
  merged until the CLA requirement is satisfied for their author.
- By opening a pull request you confirm that your contribution is your own
  work and that you are eligible to license it under Apache 2.0 via the CLA.

## Development setup

```bash
# Windows PowerShell
powershell -ExecutionPolicy Bypass -File .\install.ps1

# Linux / macOS / WSL bash
bash install.sh
```

Or manually:

```bash
python3 -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt -r requirements-dev.txt
```

## Running tests

```bash
python -m pytest -q -p no:cacheprovider
ruff check zero scripts tests
python scripts/scan_secrets.py .
```

The full suite must pass locally before you open a pull request. Tests that
require live Telegram credentials or paid providers are environment-gated and
are skipped without credentials; never add a test that needs real secrets to
pass in CI.

## Ground rules

- Keep changes narrow and focused; one logical change per pull request.
- Add focused tests for every behavior change.
- Never commit secrets, session files, databases, logs, or runtime data.
- Never include local machine paths (home directories, usernames) in product
  files; use portable configuration paths (`ZERO_HOME`, `ZERO_CONFIG_PATH`).
- Do not claim live E2E verification you have not performed; use the status
  vocabulary from the README (`IMPLEMENTED`, `VERIFIED LOCALLY`,
  `LIVE E2E VERIFIED`).
- Security issues: do not open public issues; follow [SECURITY.md](SECURITY.md).

## Reporting bugs

Open a GitHub issue with:

1. What you did and what you expected.
2. What actually happened (include relevant log lines with secrets redacted).
3. Your OS, Python version, and how you installed Zero.