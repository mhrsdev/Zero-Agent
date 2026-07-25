from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .configuration import ConfigStore, canonical_config_path


def runtime_config_path(default: str | Path = "/root/zero/config/zero.yaml") -> str:
    """Return the one legacy-runtime path used by all composition roots."""
    return os.environ.get("ZERO_CONFIG_PATH", str(default))


def load_effective_config(legacy_path: str | Path | None, config_type: Any):
    """Validate canonical setup before loading the legacy runtime adapter.

    The adapter is intentionally fail-closed: a present but invalid canonical
    document prevents startup instead of silently ignoring panel setup.
    """
    canonical_path = canonical_config_path()
    if canonical_path.exists():
        ConfigStore(canonical_path).load()
    path = Path(legacy_path) if legacy_path is not None else Path(runtime_config_path())
    return config_type.load(path)
