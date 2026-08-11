#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zero.group_simulation import run_simulation


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Zero's deterministic local group simulation.")
    parser.add_argument("--output-dir", default="/workspace/Zero-Agent/runtime/simulation")
    parser.add_argument("--messages", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1389)
    args = parser.parse_args()
    report = asyncio.run(run_simulation(args.output_dir, message_count=args.messages, seed=args.seed))
    summary = {
        "passed": report["passed"],
        "incoming_messages": report["incoming_messages"],
        "stored_messages": report["stored_messages"],
        "member_count": report["member_count"],
        "abusive_message_count": report["abusive_message_count"],
        "personal_memory_owner_count": report["personal_memory_owner_count"],
        "reply_edge_count": report["reply_edge_count"],
        "long_chain_depth": report["long_chain_depth"],
        "duration_seconds": report["duration_seconds"],
        "report": report["artifacts"]["report"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
