from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from zero.config import OfficeConfig
from zero.office.command_gate import CommandGateError, parse_office_command
from zero.office.text import normalize_text, quota_date


@pytest.mark.parametrize(
    ("text", "fmt", "expected_request"),
    [
        ("/docx گزارش فارسی بساز", "docx", "گزارش فارسی بساز"),
        ("/XLSX برنامه هفتگی بساز", "xlsx", "برنامه هفتگی بساز"),
        ("   /pptx ارائه بساز", "pptx", "ارائه بساز"),
        ("/docx@ZeroBot متن را اصلاح کن", "docx", "متن را اصلاح کن"),
    ],
)
def test_explicit_office_commands_are_parsed(text, fmt, expected_request):
    parsed = parse_office_command(text, bot_username="ZeroBot")
    assert parsed.format == fmt
    assert parsed.request == expected_request


@pytest.mark.parametrize(
    "text",
    [
        "/docx_fake درخواست",
        "/docxx درخواست",
        "متن /docx درخواست",
        "```\n/docx درخواست\n```",
        "> /docx درخواست",
        '"/docx درخواست"',
        "/docx@OtherBot درخواست",
    ],
)
def test_non_executable_command_locations_and_spoofs_are_rejected(text):
    with pytest.raises(CommandGateError):
        parse_office_command(text, bot_username="ZeroBot")


@pytest.mark.parametrize("text", ["/docx", "/xlsx   ", "/pptx@ZeroBot\n"])
def test_command_requires_nonempty_request(text):
    with pytest.raises(CommandGateError, match="missing_request"):
        parse_office_command(text, bot_username="ZeroBot")


@pytest.mark.parametrize("text", ["/docm x", "/xlsm x", "/pptm x", "/doc x", "/xls x", "/ppt x"])
def test_unsafe_and_legacy_commands_are_explicitly_unsupported(text):
    with pytest.raises(CommandGateError, match="unsupported_format"):
        parse_office_command(text, bot_username="ZeroBot")


def test_normalize_text_preserves_persian_and_collapses_controls_and_whitespace():
    value = "  سلام\u200c دنیا\n\tخوبی؟\x00  بله  "
    assert normalize_text(value) == "سلام\u200c دنیا خوبی؟ بله"
    assert len(normalize_text("فارسی")) == 5


def test_quota_date_uses_configured_timezone_boundary():
    instant = datetime(2026, 7, 18, 20, 45, tzinfo=timezone.utc)
    assert quota_date(instant, "Asia/Tehran") == "2026-07-19"
    assert quota_date(instant, "UTC") == "2026-07-18"


def test_office_config_defaults_are_fail_closed_and_match_policy():
    cfg = OfficeConfig()
    assert cfg.enabled is False
    assert cfg.quota.jobs_per_user_per_day == 1
    assert cfg.quota.max_characters_per_job == 40_000
    assert cfg.admin_quota.jobs_per_day == 20
    assert cfg.admin_quota.max_characters_per_job == 150_000
    assert cfg.concurrency.global_jobs == 1
    assert cfg.pending_attachment_ttl_minutes == 30


def test_office_config_environment_overrides(monkeypatch):
    monkeypatch.setenv("ZERO_OFFICE_ENABLED", "true")
    monkeypatch.setenv("ZERO_OFFICE_USER_JOBS_PER_DAY", "3")
    monkeypatch.setenv("ZERO_OFFICE_TIMEZONE", "UTC")
    monkeypatch.setenv("ZERO_OFFICE_UNLIMITED_ADMIN_IDS", "12,34")
    monkeypatch.setenv("ZERO_OFFICE_ROLLOUT_REQUIRED", "true")
    monkeypatch.setenv("ZERO_OFFICE_ROLLOUT_USER_IDS", "56,78")
    monkeypatch.setenv("ZERO_OFFICE_ROLLOUT_CHAT_IDS", "-1001")
    cfg = OfficeConfig.from_env()
    assert cfg.enabled is True
    assert cfg.quota.jobs_per_user_per_day == 3
    assert cfg.quota.timezone == "UTC"
    assert cfg.unlimited_admin_user_ids == [12, 34]
    assert cfg.rollout_required is True
    assert cfg.rollout_user_ids == [56, 78]
    assert cfg.rollout_chat_ids == [-1001]


@pytest.mark.parametrize(
    ("env", "value"),
    [
        ("ZERO_OFFICE_USER_JOBS_PER_DAY", "0"),
        ("ZERO_OFFICE_USER_MAX_CHARACTERS", "-1"),
        ("ZERO_OFFICE_MAX_RUNTIME_SECONDS", "0"),
        ("ZERO_OFFICE_GLOBAL_CONCURRENCY", "0"),
        ("ZERO_OFFICE_TIMEZONE", "Mars/Olympus"),
    ],
)
def test_invalid_office_config_is_rejected_at_startup(monkeypatch, env, value):
    monkeypatch.setenv(env, value)
    with pytest.raises((ValidationError, ValueError)):
        OfficeConfig.from_env()


def test_example_office_config_uses_model_field_names():
    from zero.config import ZeroConfig

    config = ZeroConfig.load("config/zero.example.yaml")
    assert config.office.quota.timezone == "Asia/Tehran"
    assert config.office.quota.jobs_per_user_per_day == 1
    assert config.office.limits.max_zip_entries == 5000
