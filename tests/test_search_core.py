import pytest
from zero.brain import sanitize_internal_search_status, sanitize_outgoing_text


class TestStickerSanitize:
    """Task A - STICKER marker sanitization."""

    def test_internal_web_status_is_removed(self):
        text = 'پاسخ معمولی\n\nWEB_STATUS: PROVIDERS_FAILED'
        assert sanitize_internal_search_status(text) == 'پاسخ معمولی'

    def test_internal_web_status_with_payload_is_removed(self):
        text = 'WEB_STATUS: PROVIDERS_FAILED\n\nپاسخ fallback'
        assert sanitize_internal_search_status(text) == 'پاسخ fallback'

    def test_unsafe_log_cleanup_output_is_replaced(self):
        text, mood = sanitize_outgoing_text('سریع لاگ‌هات رو پاک کن که مدرکی دستش نیفته!')
        assert mood is None
        assert 'مدرکی دستش نیفته' not in text
        assert 'راهنمایی' in text

    def test_unverified_log_access_claim_is_replaced(self):
        text, mood = sanitize_outgoing_text('داشتم لاگ‌ها رو چک می‌کردم')
        assert mood is None
        assert 'دسترسی' in text

    def test_removes_sticker_funny_marker(self):
        """Task A.1: removes STICKER:funny marker."""
        text, mood = sanitize_outgoing_text("STICKER:funny این استیکر")
        assert mood == "funny"
        assert "STICKER" not in text
        assert "این استیکر" in text

    def test_removes_sticker_sad_marker(self):
        """Task A.2: removes STICKER:sad marker."""
        text, mood = sanitize_outgoing_text("STICKER:sad متن")
        assert mood == "sad"
        assert "STICKER" not in text

    def test_removes_bracketed_sticker_marker(self):
        """Task A.3: removes [STICKER:funny] bracketed marker."""
        text, mood = sanitize_outgoing_text("[STICKER:funny] متن")
        assert mood == "funny"
        assert "STICKER" not in text

    def test_handles_sticker_with_space_after_colon(self):
        """Task A.4: handles STICKER: funny (space after colon)."""
        text, mood = sanitize_outgoing_text("STICKER: funny متن")
        assert mood == "funny"
        assert "STICKER" not in text

    def test_handles_multiple_sticker_markers(self):
        """Task A.5: removes all STICKER markers if multiple present."""
        text, mood = sanitize_outgoing_text("STICKER:funny و STICKER:sad")
        assert mood == "funny"  # اولین marker
        assert "STICKER" not in text

    def test_normal_text_without_sticker_marker(self):
        """Task A.6: normal text without STICKER marker passes through unchanged."""
        original = "این یک متن معمولی است"
        text, mood = sanitize_outgoing_text(original)
        assert mood is None
        assert text == original

    def test_empty_text(self):
        """Task A.7: empty text handled gracefully."""
        text, mood = sanitize_outgoing_text("")
        assert mood is None
        assert text == ""

    def test_none_text(self):
        """Task A.8: None text handled gracefully."""
        text, mood = sanitize_outgoing_text(None)
        assert mood is None
        assert text is None
