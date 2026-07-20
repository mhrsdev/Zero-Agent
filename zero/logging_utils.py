from __future__ import annotations

import logging
import os
from pathlib import Path


def setup_logger(name: str, log_path: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(path, encoding="utf-8")
    os.chmod(path, 0o600)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
