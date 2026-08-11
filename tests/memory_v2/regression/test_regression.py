import asyncio
import json
import subprocess
import sys
from pathlib import Path

from zero.memory_v2.service import MemoryItem, MemoryV2Service
from zero.models import IncomingMessage

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "tests/fixtures/memory_v2/regression_corpus.jsonl"
REQUIRED_CATEGORIES = {
    "casual", "education_track_correction", "multi_user_group",
    "project_continuation", "superseded", "forwarded", "bot",
    "restart", "session_change",
}


def load_cases():
    return [json.loads(line) for line in CORPUS.read_text().splitlines()]


def test_corpus_has_required_minimum_categories_and_synthetic_label():
    rows = load_cases()
    assert len(rows) >= 50
    assert REQUIRED_CATEGORIES <= {row["category"] for row in rows}
    assert {row["corpus_kind"] for row in rows} == {"synthetic"}
    assert {row["seed"] for row in rows} == {1389}


def test_corpus_schema_has_disjoint_ground_truth_labels():
    for row in load_cases():
        required = set(row["expected_relevant_memory_ids"])
        optional = set(row["acceptable_optional_memory_ids"])
        forbidden = set(row["forbidden_memory_ids"])
        memory_ids = {item["id"] for item in row["stored_memories"]}
        assert not (required & optional or required & forbidden or optional & forbidden)
        assert required | optional | forbidden <= memory_ids
        assert row["expected_no_memory"] is (not required)
        assert all(item["provenance"]["kind"] == "synthetic" for item in row["stored_memories"])


def test_checked_in_corpus_matches_deterministic_generator():
    completed = subprocess.run(
        [sys.executable, "scripts/generate_synthetic_memory_corpus.py", "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert '"status": "PASSED"' in completed.stdout


def test_long_conversation_stays_bounded(tmp_path):
    async def run():
        service = MemoryV2Service(str(tmp_path / "v2.db"))
        message = IncomingMessage(1, "g", 1, "u", "zero deployment", message_id=1)
        for index in range(500):
            await service.put(MemoryItem(
                "", "fact", "group_user", f"zero deployment step {index}",
                "zero deployment", 1, 1, group_id=1, subject="deploy",
                predicate=f"step{index}", importance=.8, confidence=.9,
            ))
        block, meta = await service.context(message)
        assert meta["selected"] <= 5
        assert meta["tokens"] <= 700
        assert len(block) < 3000
    asyncio.run(run())
