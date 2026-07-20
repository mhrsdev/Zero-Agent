from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import zipfile

import pytest

from zero.config import OfficeConfig
from zero.office.db import OfficeRepository
from zero.office.intake import LocalAttachment, OfficeIntakeService, OfficeRequest


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def make_docx(path: Path, text="سلام"):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("word/document.xml", f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')


def cfg(tmp_path, *, enabled=True):
    return OfficeConfig(enabled=enabled, workspace_root=str(tmp_path / "jobs"), cli_path="/usr/local/lib/zero-office/officecli")


def request(text, *, attachment=None, reply_attachment=None, reply_context_present=False, user_id=10, chat_id=-100, message_id=50, trigger=True, forwarded=False):
    return OfficeRequest(
        text=text, user_id=user_id, chat_id=chat_id, message_id=message_id,
        bot_username="ZeroBot", is_group=True, trigger_valid=trigger, is_forwarded=forwarded,
        attachment=attachment, reply_attachment=reply_attachment, reply_context_present=reply_context_present,
        received_at=datetime(2026, 7, 18, 20, 45, tzinfo=timezone.utc),
    )


def attachment(path, *, fmt="docx", owner=10, chat=-100, message=40, age_minutes=1):
    mime = {"docx": DOCX_MIME, "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}[fmt]
    return LocalAttachment(
        path=Path(path), filename=f"input.{fmt}", declared_mime=mime,
        owner_user_id=owner, chat_id=chat, message_id=message,
        created_at=datetime(2026, 7, 18, 20, 45, tzinfo=timezone.utc) - timedelta(minutes=age_minutes),
    )


def test_disabled_feature_does_not_handle_create_or_consume_quota(tmp_path):
    repo = OfficeRepository(tmp_path / "db.sqlite")
    result = OfficeIntakeService(cfg(tmp_path, enabled=False), repo, owner_user_id=1).handle(request("/docx گزارش بساز"))
    assert result.handled is False
    assert repo.list_jobs({"quota_reserved"}) == []


@pytest.mark.parametrize("text", ["یه گزارش برام درست کن", "یه پاورپوینت می‌خوام", "این اکسل رو تحلیل کن"])
def test_natural_language_without_explicit_command_only_returns_usage(tmp_path, text):
    repo = OfficeRepository(tmp_path / "db.sqlite")
    result = OfficeIntakeService(cfg(tmp_path), repo, owner_user_id=1).handle(request(text))
    assert result.handled and not result.accepted
    assert "/docx" in result.message
    assert repo.quota_usage(10, "2026-07-19")["jobs_reserved"] == 0


def test_attachment_without_command_is_not_processed_or_reserved(tmp_path):
    path = tmp_path / "in.docx"; make_docx(path)
    repo = OfficeRepository(tmp_path / "db.sqlite")
    result = OfficeIntakeService(cfg(tmp_path), repo, owner_user_id=1).handle(request("", attachment=attachment(path)))
    assert result.handled and not result.accepted
    assert "هنوز پردازشش نکردم" in result.message
    assert repo.list_jobs({"quota_reserved"}) == []


def test_valid_create_command_reserves_and_creates_persistent_job(tmp_path):
    repo = OfficeRepository(tmp_path / "db.sqlite")
    result = OfficeIntakeService(cfg(tmp_path), repo, owner_user_id=1).handle(request("/docx گزارش فارسی بساز"))
    assert result.accepted and result.job_id
    job = repo.get_job(result.job_id)
    assert job["status"] == "quota_reserved"
    assert job["office_format"] == "docx"
    assert repo.quota_usage(10, "2026-07-19")["jobs_reserved"] == 1


def test_valid_command_reply_to_non_attachment_is_not_treated_as_create(tmp_path):
    repo = OfficeRepository(tmp_path / "db.sqlite")
    result = OfficeIntakeService(cfg(tmp_path), repo, owner_user_id=1).handle(
        request("/docx گزارش بساز", reply_context_present=True)
    )
    assert result.accepted is False
    assert "Reply" in result.message
    assert repo.list_jobs({"quota_reserved"}) == []


def test_valid_reply_to_owned_matching_attachment_is_accepted(tmp_path):
    path = tmp_path / "in.docx"; make_docx(path, "متن فارسی")
    repo = OfficeRepository(tmp_path / "db.sqlite")
    result = OfficeIntakeService(cfg(tmp_path), repo, owner_user_id=1).handle(
        request("/docx متن را اصلاح کن", reply_attachment=attachment(path))
    )
    assert result.accepted
    job = repo.get_job(result.job_id)
    assert job["input_path"].endswith("input.docx")
    assert Path(job["input_path"]).stat().st_mode & 0o222 == 0


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        ({"owner": 11}, "متعلق به کاربر دیگری"),
        ({"chat": -200}, "گفت‌وگوی دیگری"),
        ({"age_minutes": 31}, "منقضی"),
    ],
)
def test_cross_user_cross_chat_and_expired_reply_are_blocked_without_quota(tmp_path, reply, message):
    path = tmp_path / "in.docx"; make_docx(path)
    repo = OfficeRepository(tmp_path / "db.sqlite")
    att = attachment(path, **reply)
    result = OfficeIntakeService(cfg(tmp_path), repo, owner_user_id=1).handle(request("/docx اصلاح کن", reply_attachment=att))
    assert not result.accepted and message in result.message
    assert repo.quota_usage(10, "2026-07-19")["jobs_reserved"] == 0


def test_mismatched_command_and_real_file_type_is_rejected_without_quota(tmp_path):
    path = tmp_path / "in.docx"; make_docx(path)
    repo = OfficeRepository(tmp_path / "db.sqlite")
    result = OfficeIntakeService(cfg(tmp_path), repo, owner_user_id=1).handle(request("/xlsx تحلیل کن", reply_attachment=attachment(path)))
    assert not result.accepted and "هماهنگ نیست" in result.message
    assert repo.quota_usage(10, "2026-07-19")["jobs_reserved"] == 0


def test_invalid_group_trigger_and_forwarded_command_are_rejected(tmp_path):
    for index, req in enumerate((request("/docx بساز", trigger=False, message_id=60), request("/docx بساز", forwarded=True, message_id=61))):
        repo = OfficeRepository(tmp_path / f"db{index}.sqlite")
        result = OfficeIntakeService(cfg(tmp_path), repo, owner_user_id=1).handle(req)
        assert not result.accepted
        assert repo.quota_usage(10, "2026-07-19")["jobs_reserved"] == 0


def test_user_character_limit_and_admin_separate_limit(tmp_path):
    path = tmp_path / "large.docx"; make_docx(path, "ا" * 40_001)
    repo = OfficeRepository(tmp_path / "db.sqlite")
    service = OfficeIntakeService(cfg(tmp_path), repo, owner_user_id=1)
    user = service.handle(request("/docx خلاصه کن", reply_attachment=attachment(path)))
    assert not user.accepted and "۴۰۰۰۰" in user.message.replace("٬", "")
    admin = service.handle(request("/docx خلاصه کن", reply_attachment=attachment(path, owner=1), user_id=1, message_id=51))
    assert admin.accepted


def test_duplicate_submit_returns_same_job_without_double_quota(tmp_path):
    repo = OfficeRepository(tmp_path / "db.sqlite")
    service = OfficeIntakeService(cfg(tmp_path), repo, owner_user_id=1)
    first = service.handle(request("/docx گزارش بساز"))
    second = service.handle(request("/docx گزارش بساز"))
    assert first.job_id == second.job_id
    assert repo.quota_usage(10, "2026-07-19")["jobs_reserved"] == 1
