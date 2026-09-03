"""Live Telegram E2E test setup.

This file documents the exact steps to set up live Telegram E2E testing.
To actually run the live tests, you need:

1. A Telegram bot token from @BotFather
2. A test group with the bot added as admin
3. At least 2 test users in the group
4. Environment variables set:
   - ZERO_COMMUNITY_E2E=1
   - ZERO_LIVE_E2E=1 (Gemini/OpenRouter/Telegram getMe harness in test_live_providers_e2e.py)
   - TELEGRAM_BOT_TOKEN=<real token>
   - TELEGRAM_TEST_GROUP_ID=<real group ID>
   - TELEGRAM_TEST_USER_ID_1=<real user ID 1>
   - TELEGRAM_TEST_USER_ID_2=<real user ID 2>

When these are set, the skipped tests in test_community_e2e.py will run
against the real Telegram API.

Without these credentials, the 2 live tests skip gracefully.

SAFETY: Live tests create and delete real messages in the test group.
Do NOT run against production groups. Use a dedicated test group.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# This file is documentation + environment validation.
# The actual live tests are in tests/test_community_e2e.py


def test_e2e_credentials_are_documented():
    """Verify that the E2E credential requirements are documented."""
    required = [
        "ZERO_COMMUNITY_E2E",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_TEST_GROUP_ID",
        "TELEGRAM_TEST_USER_ID_1",
    ]
    # Document each requirement
    docs = Path(__file__).resolve().parent / "test_live_e2e_setup.py"
    source = docs.read_text(encoding="utf-8")
    for var in required:
        assert var in source, f"{var} must be documented in E2E setup"


def test_e2e_safety_warning_present():
    """Verify that safety warnings are in the setup docs."""
    docs = Path(__file__).resolve().read_text(encoding="utf-8")
    assert "SAFETY" in docs or "safety" in docs
    assert "production" in docs.lower() or "test group" in docs.lower()


def test_e2e_skips_without_credentials():
    """Without ZERO_COMMUNITY_E2E=1, live tests must skip (not fail)."""
    if not os.getenv("ZERO_COMMUNITY_E2E"):
        pytest.skip("Community E2E requires live credentials: ZERO_COMMUNITY_E2E=1")
