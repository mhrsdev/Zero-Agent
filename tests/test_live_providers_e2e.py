"""Gated live Telegram / Gemini / OpenRouter E2E.

This harness never talks to the network unless:

- ZERO_LIVE_E2E=1
- and the relevant key/token is present

Without those, every test skips. Do not point this at a production group.
"""
from __future__ import annotations

import asyncio
import os

import pytest

LIVE = os.getenv("ZERO_LIVE_E2E") == "1"


def _skip_unless(*names: str) -> None:
    if not LIVE:
        pytest.skip("set ZERO_LIVE_E2E=1 and provider keys to run live E2E")
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        pytest.skip("missing live credentials: " + ",".join(missing))


def test_live_gemini_complete():
    _skip_unless("GEMINI_API_KEY")
    from zero.providers.base import CompletionRequest, ProviderKind, ProviderProfile
    from zero.providers.implementations import GeminiProvider
    from zero.providers.transport import async_post_json

    profile = ProviderProfile(name="gemini", kind=ProviderKind.GEMINI, model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"))
    provider = GeminiProvider(profile, async_post_json, api_key=os.environ["GEMINI_API_KEY"])

    async def run():
        return await provider.complete(CompletionRequest(prompt="Reply with the word pong only.", max_output_tokens=8))

    result = asyncio.run(run())
    assert result.text.strip()


def test_live_openrouter_complete():
    _skip_unless("OPENROUTER_API_KEY")
    from zero.providers.base import CompletionRequest, OPENAI_COMPATIBLE_PRESETS, ProviderKind, ProviderProfile
    from zero.providers.implementations import OpenAICompatibleProvider
    from zero.providers.transport import async_post_json

    profile = ProviderProfile(
        name="openrouter",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        model=os.getenv("OPENROUTER_MODEL", "openrouter/auto"),
        base_url=OPENAI_COMPATIBLE_PRESETS["openrouter"],
    )
    provider = OpenAICompatibleProvider(profile, async_post_json, api_key=os.environ["OPENROUTER_API_KEY"])

    async def run():
        return await provider.complete(CompletionRequest(prompt="Reply with the word pong only.", max_output_tokens=8))

    result = asyncio.run(run())
    assert result.text.strip()


def test_live_telegram_get_me():
    _skip_unless("TELEGRAM_BOT_TOKEN")
    import json
    import urllib.request

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = f"https://api.telegram.org/bot{token}/getMe"
    with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310 - operator-gated live test
        payload = json.loads(response.read().decode("utf-8"))
    assert payload.get("ok") is True
