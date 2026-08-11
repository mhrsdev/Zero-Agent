from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon.tl import types

import zero.vision as vision_mod
from zero.brain import ZeroBrain
from zero.config import ZeroConfig
from zero.models import IncomingMessage
from zero.storage import ZeroStore
from zero.stickers.observer import StickerObserver
from zero.stickers.models import Sticker
from zero.vision import VisionProcessor


def config():
    cfg = ZeroConfig.load("config/zero.example.yaml")
    return cfg.model_copy(update={
        "vision": cfg.vision.model_copy(update={
            "enabled": True,
            "cooldown_seconds": 0,
            "max_gifs_per_user_per_window": 100,
        })
    })


def gif_event(*, payload=b"fake-mp4", sender_id=42):
    return SimpleNamespace(
        media=True,
        photo=None,
        sender_id=sender_id,
        raw_text="",
        file=SimpleNamespace(name="animation.mp4"),
        document=SimpleNamespace(
            id=100,
            size=len(payload),
            mime_type="video/mp4",
            attributes=[types.DocumentAttributeAnimated()],
        ),
        download_media=AsyncMock(return_value=payload),
    )


@pytest.mark.asyncio
async def test_provider_exception_is_structured_and_never_escapes(tmp_path, monkeypatch):
    store = ZeroStore(str(tmp_path / "provider.db"))
    processor = VisionProcessor(config(), ["synthetic-key"], store)
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpg")

    async def frames(_path):
        return [str(frame)]

    async def explode(*_args, **_kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(vision_mod, "extract_video_frames", frames)
    monkeypatch.setattr(vision_mod, "analyze_image_with_gemini", explode)
    outcome = await processor.process_outcome(gif_event(), question="چی میشه؟")
    assert outcome.ok is False
    assert outcome.reason == "analysis_exception"
    assert outcome.exception_type == "RuntimeError"
    assert not frame.exists()


@pytest.mark.asyncio
async def test_corrupt_animation_has_frame_extraction_reason_and_no_provider_call(tmp_path, monkeypatch):
    store = ZeroStore(str(tmp_path / "frames.db"))
    processor = VisionProcessor(config(), ["synthetic-key"], store)
    provider = AsyncMock(return_value="must not run")
    monkeypatch.setattr(vision_mod, "extract_video_frames", AsyncMock(return_value=[]))
    monkeypatch.setattr(vision_mod, "analyze_image_with_gemini", provider)
    outcome = await processor.process_outcome(gif_event(payload=b"corrupt"))
    assert outcome.ok is False
    assert outcome.reason == "frame_extraction_failed"
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_exception_is_structured_and_never_escapes(tmp_path):
    store = ZeroStore(str(tmp_path / "download.db"))
    processor = VisionProcessor(config(), ["synthetic-key"], store)
    event = gif_event()
    event.download_media = AsyncMock(side_effect=ConnectionError("telegram interrupted"))
    outcome = await processor.process_outcome(event)
    assert outcome.ok is False
    assert outcome.reason == "download_failed"


@pytest.mark.asyncio
async def test_oversized_media_is_rejected_before_telegram_download(tmp_path):
    cfg = config()
    store = ZeroStore(str(tmp_path / "oversized.db"))
    processor = VisionProcessor(cfg, ["synthetic-key"], store)
    event = gif_event()
    event.document.size = (cfg.vision.max_file_size_mb * 1024 * 1024) + 1
    outcome = await processor.process_outcome(event)
    assert outcome.ok is False
    assert outcome.reason == "download_failed"
    event.download_media.assert_not_awaited()


@pytest.mark.asyncio
async def test_extracted_frames_are_sent_as_jpeg_and_cleaned(tmp_path, monkeypatch):
    store = ZeroStore(str(tmp_path / "success.db"))
    processor = VisionProcessor(config(), ["synthetic-key"], store)
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpg")
    seen = {}

    async def analyze(paths, prompt, api_key, model, mime):
        seen.update(paths=paths, mime=mime, api_key=api_key)
        return "یک حرکت خنده‌دار"

    monkeypatch.setattr(vision_mod, "extract_video_frames", AsyncMock(return_value=[str(frame)]))
    monkeypatch.setattr(vision_mod, "analyze_image_with_gemini", analyze)
    outcome = await processor.process_outcome(gif_event())
    assert outcome.ok is True
    assert outcome.reason == "analyzed"
    assert outcome.frame_count == 1
    assert seen["mime"] == "image/jpeg"
    assert not frame.exists()


@pytest.mark.asyncio
async def test_direct_video_reply_analyzes_direct_video_not_replied_image(tmp_path):
    cfg = config()
    brain = object.__new__(ZeroBrain)
    brain.config = cfg
    brain._pre_check = AsyncMock(return_value=(None, ""))
    brain.vision = SimpleNamespace(process=AsyncMock(return_value=None))
    message = IncomingMessage(chat_id=-100, chat_title="g", sender_id=42, sender_label="u", text="این چیه؟")
    replied = SimpleNamespace(
        media=True, photo=object(), document=None, sender_id=7, raw_text="",
    )
    direct = SimpleNamespace(
        media=True,
        photo=None,
        sender_id=42,
        raw_text="",
        document=SimpleNamespace(
            mime_type="video/mp4",
            attributes=[types.DocumentAttributeVideo(duration=1.0, w=320, h=240, round_message=False, supports_streaming=True, nosound=False)],
        ),
        is_reply=True,
        get_reply_message=AsyncMock(return_value=replied),
    )
    decision, _ = await brain.maybe_reply_with_media(message, direct)
    assert decision.reason == "vision_unavailable"
    brain.vision.process.assert_awaited_once_with(direct, question="این چیه؟")


@pytest.mark.asyncio
async def test_download_returned_temp_path_remains_available_to_caller(tmp_path):
    media_path = tmp_path / "animation.mp4"
    media_path.write_bytes(b"synthetic-video")
    event = gif_event()
    event.download_media = AsyncMock(return_value=str(media_path))
    downloaded = await vision_mod.download_media(event, config(), 10)
    assert downloaded == str(media_path)
    assert media_path.exists()
    media_path.unlink()


@pytest.mark.asyncio
async def test_real_provider_wrapper_propagates_transport_exception(monkeypatch, tmp_path):
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"jpeg")
    async def explode(_call):
        raise RuntimeError("provider offline")
    monkeypatch.setattr(vision_mod.asyncio, "to_thread", explode)
    with pytest.raises(RuntimeError, match="provider offline"):
        await vision_mod.analyze_image_with_gemini(
            str(image), "describe", "synthetic-key", "synthetic-model", "image/jpeg"
        )


