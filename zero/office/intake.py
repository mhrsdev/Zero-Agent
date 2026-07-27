from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import shutil
import uuid

from zero.config import OfficeConfig
from .command_gate import CommandGateError, OfficeCommand, parse_office_command
from .db import OfficeRepository, QuotaExceeded
from .preflight import MIMES, PreflightError, inspect_ooxml
from .text import normalize_text, quota_date
from .workspace import WorkspaceError, create_workspace


USAGE = "برای ساخت فایل، فرمت را اول پیام مشخص کن:\n\n/docx برای Word\n/xlsx برای Excel\n/pptx برای PowerPoint\n\nمثال:\n/pptx یک ارائه ۱۰ اسلایدی درباره منظومه شمسی بساز."
_OFFICE_NATURAL = re.compile(r"(?i)(ورد|word|docx|اکسل|excel|xlsx|پاورپوینت|powerpoint|pptx|فایل|ارائه|گزارش)")
_FORMAT_LABEL = {"docx": "DOCX", "xlsx": "XLSX", "pptx": "PPTX"}


@dataclass(frozen=True, slots=True)
class LocalAttachment:
    path: Path
    filename: str
    declared_mime: str
    owner_user_id: int
    chat_id: int
    message_id: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OfficeRequest:
    text: str
    user_id: int
    chat_id: int
    message_id: int
    bot_username: str
    received_at: datetime
    is_group: bool = False
    trigger_valid: bool = True
    is_forwarded: bool = False
    account_scope: str = "telegram"
    installation_id: str = ""
    group_id: str = ""
    thread_id: int | None = None
    attachment: LocalAttachment | None = None
    reply_attachment: LocalAttachment | None = None
    reply_context_present: bool = False


@dataclass(frozen=True, slots=True)
class IntakeResult:
    handled: bool
    accepted: bool = False
    message: str = ""
    job_id: str = ""


