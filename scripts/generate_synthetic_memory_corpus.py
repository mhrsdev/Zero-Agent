#!/usr/bin/env python3
"""Generate Zero's deterministic, explicitly synthetic Memory V2 corpus."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "tests/fixtures/memory_v2/regression_corpus.jsonl"
SEED = 1389
CATEGORIES = (
    "casual",
    "education_track_correction",
    "multi_user_group",
    "project_continuation",
    "superseded",
    "forwarded",
    "bot",
    "restart",
    "session_change",
    "no_memory",
)


def build_cases(seed: int = SEED, count: int = 60) -> list[dict[str, object]]:
    rng = random.Random(seed)
    cases: list[dict[str, object]] = []
    for index in range(count):
        category = CATEGORIES[index % len(CATEGORIES)]
        token = f"memcase{index:03d}"
        expected_id = f"synthetic-{index:03d}-required"
        distractor_id = f"synthetic-{index:03d}-forbidden"
        no_memory = category in {"forwarded", "bot", "no_memory"}
        expected = [] if no_memory else [expected_id]
        stored = [
            {
                "id": distractor_id,
                "scope": "group_user",
                "content": f"unrelated{index:03d} synthetic distractor",
                "category": category,
                "provenance": {"kind": "synthetic", "event_id": index * 2 + 1},
            }
        ]
        if not no_memory:
            detail = rng.choice(("alpha", "bravo", "charlie", "delta"))
            stored.insert(0, {
                "id": expected_id,
                "scope": "group_user",
                "content": f"{token} synthetic {category} fact {detail}",
                "category": category,
                "provenance": {"kind": "synthetic", "event_id": index * 2 + 2},
            })
        cases.append({
            "schema_version": 1,
            "corpus_kind": "synthetic",
            "seed": seed,
            "case_id": f"syn-{index:03d}",
            "category": category,
            "query": token,
            "stored_memories": stored,
            "expected_relevant_memory_ids": expected,
            "acceptable_optional_memory_ids": [],
            "forbidden_memory_ids": [distractor_id],
            "expected_no_memory": no_memory,
        })
    return cases


def canonical_jsonl(cases: list[dict[str, object]]) -> str:
    return "".join(
        json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for case in cases
    )


def validate_cases(cases: list[dict[str, object]]) -> None:
    if len(cases) < 50:
        raise ValueError("synthetic corpus must contain at least 50 cases")
    missing = set(CATEGORIES) - {str(case.get("category")) for case in cases}
    if missing:
        raise ValueError(f"missing categories: {sorted(missing)}")
    seen: set[str] = set()
    for case in cases:
        case_id = str(case.get("case_id", ""))
        if not case_id or case_id in seen:
            raise ValueError(f"invalid or duplicate case_id: {case_id!r}")
        seen.add(case_id)
        if case.get("corpus_kind") != "synthetic":
            raise ValueError(f"{case_id}: corpus_kind must be synthetic")
        required = set(case.get("expected_relevant_memory_ids", []))
        optional = set(case.get("acceptable_optional_memory_ids", []))
        forbidden = set(case.get("forbidden_memory_ids", []))
        if required & optional or required & forbidden or optional & forbidden:
            raise ValueError(f"{case_id}: label sets overlap")
        memory_ids = {str(item.get("id")) for item in case.get("stored_memories", [])}
        if not (required | optional | forbidden) <= memory_ids:
            raise ValueError(f"{case_id}: labels reference unknown memories")
        if bool(case.get("expected_no_memory")) != (not required):
            raise ValueError(f"{case_id}: expected_no_memory conflicts with required labels")
        for item in case.get("stored_memories", []):
            if item.get("provenance", {}).get("kind") != "synthetic":
                raise ValueError(f"{case_id}: non-synthetic provenance")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    cases = build_cases()
    validate_cases(cases)
    payload = canonical_jsonl(cases)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if args.check:
        if not args.output.is_file() or args.output.read_text() != payload:
            print(json.dumps({"status": "FAILED", "reason": "synthetic_corpus_drift"}))
            return 1
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    print(json.dumps({
        "status": "PASSED",
        "corpus_kind": "synthetic",
        "cases": len(cases),
        "seed": SEED,
        "sha256": digest,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
