# Security Findings

Values are intentionally omitted. Findings identify categories and paths only.

## Open

- `runtime/state/`: production SQLite databases, Telegram sessions and private
  state are local deployment artifacts and must stay outside public release
  workspaces.
- `runtime/secrets/`: provider and Telegram secret files are local-only and
  must never enter Git or a Docker build context.
- `backups/` and `archive/`: prior recovery material must be excluded from
  public artifacts; previous contents are inventoried as metadata only.
- `/etc/zero/zero.yaml` and effective systemd drop-ins are private deployment
  configuration.
- `runtime/logs/`: logs may contain identifiers, labels, user-controlled text,
  traces and exception details; they require retention and redaction policy.
- Git has six unreachable blobs requiring isolated review before any history
  rewrite or publication.
- Current README/docs contain private deployment and unsupported feature claims;
  they are documentation/release blockers, not evidence for credential exposure.
- Panel setup credential persistence was verified in the prior setup slice; keep
  symbolic references as the only public/config/database representation.

## Closed in Phase 0

- recovery archive was created outside the repository;
- archive was encrypted;
- key was stored separately with restricted permissions;
- restore rehearsal did not contact Telegram or providers;
- production services were not restarted or changed.

## Required irreversible approvals

- credential/session rotation;
- Git unreachable-object deletion or history rewrite;
- permanent deletion of private artifacts.
