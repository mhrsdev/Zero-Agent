"""Semantic classification of every sqlite_txn site (audit tooling, not a test)."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

DML = re.compile(r"\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|REPLACE)\b", re.I)
SEL = re.compile(r"\bSELECT\b", re.I)
SITE = re.compile(r"^([ \t]*)with sqlite_txn\((.*?)\) as (\w+):(.*)$")


def _block_body(lines: list[str], start: int, base_indent: str, inline: str) -> str:
    parts = [inline]
    i = start + 1
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        indent = line[: len(line) - len(line.lstrip())]
        if len(indent) <= len(base_indent):
            break
        parts.append(line.strip())
        i += 1
    return "\n".join(parts)


def classify(root: Path) -> list[tuple[str, str, str, int]]:
    sites = []
    for p in sorted(root.rglob("*.py")):
        lines = p.read_text(errors="replace").splitlines()
        for idx, line in enumerate(lines):
            m = SITE.match(line)
            if not m:
                continue
            base_indent, src, var, inline = m.groups()
            body = _block_body(lines, idx, base_indent, inline)
            if DML.search(body):
                kind = "write_txn"
            elif SEL.search(body):
                kind = "read_only"
            else:
                kind = "schema_or_other"
            sites.append((str(p), src, kind, body.count("\n") + 1))
    return sites


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    # Show the real call-shape distribution first.
    shapes = Counter()
    for p in (root / "zero").rglob("*.py"):
        text = p.read_text(errors="replace")
        for m in re.finditer(r"sqlite_txn[^\n]{0,70}", text):
            shapes[re.sub(r"\s+", " ", m.group(0)).strip()[:60]] += 1
    print("call-shape samples:")
    for shape, n in shapes.most_common(12):
        print(f"  {n:3d}x {shape}")
    sites = classify(root / "zero") + classify(root / "tests")
    print("total sqlite_txn sites:", len(sites))
    print("by kind:", dict(Counter(k for _, _, k, _ in sites)))
    per_file = Counter(p for p, _, _, _ in sites)
    print("files:", len(per_file), "| top:", per_file.most_common(5))
    long_bodies = sorted(sites, key=lambda s: -s[3])[:5]
    print("largest bodies (lines):", [(p, n) for p, _, _, n in long_bodies])
    # Ownership check: sqlite_txn must only wrap connections the block itself
    # created via store._conn()/sqlite3.connect -- flag any other source expr.
    odd = [p for p, src, _, _ in sites if not re.match(r"\s*(self\.store\._conn\(\)|store\._conn\(\)|sqlite3\.connect|conn|self\._conn)", src)]
    print("non-standard connection sources:", odd or "none")
    print("panel_store keeps its own contextmanager:",
          "sqlite_txn" not in (root / "zero" / "panel_store.py").read_text())


if __name__ == "__main__":
    main()