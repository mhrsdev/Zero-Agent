from __future__ import annotations

import asyncio
from pathlib import Path

from conftest import CONFIG_EXAMPLE
from zero.config import ZeroConfig
from zero.replay import run_replay
from zero.storage import ZeroStore


def test_replay_finds_a_stored_message_without_network(tmp_path):
    config = ZeroConfig.load(CONFIG_EXAMPLE)
    db = tmp_path / "zero.db"
    config = config.model_copy(update={"memory": config.memory.model_copy(update={"db_path": str(db)})})
    store = ZeroStore(str(db))

    async def seed():
        await store.append_recent(-100, 7, "u7", "user", "hello zero", telegram_message_id=42)

    asyncio.run(seed())
    payload = run_replay(config, chat_id=-100, message_id=42, db_path=str(db))
    assert payload["ok"] is True
    assert payload["decision"]["reason"]
    assert payload["provider"] == "replay"


def test_replay_missing_message(tmp_path):
    config = ZeroConfig.load(CONFIG_EXAMPLE)
    db = tmp_path / "zero.db"
    config = config.model_copy(update={"memory": config.memory.model_copy(update={"db_path": str(db)})})
    ZeroStore(str(db))
    payload = run_replay(config, chat_id=-100, message_id=1, db_path=str(db))
    assert payload["ok"] is False
