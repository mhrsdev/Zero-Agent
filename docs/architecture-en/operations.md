# Operations, Entrypoints, and Deployment

## Entry points

| Command/service | File | Effect |
|---|---|---|
| Listener | `python scripts/run_listener.py` | Telethon user session, Brain, and background loops |
| Panel | `python scripts/run_panel.py` | aiogram bot plus aiohttp panel |
| Office worker | `python scripts/run_office_worker.py` | persistent Office worker; exits successfully when disabled |
| DB init | `python scripts/init_db.py` | creates/opens ZeroStore at the configured DB path |
| Office health | `python scripts/office_health.py` | Office feature/availability checks |
| Office condition | `python scripts/office_feature_enabled.py` | fail-closed systemd execution condition |

## Installed panel and configuration path

The installed panel runs with `/etc/zero/zero.yaml` and a separate secret file, binding to `127.0.0.1:8787`. Its source entrypoint is `scripts/run_panel.py`. Listener and panel are independent services.

The panel and listener do not share the same default config path: panel uses `ZERO_CONFIG_PATH`/`/etc/zero/zero.yaml`, while listener uses `/root/zero/config/zero.yaml` (`run_panel.py:40`, `run_listener.py:45`).

## systemd units

Listener unit:

- `User=zero`, `Group=zero`
- optional `/root/zero/runtime/office.env`
- `ExecStart=/root/zero/.venv/bin/python /root/zero/scripts/run_listener.py`
- restart always, `NoNewPrivileges`, private tmp/devices, ProtectSystem and restricted `ReadWritePaths` (`deploy/systemd/zero-listener.service:5-26`).

Office worker unit:

- requires OfficeCLI and passes `ExecCondition` (`deploy/systemd/zero-office-worker.service:3-13`);
- `PrivateNetwork=true`, `RestrictAddressFamilies=AF_UNIX` (`:17-32`);
- 1G memory, 100% CPU quota, 256 tasks, 15-minute runtime (`:33-37`);
- writes only to state/Office job/ingest paths (`:38`).

Other service units exist under `deploy/`, including panel and Telegram source digest/join. Without Git metadata, source and installed units cannot be historically compared by diff.

## Auxiliary Telegram source services

`deploy/zero-tg-source-digest.service` and `deploy/zero-tg-source-join.service` are oneshot services with separate timers; digest is approximately every three hours and join approximately every ten minutes. They are not listener background loops.

## Runtime state

Explicit paths include `/root/zero/runtime/logs`, `state`, `pids`, `office_jobs`, and `office_ingest`. These contain operational data and must remain outside public documentation/source archives.

## Health and fail-closed behavior

`office_health.py` reports the feature state and `office_feature_enabled.py` gates worker startup. The listener does not create the Office repository/bridge/coordinator when Office is disabled (`run_listener.py:91-126`, `:586-598`). Test both gates together.

## Utility scripts

`login_tgsearch.py`, `join_tgsearch_channels.py`, `real_tests.py`, `live_*`, prototypes, migration scripts, and benchmarks may access networks, sessions, or DB state. They are not production services unless a unit or documented call site connects them.