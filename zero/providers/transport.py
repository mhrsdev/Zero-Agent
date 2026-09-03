"""Stdlib HTTP transport for provider calls. Secrets stay in headers, never logs."""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any


def post_json_text(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> str:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers or {"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    return json.loads(post_json_text(url, payload, headers, timeout) or "{}")


async def async_post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
    text = await asyncio.to_thread(post_json_text, url, payload, headers, timeout)
    return json.loads(text) if text else {}
