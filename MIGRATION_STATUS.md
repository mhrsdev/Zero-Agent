# Memory Migration Status

- Canonical target: Memory V3
- Active stable boundary: `zero.core.memory_service.MemoryService`
- Shared SetupService is now wired into the panel composition root for Telegram setup.
- Runtime composition roots validate a present canonical config before legacy runtime load.
- Current runtime: normal prompt retrieval and normal writes are V3-only; V1 storage remains archive/migration material
- V1→V3 migration: not yet complete
- V1 read-only cutover: normal runtime flag disabled; migration tooling still required
- V2 removal: not yet complete
- Production migration: not started

## Required next work

1. classify remaining V1-dependent maintenance behavior by legitimate contract versus obsolete implementation
2. preserve legitimate behavior in Memory V3
3. add direct V1→V3 dry-run/apply/verify/rollback tooling
4. add quarantine and interruption-resume state
5. disable V1 normal reads/writes only after regression coverage is green
6. remove V2 from active/public surfaces while retaining historical artifacts safely
