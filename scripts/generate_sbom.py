#!/usr/bin/env python3
"""Generate a CycloneDX 1.5 SBOM from the resolved environment.

The SBOM describes what is actually installed, resolved via importlib.metadata,
rather than what a requirements file asks for. Run it inside the environment
whose contents you want to describe.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ZERO_VERSION = "0.1.0-alpha"


def _license(dist: metadata.Distribution) -> list[dict]:
    meta = dist.metadata
    declared = meta.get("License")
    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if declared and declared.strip() and declared.strip().upper() != "UNKNOWN":
        return [{"license": {"name": declared.strip()}}]
    if classifiers:
        return [{"license": {"name": classifiers[-1].split("::")[-1].strip()}}]
    return []


def components() -> list[dict]:
    rows = []
    for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata.get("Name") or "").lower()):
        name = dist.metadata.get("Name")
        if not name:
            continue
        version = dist.version or "0"
        rows.append({
            "type": "library",
            "bom-ref": f"pkg:pypi/{name.lower()}@{version}",
            "name": name,
            "version": version,
            "purl": f"pkg:pypi/{name.lower()}@{version}",
            "licenses": _license(dist),
            "externalReferences": (
                [{"type": "website", "url": dist.metadata.get("Home-page")}]
                if dist.metadata.get("Home-page") else []
            ),
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="sbom.cdx.json")
    args = parser.parse_args()

    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tools": [{"vendor": "Zero", "name": "generate_sbom.py", "version": ZERO_VERSION}],
            "component": {
                "type": "application",
                "bom-ref": f"pkg:generic/zero@{ZERO_VERSION}",
                "name": "zero",
                "version": ZERO_VERSION,
                "licenses": [{"license": {"id": "Apache-2.0"}}],
            },
        },
        "components": components(),
    }
    payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
    target = Path(args.output)
    target.write_text(payload, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    print(json.dumps({
        "sbom": str(target),
        "components": len(document["components"]),
        "sha256": digest,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
