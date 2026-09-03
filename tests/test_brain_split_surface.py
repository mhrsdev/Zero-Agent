"""Public brain helpers stay importable after the S5 mixin split."""
from zero.brain import (
    ZeroBrain,
    parse_search_command,
    sanitize_outgoing_text,
    user_requests_sticker,
)
from zero.brain_generate import BrainGenerateMixin
from zero.brain_media import BrainMediaMixin
from zero.brain_policy import BrainPolicyMixin


def test_zero_brain_composes_policy_media_generate_mixins():
    assert issubclass(ZeroBrain, BrainPolicyMixin)
    assert issubclass(ZeroBrain, BrainMediaMixin)
    assert issubclass(ZeroBrain, BrainGenerateMixin)


def test_public_helpers_still_exported_from_zero_brain():
    assert parse_search_command("/search hello") == ("web", "hello")
    cleaned, mood = sanitize_outgoing_text("سلام STICKER:funny")
    assert "STICKER" not in cleaned
    assert mood == "funny"
    assert user_requests_sticker("استیکر بفرست") is True
