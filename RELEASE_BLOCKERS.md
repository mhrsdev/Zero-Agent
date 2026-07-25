# Release Blockers

## Open

- Canonical configuration composition-root integration (runtime validation boundary completed; shared setup write-through remains open)
- Shared SetupService replacing panel setup credential persistence
- Memory V3-only runtime cutover
- Direct V1→V3 migration with quarantine, resume, verification and rollback
- Memory V2 active/public surface removal
- Multi-Group isolation
- Normalized model providers
- External API-only Web Search
- Bot, User Session and Hybrid Telegram adapters
- Canonical Admin API/authentication
- English web panel and Zero TUI
- Docker Compose and clean-install validation
- CI, dependency/license audit, SBOM and security gates
- Apache-2.0 release tree and third-party notices
- Community E2E and release artifacts

## Safety gates not executed

- production migration
- credential/session rotation
- permanent deletion
- Git history rewrite
- public publication
