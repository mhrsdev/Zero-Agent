from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class Sticker:
    """Represents a sticker with all its metadata."""
    doc_id: int
    access_hash: int
    file_reference: bytes
    mime_type: str
    emoji: Optional[str]
    stickerset_id: Optional[int]
    stickerset_access_hash: Optional[int]
    stickerset_short_name: Optional[str]
    is_animated: bool
    is_video: bool
    vision_summary: Optional[str] = None
    vision_tags: Optional[str] = None
    nsfw_score: float = 0.0
    mood_tags: Optional[str] = None
    quality_score: float = 0.5
    spam_score: float = 0.0
    reaction_score: int = 0
    usage_count: int = 0
    first_seen: int = 0
    last_seen: int = 0
    first_sender_id: Optional[int] = None
    saved_to_account: bool = False
    saved_at: Optional[int] = None
    recent_saved: bool = False
    last_message_id: Optional[int] = None
    source_chat_id: Optional[int] = None
    source_message_id: Optional[int] = None
    source_sender_id: Optional[int] = None
    inferred_mood: Optional[str] = None
    is_available: bool = True
    failure_count: int = 0
    send_count: int = 0
    last_sent_at: Optional[int] = None

    def to_row(self) -> tuple:
        """Convert to database row tuple."""
        return (
            self.doc_id, self.access_hash, self.file_reference, self.mime_type,
            self.emoji, self.stickerset_id, self.stickerset_access_hash, self.stickerset_short_name,
            int(self.is_animated), int(self.is_video),
            self.vision_summary, self.vision_tags,
            self.nsfw_score, self.mood_tags, self.quality_score, self.spam_score,
            self.usage_count, self.first_seen, self.last_seen, self.first_sender_id,
            int(self.saved_to_account), self.saved_at, int(self.recent_saved),
            self.last_message_id
        )

    def to_input_document(self):
        """Convert to telethon InputDocument."""
        from telethon.tl import types
        return types.InputDocument(
            id=self.doc_id,
            access_hash=self.access_hash,
            file_reference=self.file_reference
        )

    def get_document_attribute_sticker(self):
        """Get the DocumentAttributeSticker for sending."""
        from telethon.tl import types
        stickerset = None
        if self.stickerset_short_name:
            from telethon.tl import types
            stickerset = types.InputStickerSetShortName(short_name=self.stickerset_short_name)
        elif self.stickerset_id and self.stickerset_access_hash:
            from telethon.tl import types
            stickerset = types.InputStickerSetID(
                id=self.stickerset_id,
                access_hash=self.stickerset_access_hash
            )
        
        from telethon.tl import types
        return types.DocumentAttributeSticker(
            alt=self.emoji or '',
            stickerset=stickerset or types.InputStickerSetEmpty(),
            mask=False
        )


@dataclass(slots=True)
class StickerSet:
    """Represents a sticker pack/set."""
    set_id: int
    access_hash: int
    short_name: str
    title: str
    count: int = 0
    is_animated: bool = False
    is_video: bool = False
    is_official: bool = False
    installed: bool = False
    installed_at: Optional[int] = None
    updated_at: int = 0

    def to_row(self) -> tuple:
        return (
            self.set_id, self.access_hash, self.short_name, self.title,
            self.count, int(self.is_animated), int(self.is_video), int(self.is_official),
            int(self.installed), self.installed_at, self.updated_at
        )


@dataclass(slots=True)
class StickerCandidate:
    """A candidate sticker plus explainable relevance metadata."""
    sticker: 'Sticker'
    score: float = 0.0
    match_reason: str = ''
    relevance_score: float = 0.0
    fallback_level: str = 'none'


@dataclass(slots=True)
class StickerStats:
    """Statistics about the sticker library."""
    total: int = 0
    saved_to_account: int = 0
    recent_saved: int = 0
    animated: int = 0
    video: int = 0
    avg_quality: float = 0.0
    total_usage: int = 0