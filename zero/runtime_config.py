from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .configuration import ConfigStore, canonical_config_path
from .paths import zero_home_path


def runtime_config_path(default: str | Path | None = None) -> str:
    """Return the portable legacy-runtime path used by composition roots."""
    fallback = Path(default) if default is not None else zero_home_path("config", "zero.yaml")
    return os.environ.get("ZERO_CONFIG_PATH", str(fallback))


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
