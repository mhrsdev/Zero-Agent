from conftest import CONFIG_EXAMPLE
import pytest

from zero.brain import ZeroBrain
from zero.config import ZeroConfig
from zero.models import Decision, IncomingMessage, RouteResult
from zero.security import classify_intent
from zero.storage import ZeroStore
from zero.memory_v3 import MemoryV3Item


class Router:
    keys = []

    def __init__(self):
        self.prompts = []

    async def complete(self, prompt, *, max_output_tokens=700):
        self.prompts.append(prompt)
        return RouteResult(text="ok", provider="test", model="test", attempts=1)


@pytest.mark.asyncio
async def test_normal_prompt_memory_uses_v3_without_legacy_composer(tmp_path, monkeypatch):
    monkeypatch.setenv("ZERO_MEMORY_V2_ENABLED", "false")
    monkeypatch.setenv("ZERO_MEMORY_V2_SHADOW", "false")
    cfg = ZeroConfig.load(CONFIG_EXAMPLE)
    cfg = cfg.model_copy(update={"memory": cfg.memory.model_copy(update={"db_path": str(tmp_path / "legacy.db")})})
    store = ZeroStore(cfg.memory.db_path)
    router = Router()
    brain = ZeroBrain(cfg, store, router)
    message = IncomingMessage(-1, "g", 7, "u", "what do you remember?", mention_zero=True, message_id=1)
    await store.add_long_memory(-1, "legacy", "LEGACY_MUST_NOT_REACH_PROMPT", created_by=7, subject_user_id=7, confidence=.99)
    await brain.memory.put(MemoryV3Item.personal(chat_id=-1, user_id=7, content="V3_MUST_REACH_PROMPT", importance=1, confidence=1))

    async def forbidden_legacy_composer(*args, **kwargs):
        raise AssertionError("legacy composer used by normal prompt path")

    monkeypatch.setattr("zero.brain.compose_memory_context", forbidden_legacy_composer)
    _, reply = await brain._handle_no_media(message, Decision(True, "test"), classify_intent(message.text, message.reply_text))
    assert reply == "ok"
    assert "V3_MUST_REACH_PROMPT" in router.prompts[-1]
    assert "LEGACY_MUST_NOT_REACH_PROMPT" not in router.prompts[-1]
