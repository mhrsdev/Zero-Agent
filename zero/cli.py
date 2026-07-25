from __future__ import annotations

import argparse
import json
import os

from .configuration import ConfigStore, canonical_config_path

VERSION = "0.1.0-alpha"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zero", description="Zero administration console")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version")
    sub.add_parser("status")
    config = sub.add_parser("config")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    show = config_sub.add_parser("show")
    show.add_argument("--path", default=os.getenv("ZERO_CANONICAL_CONFIG", "config/zero.json"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(VERSION)
        return 0
    if args.command == "status":
        print(json.dumps({"version": VERSION, "config": str(canonical_config_path())}, indent=2))
        return 0
    if args.command == "config" and args.config_command == "show":
        print(json.dumps(ConfigStore(args.path).load().model_dump(mode="json", exclude_none=True), indent=2, sort_keys=True))
        return 0
    return 2
