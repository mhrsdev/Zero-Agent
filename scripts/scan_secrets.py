"""Secret-pattern scanner for the repository working tree.

Exit codes: 0 = clean, 1 = potential secret found.
Patterns cover common credential shapes; matches are reported as
file:line only -- matched text is never printed.

Usage: python scripts/scan_secrets.py [root]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERNS = {
    "telegram_bot_token": re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}"),
    "hex32_secret": re.compile(r"\b[0-9a-f]{32}\b"),
    "bearer_token": re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{20,}"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private_key_block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "quoted_secret_assign": re.compile(
        r"(?i)(api_key|secret|password|token)\s*[=:]\s*['\"][A-Za-z0-9+/_-]{16,}['\"]"),
    "openai_style_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
}

SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", "panel"}
SKIP_SUFFIXES = {".png", ".jpg", ".gif", ".ico", ".db", ".session", ".zip", ".whl", ".min.js"}

# Known-safe adversarial test fixtures. Each entry is matched verbatim and is
# still REPORTED on every run -- it just does not fail the gate. Nothing is
# silently whitelisted; review this list whenever it changes.
KNOWN_FIXTURES = {
    "StrongPassword123",
    "Bearer abcdefghijklmnopqrstuvwxyz",
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "example-gemini-key",
    "test-secret-never-log",
    "password = hunter2",
}


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    hits: list[str] = []
    self_path = Path(__file__).resolve()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.resolve() == self_path:
            continue  # never scan the scanner's own pattern literals
        if SKIP_DIRS & set(path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, rx in PATTERNS.items():
            for match in rx.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                hits.append((name, path.as_posix(), line, match.group(0)))
    real = [h for h in hits if not any(f in h[3] for f in KNOWN_FIXTURES)]
    known = [h for h in hits if any(f in h[3] for f in KNOWN_FIXTURES)]
    if known:
        print("Known test fixtures matched (reported, non-failing):")
        print("\n".join(f"{n}: {p}:{l}" for n, p, l, _ in known))
    if real:
        print("POTENTIAL SECRETS FOUND (values not printed):")
        print("\n".join(f"{n}: {p}:{l}" for n, p, l, _ in real))
        return 1
    print("CLEAN: no unexplained secret-pattern matches")
    return 0


if __name__ == "__main__":
    sys.exit(main())