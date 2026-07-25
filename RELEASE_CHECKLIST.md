# Zero v0.1.0-alpha Release Checklist

This checklist is intentionally open until evidence exists. It is not a claim of release readiness.

- [ ] Canonical config and SetupService wired to every composition root
- [ ] Memory V3-only runtime and direct V1 → V3 migration verified
- [ ] V2 absent from active/public surfaces
- [ ] Multi-group adversarial isolation verified
- [ ] Normalized providers and API-only Web Search verified
- [ ] Bot, User Session and Hybrid adapters verified
- [ ] Secure canonical Admin API and authentication verified
- [ ] New English panel verified at desktop/mobile widths
- [ ] Zero TUI verified with shared backend services
- [ ] Docker Compose clean install, restart, backup/restore, upgrade/rollback verified
- [ ] CI, secret scan, dependency/license audit and SBOM passed
- [ ] Apache-2.0 materials and third-party notices complete
- [ ] Allowlist release tree and artifact scanner passed
- [ ] Isolated Community E2E passed
- [ ] No known Critical or High blocker
- [ ] Production and `main` untouched

Irreversible actions (production migration, credential rotation, history rewrite,
publication and package/image release) remain owner-approval gates and are not
performed by the transformation worktree.
