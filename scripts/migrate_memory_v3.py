#!/usr/bin/env python3
"""Idempotently import V2 and legacy Zero memory into the local V3 store."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zero.memory_v3 import MemoryV3Service


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v3", required=True)
    parser.add_argument("--v2", required=True)
    parser.add_argument("--legacy", required=True)
    args = parser.parse_args()
    service = MemoryV3Service(args.v3)
    if not service.healthy:
        raise SystemExit("V3 store initialization failed")
    result = {
        "v2": await service.migrate_v2(args.v2),
        "legacy": await service.migrate_legacy_zero(args.legacy),
        "total_items": service.count_items(),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
