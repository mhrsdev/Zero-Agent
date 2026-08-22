# Zero Architecture Documentation

This directory is the English, source-grounded documentation set for Zero. It was produced from direct inspection of source code, imports, call sites, configuration, SQLite schema, systemd units, runtime entrypoints, and tests. File names alone were not used as evidence.

## Start here

1. [System overview](system-overview.md)
2. [Runtime flows](runtime-flows.md)
3. [Module catalog](module-catalog.md)
4. [Data and storage](data-and-storage.md)
5. [Configuration](configuration.md)
6. [Operations and deployment](operations.md)
7. [Testing and verification](testing.md)
8. [Change guide](change-guide.md)
9. [Known uncertainties](known-uncertainties.md)
10. [Import inventory methodology](import-graph.md)

## Audit scope

- Project root: `/opt/zero`
- Python AST inventory: 194 files, 88 modules under `zero/`, 24 scripts, and 82 Python files under `tests/`/support paths; 26,527 parsed file lines.
- No AST parse errors were found in the inspected files.
- `.git` is absent from the project root; Git history, branches, ownership, and diffs cannot be verified.
- Runtime databases, Telegram sessions, logs, backups, generated Office workspaces, and real secrets are operational data and are not reproduced here.

## Evidence convention

References use `path:line`. “Current,” “uncertain,” “suspected,” and “unconfirmed” are intentional distinctions between direct observation and architectural assumption.