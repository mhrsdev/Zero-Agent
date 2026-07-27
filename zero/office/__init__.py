"""Secure, explicit-command Office job subsystem."""

from .command_gate import OfficeCommand, CommandGateError, parse_office_command

__all__ = ["OfficeCommand", "CommandGateError", "parse_office_command"]