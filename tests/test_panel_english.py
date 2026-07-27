"""Test: Panel API contains no Persian/placeholder text.

All user-facing error messages, API responses, and panel strings must be
in English. The bot persona (persona.py) is excluded — it's intentionally
Persian for the bot's personality.
"""
from __future__ import annotations

import re
import inspect

import pytest

# Persian/Arabic character range + ZWNJ
PERSIAN_RE = re.compile(r'[\u0600-\u06FF\u200C\u200D]')


def test_panel_api_no_persian_text():
    from zero import panel_api
    source = inspect.getsource(panel_api)
    # Find any Persian in string literals
    lines = source.split('\n')
    persian_lines = [(i + 1, line.strip()) for i, line in enumerate(lines)
                     if PERSIAN_RE.search(line) and 'import' not in line]
    assert not persian_lines, \
        f"Panel API has Persian text: {persian_lines}"


def test_panel_api_error_messages_are_english():
    from zero import panel_api
    source = inspect.getsource(panel_api)
    # All _json_error and HTTPForbidden texts should be English
    # Extract quoted strings from error/reject lines
    error_lines = [line for line in source.split('\n')
                   if '_json_error' in line or 'HTTPForbidden' in line]
    for line in error_lines:
        if PERSIAN_RE.search(line):
            pytest.fail(f"Persian text in error response: {line.strip()}")


def test_router_fallback_message_is_english():
    from zero import router
    source = inspect.getsource(router)
    # The fallback "unavailable" message must be English
    lines = source.split('\n')
    persian_lines = [(i + 1, line.strip()) for i, line in enumerate(lines)
                     if PERSIAN_RE.search(line) and 'import' not in line]
    assert not persian_lines, \
        f"Router has Persian text: {persian_lines}"
