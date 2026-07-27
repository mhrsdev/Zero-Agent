# Zero Office Agent deployment and operations

## Scope and trust boundary

Office mode is deterministic and disabled by default. Only `/docx`, `/xlsx`, or `/pptx` at the start of a direct user message can create a job. Document text, filenames, forwarded text, quotes, model output, and metadata can never activate Office mode. Macro-enabled and legacy Office formats are rejected.

Runtime path:

```text
Telegram command gate -> OOXML preflight -> character/structure limits
-> atomic quota reservation -> persistent planner queue -> isolated worker
-> OfficeCLI -> structural/content validation -> render/review
-> bounded repair -> idempotent delivery outbox -> quota commit/refund
```

## Official OfficeCLI basis

- Website: <https://officecli.ai>
- Repository: <https://github.com/iOfficeAI/OfficeCLI>
- License: Apache-2.0
- Production pin: `v1.0.138`
- Linux x64 SHA-256: `c784d89fdadfa3c6adc70b6f74bff7a6a04f7cc2b105a764369e266cca885b2b`

Install the pinned official release and verify before installing:

```bash
set -euo pipefail
install -d -m 0755 /usr/local/lib/zero-office
curl -fL https://github.com/iOfficeAI/OfficeCLI/releases/download/v1.0.138/officecli-linux-x64 -o /tmp/officecli
curl -fL https://github.com/iOfficeAI/OfficeCLI/releases/download/v1.0.138/SHA256SUMS -o /tmp/SHA256SUMS
(cd /tmp && sha256sum -c SHA256SUMS --ignore-missing)
test "$(sha256sum /tmp/officecli | cut -d' ' -f1)" = "c784d89fdadfa3c6adc70b6f74bff7a6a04f7cc2b105a764369e266cca885b2b"
install -m 0755 /tmp/officecli /usr/local/lib/zero-office/officecli
OFFICECLI_SKIP_UPDATE=1 OFFICECLI_NO_AUTO_RESIDENT=1 /usr/local/lib/zero-office/officecli --version
```

OfficeCLI screenshot rendering requires a supported Chrome/Chromium backend. On this Ubuntu host, the snap Chromium backend could not render inside the service cgroup; Google Chrome Stable (non-snap) was verified. Keep the browser patched. The worker has no network, so update checks and resident mode are disabled.

## Configuration

Copy `.env.example` to `runtime/office.env`, keep mode `0600`, and set `ZERO_OFFICE_ENABLED=false` during installation/migration. All limits are validated at startup; invalid timezone, non-positive limits, and contradictory values fail closed.

The systemd cgroup is the authoritative physical resource boundary: `MemoryMax`, `MemorySwapMax`, `CPUQuota`, `TasksMax`, `PrivateNetwork`, and `RuntimeMaxSec`. `RLIMIT_AS` and `RLIMIT_NPROC` are intentionally not used because Chrome reserves sparse address space and many renderer threads; hard cgroup limits avoid breaking valid rendering.

## Migration and install

```bash
cd /root/zero
.venv/bin/python scripts/init_db.py
install -o root -g root -m 0644 deploy/systemd/zero-office-worker.service /etc/systemd/system/zero-office-worker.service
install -d -o zero -g zero -m 0700 runtime/office_jobs runtime/office_ingest runtime/state
systemctl daemon-reload
ZERO_OFFICE_ENABLED=false .venv/bin/python scripts/office_health.py
```

Migration is additive and idempotent. Tables: `office_jobs`, `office_quota_usage`, `office_job_events`, `office_delivery_outbox`, and `office_metrics`. Back up the SQLite DB before migration. Recovery is restore-from-backup; do not drop these tables while jobs or quota reservations exist.

## Enable and start

First run the full test suite and a non-production enabled health check. Then:

```bash
install -o root -g zero -m 0600 .env.example runtime/office.env
# edit runtime/office.env and set ZERO_OFFICE_ENABLED=true
systemctl restart zero-listener.service
systemctl enable --now zero-office-worker.service
systemctl status --no-pager zero-listener.service zero-office-worker.service
/root/zero/.venv/bin/python /root/zero/scripts/office_health.py
```

Never run the Office worker as root. The systemd unit uses `User=zero`, `PrivateNetwork=true`, read-only system/home protections, a private `/tmp`, and write access only to state/Office workspace paths.

## Usage

```text
/docx یک گزارش فارسی بساز.
/xlsx برنامه هفتگی مطالعه بساز.
/pptx یک ارائه ۱۰ اسلایدی بساز.
```

To process an existing file, put the matching command in its caption or reply directly to that exact attachment. A normal message never selects a previous file. Read-only requests return text; create/edit requests return a separate validated file.

## Health and observability

```bash
/root/zero/.venv/bin/python /root/zero/scripts/office_health.py
journalctl -u zero-office-worker.service -u zero-listener.service --since '30 min ago'
```

Health checks report feature state, OfficeCLI version, workspace writability, migration presence, and last worker heartbeat. Metrics are exposed through `OfficeRepository.metrics_snapshot()` under the required `office_*` names. Logs contain identifiers and safe error codes, never full document content, tokens, arbitrary stderr, or user-controlled paths.

## Cleanup and retention

Only terminal, delivered jobs older than the configured retention may be removed. Never delete `queued`, `planning`, `processing`, `rendering`, `reviewing`, or `repairing` workspaces. An expired in-flight Telegram delivery is marked `ambiguous` and not blindly replayed.

## Rollback

```bash
# fail closed first
sed -i 's/^ZERO_OFFICE_ENABLED=.*/ZERO_OFFICE_ENABLED=false/' /root/zero/runtime/office.env
systemctl stop zero-office-worker.service
systemctl restart zero-listener.service
systemctl disable zero-office-worker.service
```

Do not reverse the additive schema during emergency rollback. Retain DB rows and workspaces until quota/outbox reconciliation is complete. Restore source and DB from the verified encrypted pre-change backup only if a full code/data rollback is required.

## Troubleshooting

- `officecli_unavailable`: verify the pinned executable and systemd `ConditionPathExists`.
- `no_screenshot_backend`: install a supported non-snap Chrome/Chromium and run as user `zero`.
- `officecli_timeout`: inspect resource pressure; user quota is retried/refunded according to the terminal outcome.
- `ambiguous` delivery: do not resend automatically; an operator must reconcile Telegram history first.
- RTL: OfficeCLI documents `direction=rtl` and `--locale fa-IR`; Persian font fallback and pixel layout must still be verified from rendered fixtures. Pixel identity with Microsoft Office is not guaranteed by upstream.
