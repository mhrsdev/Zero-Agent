#!/usr/bin/env python3
"""Conservative direct V1 -> V3 migration; never uses V2 as a target."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zero.memory_v3.migration import apply_v1_to_v3, rollback_v1_to_v3, verify_v1_to_v3


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--run-id", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    parser.add_argument("--backup")
    parser.add_argument("--backup-sha256")
    args = parser.parse_args()
    if args.verify:
        result = verify_v1_to_v3(args.target, args.run_id)
    elif args.rollback:
        result = rollback_v1_to_v3(args.target, args.run_id)
    else:
        result = apply_v1_to_v3(
            Path(args.source), Path(args.target), run_id=args.run_id,
            dry_run=args.dry_run, backup_path=args.backup,
            backup_sha256=args.backup_sha256,
        )
        result = result.__dict__
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
