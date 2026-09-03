from pathlib import Path

from conftest import CONFIG_EXAMPLE
from zero.brain import ZeroBrain
from zero.config import ZeroConfig
from zero.storage import ZeroStore


class NoCallRouter:
    keys = []
    gemini_keys = []
    last_route = {}

    async def complete(self, *args, **kwargs):
        raise AssertionError("not used")


def test_prompt_token_budget_trims_memory_context(tmp_path):
    config = ZeroConfig.load(CONFIG_EXAMPLE)
    config = config.model_copy(
        update={
            "memory": config.memory.model_copy(update={"db_path": str(tmp_path / "zero.db"), "prompt_token_budget": 4}),
        }
    )
    brain = ZeroBrain(config, ZeroStore(config.memory.db_path), NoCallRouter())
    long = "x" * 100
    trimmed = brain._apply_prompt_budget(long)
    assert len(trimmed) == 16