@pytest.mark.asyncio
async def test_missing_provider_key_is_analysis_unavailable(tmp_path):
    processor = VisionProcessor(config(), [], ZeroStore(str(tmp_path / "missing-key.db")))
    outcome = await processor.process_outcome(gif_event())
    assert outcome.reason == "analysis_unavailable"
    assert outcome.exception_type == "missing_api_key"


@pytest.mark.asyncio
async def test_empty_provider_result_is_no_semantic_signature(monkeypatch, tmp_path):
    processor = VisionProcessor(config(), ["synthetic-key"], ZeroStore(str(tmp_path / "empty.db")))
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"frame")
    async def frames(_path):
        return [str(frame)]
    async def empty(*_args, **_kwargs):
        return "   "
    monkeypatch.setattr(vision_mod, "extract_video_frames", frames)
    monkeypatch.setattr(vision_mod, "analyze_image_with_gemini", empty)
    outcome = await processor.process_outcome(gif_event())
    assert outcome.reason == "no_semantic_signature"


@pytest.mark.asyncio
async def test_empty_sticker_analysis_does_not_poison_semantic_backfill():
    cfg = config()
    store = SimpleNamespace(update_sticker_vision=AsyncMock())
    vision = SimpleNamespace(analyze=AsyncMock(return_value=None))
    observer = StickerObserver(cfg, store, client=AsyncMock(), vision=vision)
    event = SimpleNamespace(download_media=AsyncMock())
    sticker = Sticker(
        doc_id=123, access_hash=1, file_reference=b"ref", mime_type="image/webp",
        emoji="", stickerset_id=None, stickerset_access_hash=None,
        stickerset_short_name=None, is_animated=False, is_video=False,
    )
    await observer._process_vision(sticker, event)
    store.update_sticker_vision.assert_not_awaited()
    assert sticker.vision_summary is None
