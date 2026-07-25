# Release Blockers

This file is a living release gate. A blocker may be closed only with direct
verification evidence and a recorded commit/phase.

## Current blockers

- [ ] public tree has no proprietary/private deployment artifacts;
- [ ] Apache-2.0 license and third-party notices are ready for the licensing
      approval gate;
- [ ] Git reachable/unreachable object review is complete;
- [ ] no secret-pattern finding remains in the release workspace;
- [ ] canonical typed configuration and real setup are implemented;
- [ ] Memory V3-only runtime is implemented and verified;
- [ ] direct V1 → V3 migration has dry-run, quarantine, verify and rollback;
- [ ] Memory V2 is absent from active runtime, UI, setup and docs;
- [ ] RequestContext reaches every group-relevant stateful operation;
- [ ] adversarial Multi-Group and thread isolation passes;
- [ ] Bot Mode, User Session Mode and Hybrid Mode have real adapters;
- [ ] public Web Search is external API-only;
- [ ] Telegram Search is outside the public tree/runtime/build;
- [ ] canonical admin auth is hardened;
- [ ] new panel is connected to real backend contracts;
- [ ] Zero TUI uses the shared setup/config services;
- [ ] Docker Compose clean-host installation passes;
- [ ] CI, dependency audit, secret scan and SBOM pass;
- [ ] isolated Community deployment passes;
- [ ] production migration package is prepared but not applied;
- [ ] public publication package is prepared but not published.

## Irreversible gates

The following require a separate final owner approval:

- Git history rewrite or deletion of unreachable objects;
- credential or Telegram session rotation/revocation;
- permanent deletion of Memory V1/V2, logs, backups, archives or quarantine;
- production migration or service replacement;
- public repository/release/Docker image publication.
