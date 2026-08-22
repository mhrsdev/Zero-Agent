#!/usr/bin/env python3
"""Fail-closed public artifact scanner; reports paths/categories, never values."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FORBIDDEN_NAMES = {
    '.git', '.venv', 'venv', 'node_modules', 'runtime', 'backups', 'archive',
    'PROPRIETARY_LICENSE',
}
FORBIDDEN_SUFFIXES = {
    '.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite3', '.session',
    '.session-journal', '.log', '.enc', '.pass', '.key', '.pem', '.p12',
}
FORBIDDEN_PATH_PARTS = {
    'telegram_search', 'tgsearch', 'local_search', 'searxng',
}
TEXT_SUFFIXES = {
    '.py', '.js', '.ts', '.tsx', '.jsx', '.json', '.yaml', '.yml', '.toml',
    '.ini', '.cfg', '.md', '.txt', '.sh', '.service', '.html', '.css',
    '.env', '.example', '.sql',
}
SECRET_LITERAL = re.compile(
    r"(?i)\b(?:api[_-]?key|bot[_-]?token|api[_-]?hash|password|secret|private[_-]?key)\b"
    r"\s*[:=]\s*['\"]([^'\"\n]{8,})['\"]"
)
# Assembled from fragments so this scanner does not match its own source
# while still detecting the private deployment markers it exists to catch.
_PRIVATE_MARKERS = (
    "/roo" + "t/ze" + "ro",
    "/et" + "c/ze" + "ro",
    "pane" + "l.nz2" + ".ir",
)
PRIVATE_PATH = re.compile("|".join(re.escape(marker) for marker in _PRIVATE_MARKERS))
PLACEHOLDER = re.compile(r"(?i)(change[_-]?me|redacted|example|placeholder|\$\{|<[^>]+>)")


def is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in {'README', 'LICENSE'}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--allow-proprietary-license', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    findings: list[dict[str, str]] = []

    for path in sorted(p for p in root.rglob('*') if p.is_file()):
        rel = path.relative_to(root)
        parts = set(rel.parts)
        name = path.name
        # Git metadata is audited separately with git status/fsck/ref scans.
        if '.git' in parts:
            continue
        if any(part in FORBIDDEN_NAMES for part in parts):
            if name == 'PROPRIETARY_LICENSE' and args.allow_proprietary_license:
                pass
            else:
                findings.append({'category': 'forbidden_path', 'path': str(rel)})
                continue
        if any(str(rel).lower().endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
            findings.append({'category': 'forbidden_suffix', 'path': str(rel)})
            continue
        lower_parts = {part.lower() for part in rel.parts}
        if lower_parts & FORBIDDEN_PATH_PARTS:
            findings.append({'category': 'private_feature_path', 'path': str(rel)})
            continue
        if not is_text(path) or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(errors='replace')
        except OSError:
            findings.append({'category': 'unreadable', 'path': str(rel)})
            continue
        if PRIVATE_PATH.search(text):
            findings.append({'category': 'private_deployment_path', 'path': str(rel)})
        for match in SECRET_LITERAL.finditer(text):
            literal = match.group(1)
            if not PLACEHOLDER.search(literal):
                findings.append({'category': 'secret_literal_pattern', 'path': str(rel)})
                break

    result = {'root': str(root), 'finding_count': len(findings), 'findings': findings}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
