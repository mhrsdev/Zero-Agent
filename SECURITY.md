# Security Policy

## Supported versions

Zero is at `v0.1.0-alpha`. Until `v1.0.0`, only the latest tagged release
receives security fixes.

## Reporting a vulnerability

Report privately. Do not open a public issue for an unfixed vulnerability.

Use GitHub's private vulnerability reporting on the repository
(Security -> Report a vulnerability). Include the affected version or commit,
the impact, and the smallest reproduction you have.

Expect an acknowledgement within 7 days and an assessment within 30 days.
Please allow 90 days before public disclosure, or less if a fix ships sooner.

## Scope

In scope:

- authentication and session handling in the Admin API and panel
- cross-group data leakage, including memory, files, quotas and delivery
- secret exposure in logs, API responses, panel output or release artifacts
- SSRF, path traversal and injection in the web-search and Office paths
- migration paths that lose or corrupt data

Out of scope:

- vulnerabilities in third-party services Zero calls
- attacks requiring an operator to run a hostile configuration deliberately
- missing hardening on a deployment the operator exposed to the internet
  against the documented localhost-first guidance

## Operator responsibilities

Zero is self-hosted. The operator owns:

- keeping `runtime/secrets/` and `runtime/state/` outside version control and
  outside any image build context
- restricting the panel to localhost or placing an authenticating reverse proxy
  in front of it
- rotating provider and Telegram credentials if a host is compromised
- taking and testing backups before running any migration

## Handling secrets

Zero stores credentials as symbolic references in configuration and resolves
them from a protected secret file. No API surface returns a resolved secret;
`ProviderProfile.redacted()` is the only representation the panel and API see.
If you find a path that returns one, treat it as in scope and report it.
