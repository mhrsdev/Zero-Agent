from __future__ import annotations

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime

from zero.stickers.models import Sticker, StickerSet, StickerCandidate, StickerStats


class TestStickerModels:
    """Test Sticker and related dataclasses."""

    def test_sticker_creation(self):
        """Test Sticker dataclass creation with minimal required fields."""
        sticker = Sticker(
            doc_id=12345,
            access_hash=987654321,
            file_reference=b'test_ref',
            mime_type='application/x-tgsticker',
            emoji='😂',
            stickerset_id=11111,
            stickerset_access_hash=22222,
            stickerset_short_name='TestPack',
            is_animated=True,
            is_video=False,
            first_seen=1234567890,
            last_seen=1234567890,
            first_sender_id=11111,
        )

        assert sticker.doc_id == 12345
        assert sticker.emoji == '😂'
        assert sticker.is_animated is True
        assert sticker.is_video is False
        assert sticker.quality_score == 0.5  # default
        assert sticker.usage_count == 0
        assert sticker.last_message_id is None

    def test_sticker_to_row(self):
        """Test Sticker to_row conversion."""
        sticker = Sticker(
            doc_id=12345,
            access_hash=987654321,
            file_reference=b'test_ref',
            mime_type='application/x-tgsticker',
            emoji='😂',
            stickerset_id=11111,
            stickerset_access_hash=22222,
            stickerset_short_name='TestPack',
            is_animated=True,
            is_video=False,
            vision_summary='Test vision',
            vision_tags='meme,funny',
            nsfw_score=0.0,
            mood_tags='funny',
            quality_score=0.8,
            spam_score=0.0,
            usage_count=5,
            first_seen=1234567890,
            last_seen=1234567890,
            first_sender_id=11111,
            saved_to_account=True,
            saved_at=1234567890,
            recent_saved=False,
            last_message_id=42,
        )

        row = sticker.to_row()
        assert len(row) == 24  # All 24 fields including last_message_id
        assert row[0] == 12345  # doc_id
        assert row[1] == 987654321  # access_hash
        assert row[19] == 11111  # first_sender_id
        assert row[16] == 5  # usage_count
        assert row[23] == 42  # last_message_id

    def test_sticker_to_input_document(self):
        """Test Sticker to_input_document conversion."""
        sticker = Sticker(
            doc_id=12345,
            access_hash=987654321,
            file_reference=b'test_ref',
            mime_type='application/x-tgsticker',
            emoji='😂',
            stickerset_id=11111,
            stickerset_access_hash=22222,
            stickerset_short_name='TestPack',
            is_animated=True,
            is_video=False,
        )

        input_doc = sticker.to_input_document()
        assert input_doc.id == 12345
        assert input_doc.access_hash == 987654321
        assert input_doc.file_reference == b'test_ref'

    def test_sticker_set_creation(self):
        """Test StickerSet dataclass creation."""
        sticker_set = StickerSet(
            set_id=12345,
            access_hash=987654321,
            short_name='TestPack',
            title='Test Pack',
            count=50,
            is_animated=True,
            is_video=False,
            is_official=True,
        )

        assert sticker_set.set_id == 12345
        assert sticker_set.short_name == 'TestPack'
        assert sticker_set.count == 50
        assert sticker_set.is_animated is True

    def test_sticker_set_to_row(self):
        """Test StickerSet to_row conversion."""
        sticker_set = StickerSet(
            set_id=12345,
            access_hash=987654321,
            short_name='TestPack',
            title='Test Pack',
            count=50,
            is_animated=True,
            is_video=False,
            is_official=True,
            installed=True,
            installed_at=1234567890,
            updated_at=1234567890,
        )

        row = sticker_set.to_row()
        assert row[0] == 12345  # set_id
        assert row[1] == 987654321  # access_hash
        assert row[2] == 'TestPack'  # short_name
        assert row[3] == 'Test Pack'  # title


class TestStickerCandidate:
    """Test StickerCandidate functionality."""

    def test_sticker_candidate_creation(self):
        """Test StickerCandidate creation."""
        sticker = Sticker(
            doc_id=12345,
            access_hash=987654321,
            file_reference=b'test_ref',
            mime_type='application/x-tgsticker',
            emoji='😂',
            stickerset_id=11111,
            stickerset_access_hash=22222,
            stickerset_short_name='TestPack',
            is_animated=True,
            is_video=False,
            quality_score=0.9,
        )

        candidate = StickerCandidate(
            sticker=sticker,
            score=0.95,
            match_reason="mood:funny"
        )

        assert candidate.sticker.doc_id == 12345
        assert candidate.score == 0.95
        assert candidate.match_reason == "mood:funny"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

def test_sticker_panel_module_resolves_every_runtime_name():
    """`/zero stickers send` constructed StickerCandidate without importing it.

    `from __future__ import annotations` hides missing names used in
    annotations, but this one is constructed in a function body, so the command
    raised NameError every time an operator ran it. Resolving every name the
    module calls catches the class of defect rather than the single instance.
    """
    import ast
    import builtins

    from zero.paths import repo_path

    source = repo_path("zero", "stickers", "panel.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    bound = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bound |= {(alias.asname or alias.name).split(".")[0] for alias in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(node.name)
            arguments = node.args
            for group in (arguments.posonlyargs, arguments.args, arguments.kwonlyargs):
                bound |= {argument.arg for argument in group}
            for optional in (arguments.vararg, arguments.kwarg):
                if optional is not None:
                    bound.add(optional.arg)
        elif isinstance(node, ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)

    missing = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id not in bound
    }
    assert not missing, f"names constructed at runtime but never imported: {sorted(missing)}"


def test_no_class_defines_the_same_method_twice():
    """A duplicated method silently shadows the earlier implementation.

    StickerAccountSaver defined save_to_favorites twice; the first version
    called Telegram but never updated the database, and was unreachable because
    the second definition replaced it. Whichever is intended, having both is a
    defect.
    """
    import ast

    from zero.paths import repo_path

    offenders: list[str] = []
    root = repo_path("zero")
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            seen: dict[str, int] = {}
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name in seen:
                        offenders.append(
                            f"{path.relative_to(root)}:{item.lineno} {node.name}.{item.name}"
                            f" (first at line {seen[item.name]})"
                        )
                    seen[item.name] = item.lineno
    assert offenders == [], f"duplicate method definitions: {offenders}"
