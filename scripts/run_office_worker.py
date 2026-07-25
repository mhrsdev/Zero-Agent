from __future__ import annotations

import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from zero.config import ZeroConfig
from zero.runtime_config import load_effective_config, runtime_config_path
from zero.office.db import OfficeRepository
from zero.office.worker import OfficeWorker
from zero.office.cleanup import cleanup_expired_workspaces


CONFIG_PATH = Path(runtime_config_path())


def main() -> int:
    config = load_effective_config(CONFIG_PATH, ZeroConfig)
    if not config.office.enabled:
        return 0
    repository = OfficeRepository(config.memory.db_path)
    worker = OfficeWorker(repository, config.office, worker_id=f"worker-{time.time_ns()}")
    last_cleanup = 0.0
    running = True

    def stop(_signum, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while running:
        repository.set_metric("office_worker_heartbeat_epoch", int(time.time()))
        repository.recover_expired_leases(max_attempts=config.office.max_attempts)
        if time.monotonic() - last_cleanup >= 3600:
            cleanup_expired_workspaces(repository, config.office)
            last_cleanup = time.monotonic()
        outcome = worker.tick()
        if outcome is None:
            time.sleep(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
