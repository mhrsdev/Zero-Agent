from types import SimpleNamespace

from zero.debug_trace import emit_reply_trace, redact_event


def test_redact_event_drops_prompt_and_text_by_default():
    out = redact_event({"prompt": "secret user text", "trace_id": "abc", "chat_id": -1})
    assert "prompt" not in out
    assert out["prompt_chars"] == len("secret user text")
    assert out["trace_id"] == "abc"


def test_emit_reply_trace_writes_jsonl_when_enabled(tmp_path):
    path = tmp_path / "trace.jsonl"
    config = SimpleNamespace(debug=SimpleNamespace(trace_replies=True, log_prompts=False, trace_path=str(path)))
    emit_reply_trace(config, {"trace_id": "t1", "text": "do not persist", "reason": "triggered"})
    line = path.read_text(encoding="utf-8")
    assert "t1" in line
    assert "do not persist" not in line
    assert "triggered" in line


def test_emit_is_noop_when_disabled(tmp_path):
    path = tmp_path / "trace.jsonl"
    config = SimpleNamespace(debug=SimpleNamespace(trace_replies=False, log_prompts=False, trace_path=str(path)))
    emit_reply_trace(config, {"trace_id": "t1"})
    assert not path.exists()
