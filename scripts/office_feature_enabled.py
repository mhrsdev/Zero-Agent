from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from zero.config import ZeroConfig
from zero.runtime_config import runtime_config_path


if __name__ == "__main__":
    try:
        enabled = ZeroConfig.load(runtime_config_path()).office.enabled
    except Exception:
        raise SystemExit(2)
    raise SystemExit(0 if enabled else 1)
