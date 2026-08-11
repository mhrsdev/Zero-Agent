#!/usr/bin/env python3
"""Prepare a blind review queue for an explicitly supplied real anonymized corpus."""
import json
import os
import re
from pathlib import Path

TOKEN = re.compile(r"[\w\u0600-\u06ff]+")


def main() -> int:
    source = Path(os.getenv(
        "ZERO_REAL_MEMORY_CORPUS",
        "tests/fixtures/memory_v2/real_anonymized_corpus.jsonl",
    ))
    if not source.is_file():
        print(json.dumps({
            "status": "BLOCKED",
            "reason": "real_anonymized_corpus_missing",
            "semantic_review": "REQUIRED",
        }))
        return 0
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    output = []
    for row in rows:
        for memory in row.get("stored_memories", []):
            query_terms = set(TOKEN.findall(row["query"].casefold()))
            memory_terms = set(TOKEN.findall(memory["content"].casefold()))
            output.append({
                "case_id": row["case_id"],
                "query": row["query"],
                "candidate_memory": memory["content"],
                "source_provenance": memory.get("provenance"),
                "lexical_relationship": {"shared_terms": len(query_terms & memory_terms)},
                "semantic_relationship": "unreviewed",
                "answer_necessity": "unreviewed",
                "label_validity": "provenance_only",
                "recommended_label": "AMBIGUOUS",
                "confidence": 0.0,
            })
    destination = Path("runtime/review/memory_semantic_label_audit.jsonl")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(
        json.dumps(item, ensure_ascii=False) + "\n" for item in output
    ))
    print(json.dumps({
        "status": "SEMANTIC REVIEW REQUIRED",
        "positive_candidates": len(output),
        "required": 0,
        "optional": 0,
        "irrelevant": 0,
        "ambiguous": len(output),
        "invalid_case": 0,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
