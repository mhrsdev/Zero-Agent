# Public Release Artifact Policy

This policy is enforced by `scripts/verify_public_artifact.py` before a release
archive or Docker build context is produced.

## Forbidden paths

A public artifact must not contain:

- `.env` files except a values-free `.env.example`;
- `runtime/`, `backups/`, `archive/`, `.venv/`, `.git/`;
- SQLite databases, WAL/SHM files, session files, logs or caches;
- private deployment config such as `config/zero.yaml`;
- Telegram Search implementation or private local-search integrations;
- generated user documents and production screenshots;
- private hosts, absolute deployment paths or real identifiers;
- secret, key, password, token or credential files;
- `PROPRIETARY_LICENSE` once the clean Apache-2.0 release tree is approved.

## Allowed examples

Examples must use placeholders such as:

- `CHANGE_ME`
- `${ZERO_PROVIDER_KEY}`
- `<telegram-chat-id>`
- `example.invalid`

They must never use production identifiers, real credentials, real sessions or
real user content.

## Build rule

Build from an explicit clean release workspace, not from the live deployment
checkout. The workspace must be generated from reviewed source and must not
include runtime state copied by Docker build context.

## Gate status

The current branch is not a release candidate. The proprietary license,
private/local-search references, runtime assumptions and incomplete public
adapters are tracked as blockers. This policy does not perform history rewrites,
credential rotation, deletion or publication.
