from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_listener_and_panel_have_no_first_group_routing():
    listener = (ROOT / "scripts" / "run_listener.py").read_text(encoding="utf-8")
    panel = (ROOT / "scripts" / "run_panel.py").read_text(encoding="utf-8")
    assert "groups[0]" not in listener
    assert "allowed_group_ids[0]" not in panel
    assert "active[0]" not in panel


def test_cooldowns_are_group_scoped_in_runtime_sources():
    listener = (ROOT / "scripts" / "run_listener.py").read_text(encoding="utf-8")
    brain = (ROOT / "zero" / "brain.py").read_text(encoding="utf-8")
    policy = (ROOT / "zero" / "brain_policy.py").read_text(encoding="utf-8")
    assert "last_starter_at:{int(chat_id)}" in listener
    assert "last_interject_at:{int(incoming.chat_id)}" in listener
    assert "last_interject_at:{int(message.chat_id)}" in (brain + policy)