class OfficeIntakeService:
    def __init__(self, config: OfficeConfig, repository: OfficeRepository, *, owner_user_id: int):
        self.config = config
        self.repository = repository
        self.owner_user_id = int(owner_user_id)

    def _rejected(self, message: str, metric: str = "office_jobs_rejected_total") -> IntakeResult:
        self.repository.increment_metric(metric)
        return IntakeResult(True, message=message)

    def _is_admin(self, user_id: int) -> bool:
        return user_id == self.owner_user_id or user_id in self.config.admin_user_ids or user_id in self.config.unlimited_admin_user_ids

    def _limits(self, user_id: int) -> tuple[int, int, bool]:
        admin = self._is_admin(user_id)
        unlimited = user_id in self.config.unlimited_admin_user_ids
        if admin:
            return self.config.admin_quota.jobs_per_day, self.config.admin_quota.max_characters_per_job, unlimited
        return self.config.quota.jobs_per_user_per_day, self.config.quota.max_characters_per_job, False

    def _command_or_response(self, request: OfficeRequest) -> tuple[OfficeCommand | None, IntakeResult | None]:
        try:
            return parse_office_command(request.text, bot_username=request.bot_username), None
        except CommandGateError as exc:
            code = str(exc)
            starts_office_command = bool(re.match(r"^\s*/(?:docx|xlsx|pptx|docm|xlsm|pptm|doc|xls|ppt)(?:\b|@|_)", request.text or "", re.I))
            if code == "missing_request":
                return None, IntakeResult(True, message="بعد از فرمان بنویس دقیقاً چه فایلی می‌خواهی یا روی فایل هم‌فرمت Reply کن و درخواستت را بنویس.")
            if code == "unsupported_format":
                return None, IntakeResult(True, message="این فرمت به‌دلایل امنیتی پشتیبانی نمی‌شود. فقط /docx، /xlsx و /pptx مجازند.")
            if starts_office_command:
                return None, IntakeResult(True, message="فرمان Office معتبر نیست. فقط /docx، /xlsx و /pptx را در ابتدای پیام بنویس.")
            attachment = request.attachment or request.reply_attachment
            if request.reply_context_present and request.attachment is None and request.reply_attachment is None:
                return None, IntakeResult(True, message="این Reply به پیام دارای فایل Office متصل نیست و سهمیه‌ای مصرف نشد.")
            if attachment:
                fmt = Path(attachment.filename).suffix.casefold().lstrip(".")
                command = f"/{fmt}" if fmt in {"docx", "xlsx", "pptx"} else "/docx"
                return None, IntakeResult(True, message=f"فایل دریافت شد، اما هنوز پردازشش نکردم.\n\nدر caption فایل یا با Reply روی آن بنویس:\n{command} درخواستت")
            if _OFFICE_NATURAL.search(request.text or ""):
                return None, IntakeResult(True, message=USAGE)
            return None, IntakeResult(False)

    def handle(self, request: OfficeRequest) -> IntakeResult:
        if not self.config.enabled:
            return IntakeResult(False)
        command, immediate = self._command_or_response(request)
        if immediate is not None:
            if immediate.handled and not immediate.accepted:
                self.repository.increment_metric("office_jobs_rejected_total")
            return immediate
        assert command is not None
        if request.reply_context_present and request.attachment is None and request.reply_attachment is None:
            return self._rejected("این Reply به پیام دارای فایل Office متصل نیست و سهمیه‌ای مصرف نشد.")
        if request.is_forwarded:
            return self._rejected("فرمان Office در پیام Forwardشده اجرا نمی‌شود.")
        if request.is_group and not request.trigger_valid:
            return self._rejected("برای اجرای فرمان، آن را مستقیم برای Zero بفرست یا روی فایل Reply کن.")
        duplicate = self.repository.get_by_message(request.account_scope, request.chat_id, request.message_id)
        if duplicate:
            return IntakeResult(True, True, "فایل دریافت شد و در صف پردازش قرار گرفت.", str(duplicate["id"]))

        attachment = request.attachment or request.reply_attachment
        report = None
        if request.reply_attachment is not None:
            if request.reply_attachment.owner_user_id != request.user_id:
                return IntakeResult(True, message="این فایل متعلق به کاربر دیگری است و قابل پردازش نیست.")
            if request.reply_attachment.chat_id != request.chat_id:
                return IntakeResult(True, message="این فایل مربوط به گفت‌وگوی دیگری است و قابل پردازش نیست.")
            age = (request.received_at - request.reply_attachment.created_at).total_seconds()
            if age < 0 or age > self.config.pending_attachment_ttl_minutes * 60:
                return IntakeResult(True, message="مهلت این فایل منقضی شده است؛ فایل را دوباره ارسال کن.")
        if attachment is not None:
            if attachment.owner_user_id != request.user_id or attachment.chat_id != request.chat_id:
                return IntakeResult(True, message="دسترسی به فایل کاربر یا گفت‌وگوی دیگری مسدود شد.")
            try:
                report = inspect_ooxml(attachment.path, declared_mime=attachment.declared_mime, limits=self.config.limits)
            except PreflightError as exc:
                code = str(exc)
                if code in {"unsupported_format", "format_mismatch", "magic_bytes", "missing_content_types"}:
                    return IntakeResult(True, message="فعلاً فقط فایل‌های DOCX، XLSX و PPTX معتبر پشتیبانی می‌شوند.")
                return IntakeResult(True, message="این فایل در بررسی امنیتی یا ساختاری پذیرفته نشد و سهمیه‌ای مصرف نشد.")
            if report.format != command.format:
                return self._rejected(f"فرمان با نوع فایل هماهنگ نیست. این فایل {_FORMAT_LABEL[report.format]} است؛ از /{report.format} استفاده کن.")

        jobs_limit, character_limit, unlimited = self._limits(request.user_id)
        normalized = report.normalized_text if report else normalize_text(command.request)
        character_count = len(normalized)
        if character_count > character_limit:
            self.repository.increment_metric("office_character_limit_rejections_total")
            digits = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
            count = f"{character_count:,}".replace(",", "٬").translate(digits)
            limit = f"{character_limit:,}".replace(",", "٬").translate(digits)
            return IntakeResult(True, message=f"محتوای این فایل حدود {count} کاراکتر است، اما سقف هر فایل {limit} کاراکتر است. بخش کوچک‌تری بفرست.")

        job_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{request.account_scope}:{request.chat_id}:{request.message_id}").hex
        workspace = None
        try:
            workspace = create_workspace(self.config.workspace_root, chat_id=request.chat_id, job_id=job_id)
            input_path = ""
            if attachment is not None:
                installed = workspace.install_input(attachment.path, f"input.{command.format}")
                input_path = str(installed)
            initial_operation = {
                ("docx", False): "create_document", ("docx", True): "edit_document",
                ("xlsx", False): "create_spreadsheet", ("xlsx", True): "edit_spreadsheet",
                ("pptx", False): "create_presentation", ("pptx", True): "edit_presentation",
            }[(command.format, attachment is not None)]
            job = self.repository.reserve_and_create(
                job_id=job_id, trace_id=uuid.uuid4().hex[:16], account_scope=request.account_scope,
                user_id=request.user_id, chat_id=request.chat_id, message_id=request.message_id,
                operation_type=initial_operation, office_format=command.format, request_text=command.request,
                input_filename=attachment.filename if attachment else "", input_path=input_path,
                detected_mime=report.detected_mime if report else "", input_size_bytes=report.input_size_bytes if report else 0,
                uncompressed_size_bytes=report.uncompressed_size_bytes if report else 0,
                extracted_characters=character_count, quota_date=quota_date(request.received_at, self.config.quota.timezone),
                jobs_limit=jobs_limit, character_limit=character_limit, unlimited=unlimited,
                installation_id=request.installation_id, group_id=request.group_id,
                thread_id=request.thread_id,
            )
            return IntakeResult(True, True, "فایل دریافت شد و در صف پردازش قرار گرفت.", str(job["id"]))
        except QuotaExceeded as exc:
            if workspace:
                shutil.rmtree(workspace.root, ignore_errors=True)
            if str(exc) == "daily_jobs":
                self.repository.increment_metric("office_quota_rejections_total")
                return IntakeResult(True, message="سهمیه پردازش فایل امروزت استفاده شده است. سهمیه بعدی در ساعت ۰۰:۰۰ به وقت تهران فعال می‌شود.")
            return IntakeResult(True, message="محتوای فایل از سقف مجاز بیشتر است و سهمیه‌ای مصرف نشد.")
        except (OSError, WorkspaceError):
            if workspace:
                shutil.rmtree(workspace.root, ignore_errors=True)
            return IntakeResult(True, message="پردازش فایل به‌دلیل یک خطای داخلی شروع نشد و سهمیه‌ات مصرف نشد.")
