"""Shared public contract for the Zero terminal user interface."""

from __future__ import annotations

# Keep all CLI entry points and renderers on the same selectable panel set.
PANEL_NAMES: tuple[str, ...] = (
    "status",
    "doctor",
    "groups",
    "backup",
    "logs",
    "setup",
    "chat",
    "sessions",
)
