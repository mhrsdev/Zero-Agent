from __future__ import annotations

from pathlib import Path

from zero.core.memory_service import MemoryService
from zero.memory_v3 import MemoryV3Service
from zero.models import IncomingMessage


def test_memory_service_delegates_only_to_v3(tmp_path) -> None:
    service = MemoryService(MemoryV3Service(str(tmp_path / "v3.db")))
    message = IncomingMessage(
        chat_id=-100,
        chat_title="group",
        sender_id=7,
        sender_label="user",
        text="I prefer concise answers",
        message_id=1,
    )

    assert service.backend_name == "memory-v3"
    service.observe_sync_for_test(message)
    context, metadata = service.context_sync_for_test(message)
    assert metadata["selected"] >= 0
    assert "memory_v2" not in Path("zero/core/memory_service.py").read_text(encoding="utf-8")
