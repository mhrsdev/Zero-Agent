from __future__ import annotations

from dataclasses import dataclass
import re


_SUPPORTED = {"docx", "xlsx", "pptx"}
_UNSUPPORTED = {"docm", "xlsm", "pptm", "doc", "xls", "ppt"}
_CANDIDATE = re.compile(r"^\s*/([A-Za-z0-9_]+)(?:@([A-Za-z0-9_]{4,}))?(?:\s+([\s\S]*))?$")


class CommandGateError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OfficeCommand:
    format: str
    request: str
    addressed_username: str = ""


def parse_office_command(text: str, *, bot_username: str = "") -> OfficeCommand:
    value = text or ""
    stripped = value.lstrip()
    if stripped.startswith(("```", "`", ">", '"', "“", "«")):
        raise CommandGateError("non_executable_context")
    match = _CANDIDATE.fullmatch(value)
    if not match:
        raise CommandGateError("not_explicit_command")
    command = match.group(1).casefold()
    addressed = (match.group(2) or "").casefold()
    if command in _UNSUPPORTED:
        raise CommandGateError("unsupported_format")
    if command not in _SUPPORTED:
        raise CommandGateError("unknown_command")
    expected = (bot_username or "").lstrip("@").casefold()
    if addressed and (not expected or addressed != expected):
        raise CommandGateError("wrong_bot_username")
    request = (match.group(3) or "").strip()
    if not request:
        raise CommandGateError("missing_request")
    return OfficeCommand(format=command, request=request, addressed_username=addressed)