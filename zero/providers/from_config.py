"""Translate legacy YAML provider keys into ProviderRegistry profiles.

Groups select a profile by name. Credentials never enter a profile; they are
resolved at composition through a symbolic secret_ref.
"""
from __future__ import annotations

import logging
from typing import Any

from .base import OPENAI_COMPATIBLE_PRESETS, ProviderKind, ProviderProfile
from .registry import ProviderRegistry
from .transport import async_post_json

logger = logging.getLogger("zero.providers")


def registry_from_runtime_config(config: Any, *, http_post: Any = None) -> ProviderRegistry | None:
    """Build a registry from ZeroConfig.router.providers. Returns None if unused."""
    router = getattr(config, "router", None)
    providers = getattr(router, "providers", None)
    if providers is None:
        return None
    secrets: dict[str, str] = {}
    gemini = getattr(providers, "gemini", None)
    openrouter = getattr(providers, "openrouter", None)
    local = getattr(providers, "local_openai", None)
    if gemini is not None:
        keys = [k for k in (getattr(gemini, "keys", None) or []) if k]
        if keys:
            secrets["provider.gemini"] = keys[0]
    if openrouter is not None:
        keys = [k for k in (getattr(openrouter, "keys", None) or []) if k]
        if keys:
            secrets["provider.openrouter"] = keys[0]
    if local is not None:
        keys = [k for k in (getattr(local, "keys", None) or []) if k]
        if keys:
            secrets["provider.local_openai"] = keys[0]
    local_enabled = bool(local is not None and getattr(local, "enabled", False))
    if not secrets and not local_enabled:
        return None

    def resolve(ref: str) -> str:
        return secrets.get(ref, "")

    registry = ProviderRegistry(http_post or async_post_json, secret_resolver=resolve)
    timeout = float(getattr(router, "request_timeout_seconds", 45) or 45)
    retries = max(0, int(getattr(router, "max_provider_retries", 1) or 0))
    if "provider.gemini" in secrets and gemini is not None and getattr(gemini, "enabled", True):
        registry.register(
            ProviderProfile(
                name="gemini",
                kind=ProviderKind.GEMINI,
                model=str(getattr(gemini, "model", "") or "gemini-flash"),
                secret_ref="provider.gemini",
                timeout_seconds=timeout,
                max_retries=retries,
                requests_per_minute=getattr(gemini, "rpm", None),
            )
        )
    if "provider.openrouter" in secrets and openrouter is not None and getattr(openrouter, "enabled", True):
        registry.register(
            ProviderProfile(
                name="openrouter",
                kind=ProviderKind.OPENAI_COMPATIBLE,
                model=str(getattr(openrouter, "model", "") or "openrouter/auto"),
                base_url=OPENAI_COMPATIBLE_PRESETS["openrouter"],
                secret_ref="provider.openrouter",
                timeout_seconds=timeout,
                max_retries=retries,
                requests_per_minute=getattr(openrouter, "rpm", None),
            )
        )
    if local_enabled:
        base_url = str(getattr(local, "base_url", "") or "") or OPENAI_COMPATIBLE_PRESETS["ollama"]
        registry.register(
            ProviderProfile(
                name="local_openai",
                kind=ProviderKind.OPENAI_COMPATIBLE,
                model=str(getattr(local, "model", "") or "llama3"),
                base_url=base_url,
                secret_ref="provider.local_openai" if "provider.local_openai" in secrets else None,
                timeout_seconds=timeout,
                max_retries=retries,
                requests_per_minute=getattr(local, "rpm", None),
            )
        )
    if not registry.names():
        return None
    logger.info("PROVIDER_REGISTRY_READY profiles=%s", ",".join(registry.names()))
    return registry
