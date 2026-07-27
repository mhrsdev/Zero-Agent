from __future__ import annotations

from datetime import datetime
import re
import unicodedata
from zoneinfo import ZoneInfo


def normalize_text(value: str) -> str:
    safe = "".join(ch for ch in (value or "") if ch in "\n\t\r" or unicodedata.category(ch) not in {"Cc", "Cs"})
    return re.sub(r"\s+", " ", safe, flags=re.UNICODE).strip()


def quota_date(instant: datetime, timezone_name: str) -> str:
    if instant.tzinfo is None:
        raise ValueError("quota instant must be timezone-aware")
    return instant.astimezone(ZoneInfo(timezone_name)).date().isoformat()