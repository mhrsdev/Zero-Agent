# Zero Public/Private Boundary

Status: transformation branch; public release is not yet authorized.

## Public repository

The public repository may contain only generic, reusable, documented code and
synthetic fixtures:

- Zero core contracts and policies
- Telegram adapters without credentials or sessions
- model-provider interfaces and sanitized adapters
- canonical Memory V3 implementation and migration tooling
- group, identity, permission and quota code
- API-based Web Search adapters
- optional experimental modules with safe defaults
- admin API and public panel
- Zero TUI
- tests with synthetic data
- Docker Compose and development deployment files
- documentation, examples, notices and release metadata

## Private repository/overlay

The private deployment repository or host-local overlay owns deployment-specific
extensions and integrations:

- Telegram Search plugin
- local SearXNG and crawler experiments
- private prompts and personas
- production deployment overrides
- private provider adapters
- private monitoring integrations
- production-only scripts

The private layer consumes the public core. It must not copy the public Zero
codebase into a second long-lived fork.

## Never stored in Git

The following remain outside both repositories:

- Telegram Bot Tokens
- Telegram API credentials
- phone numbers and OTP/2FA data
- Telegram session files
- provider API keys
- encryption keys and backup passwords
- production databases and memory
- logs and queue state
- user/group data
- backups and archives
- generated user files
- production hostnames, paths and private IDs

## First public release exclusions

The public runtime, build, setup, panel, TUI and documentation must not expose:

- Telegram Search
- local SearXNG
- scraping
- crawler/browser search
- API-free search
- production data
- Memory V1/V2 runtime selectors
- unsupported Telegram modes

Office Agent and proactive follow-ups remain optional experimental modules and
must be disabled by default.

## Release boundary checks

Every release candidate must pass:

1. forbidden-path scan;
2. secret-pattern scan with values suppressed;
3. private-path and deployment-host scan;
4. Git ref and artifact review;
5. dependency/license review;
6. clean Docker build-context review.

This document defines policy only. It does not authorize repository publication,
credential rotation, Git history rewrite or permanent deletion.
