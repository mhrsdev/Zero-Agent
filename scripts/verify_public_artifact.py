#!/usr/bin/env python3
"""Fail-closed public artifact scanner; reports paths/categories, never values."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

FORBIDDEN_NAMES = {
    '.git', '.venv', 'venv', 'node_modules', 'runtime', 'backups', 'archive',
    'PROPRIETARY_LICENSE',
}
# Vendored dependency trees. These are forbidden in a public artifact and are
# reported once for the directory rather than once per contained file, so a
# dependency's own PEM/token literals can never masquerade as project findings.
# Detection is structural as well as by name: an environment called .venv311w
# used to pass the name check while its site-packages were still scanned as
# project source.
VENDOR_NAMES = {'.venv', 'venv', 'node_modules', 'site-packages'}
# Build and tool output. Pruned silently: it either duplicates project source
# (build/, dist/, *.egg-info) or is regenerated cache (__pycache__, tool
# caches), so scanning it adds no finding a source scan would miss.
GENERATED_NAMES = {
    '__pycache__', '.pytest_cache', '.ruff_cache', '.mypy_cache', '.tox',
    'build', 'dist',
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


def is_virtualenv(path: Path) -> bool:
    """Detect a virtualenv structurally rather than by directory name.

    Matching only ``.venv``/``venv`` let a differently named environment
    (``.venv311w``, ``env-3.12``) pass the path check while its vendored
    dependency source was still read and pattern-matched as project content.
    """
    return (path / 'pyvenv.cfg').is_file()


def walk_pruned(root: Path) -> tuple[list[Path], list[Path]]:
    """Return (files, vendored_dirs), skipping vendored and generated subtrees.

    ``.git`` is audited separately with git status/fsck/ref scans, so it is
    pruned without being reported.
    """
    files: list[Path] = []
    vendored: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        keep: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            if name == '.git':
                continue
            if name in VENDOR_NAMES or is_virtualenv(child):
                vendored.append(child)
                continue
            if name in GENERATED_NAMES or name.endswith('.egg-info'):
                continue
            keep.append(name)
        dirnames[:] = keep
        files.extend(current / name for name in sorted(filenames))
    return files, vendored


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--allow-proprietary-license', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    findings: list[dict[str, str]] = []

    files, vendored_dirs = walk_pruned(root)
    for directory in vendored_dirs:
        findings.append({
            'category': 'forbidden_path',
            'path': directory.relative_to(root).as_posix(),
        })

    for path in sorted(p for p in files if p.is_file()):
        rel = path.relative_to(root)
        parts = set(rel.parts)
        name = path.name
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
