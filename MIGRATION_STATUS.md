# Migration Status

## Memory

- Current production state: Memory V3 drop-in is active; V1 remains integrated.
- Target: Memory V3-only runtime.
- Migration path: direct V1 → validation/quarantine → V3.
- Memory V2: cancelled; no new runtime or public support work.
- Production migration: not started.
- Permanent V1/V2 deletion: not authorized.

## Required migration gates

1. Fresh verified backup and isolated restore — Phase 0 passed.
2. V3-only runtime design and tests.
3. Direct V1 → V3 dry-run.
4. Source/target maps and provenance.
5. Ambiguous-record quarantine.
6. Row counts and integrity verification.
7. Interruption/resume tests.
8. Scoped rollback tests.
9. Synthetic multi-group isolation tests.
10. Explicit production migration approval.

No production schema or memory data has been changed by the transformation
branch so far.
