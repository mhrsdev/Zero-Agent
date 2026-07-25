# Memory Migration Status

- Canonical target: Memory V3
- Active stable boundary: `zero.core.memory_service.MemoryService`
- Runtime composition roots now validate a present canonical config before legacy runtime load.
- Current runtime: legacy V1/V3 compatibility composition remains
- V1→V3 migration: not yet complete
- V1 read-only cutover: not yet applied
- V2 removal: not yet complete
- Production migration: not started

## Required next work

1. classify V1-dependent behavior by legitimate contract versus obsolete implementation
2. preserve legitimate behavior in Memory V3
3. add direct V1→V3 dry-run/apply/verify/rollback tooling
4. add quarantine and interruption-resume state
5. disable V1 normal reads/writes only after regression coverage is green
6. remove V2 from active/public surfaces while retaining historical artifacts safely
