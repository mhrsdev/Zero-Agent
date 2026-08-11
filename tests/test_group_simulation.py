from zero.group_simulation import build_members


def test_simulation_roster_has_required_identity_collisions():
    members = build_members()

    assert len(members) == 15
    assert len({member.user_id for member in members}) == 15
    exact = [member for member in members if member.identity_group == "exact_ali"]
    cross = [member for member in members if member.identity_group == "cross_script_reza"]
    assert len(exact) == 3 and {member.display_name for member in exact} == {"علی"}
    assert len(cross) == 3 and {member.display_name for member in cross} == {"رضا", "Reza", "REZA"}
    assert sum(member.abusive for member in members) == 4
    assert len({member.preference for member in members}) == 15


import pytest

from zero.group_simulation import run_simulation


@pytest.mark.asyncio
async def test_1000_message_simulation_exercises_memory_identity_and_reply_graph(tmp_path):
    report = await run_simulation(tmp_path, message_count=1000, seed=1389)
    assert report["passed"] is True
    assert report["incoming_messages"] == 1000
    assert report["member_count"] == report["human_sender_count"] == report["profile_count"] == 15
    assert report["exact_duplicate_profiles"] == report["cross_script_profiles"] == 3
    assert report["abusive_member_count"] == 4 and report["clean_member_count"] == 11
    assert report["abusive_message_count"] >= 20
    assert report["personal_memory_owner_count"] == 15
    assert report["memory_isolation_failures"] == []
    assert report["direct_reply_ancestor_ids"][:2] == [1002, 1001]
    assert report["cross_script_reply_ancestor_ids"][:2] == [1005, 1004]
    assert report["long_chain_depth"] == 16
    assert report["nearest_human_after_zero_matches"] is True
    assert report["reply_edge_count"] >= 30
    assert report["social_reputation"] < 0
    assert report["conflict_should_ignore"] is True
    assert report["sqlite_integrity"] == {"main": "ok", "memory_v3": "ok"}
    assert report["messages_jsonl_lines"] == report["stored_messages"]
    for name in ("group-simulation-report.json", "messages.jsonl", "members.json"):
        assert (tmp_path / name).exists()


def test_simulation_cli_resolves_project_package_outside_repo(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "simulate_group.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "deterministic local group simulation" in result.stdout
