from __future__ import annotations

import logging
from pathlib import Path

from .fsprivacy import restrict_private_path


def setup_logger(name: str, log_path: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    restrict_private_path(path.parent, directory=True)
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(path, encoding="utf-8")
    restrict_private_path(path)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger