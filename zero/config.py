from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .fsprivacy import ensure_private_path
from .paths import zero_home_path


_SECRET_ENV = "ZERO_SECRET_FILE"


def _private_file(path: Path, label: str) -> None:
    """Reject secret files that other accounts can read (POSIX bits / NTFS ACL)."""
    ensure_private_path(path, label)


def _placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().upper()
    return not normalized or normalized.startswith(("__", "YOUR_", "CHANGE_ME", "REPLACE_", "${"))


def _apply_secret_file(content: dict[str, Any], path: Path) -> None:
    sensitive = (
        content.get("listener", {}).get("telegram_api_hash"),
        content.get("telegram_search", {}).get("api_hash"),
        *(content.get("router", {}).get("providers", {}).get(name, {}).get("keys", []) for name in ("gemini", "openrouter")),
    )
    has_inline = any((isinstance(value, list) and any(not _placeholder(item) for item in value)) or (isinstance(value, str) and not _placeholder(value)) for value in sensitive)
    requested = os.environ.get(_SECRET_ENV)
    if not path.exists():
        if requested or has_inline:
            raise FileNotFoundError(f"protected secret file is required: {path}")
        return
    _private_file(path, "secret file")
    secret = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    listener_secret = secret.get("listener", {}).get("telegram_api_hash")
    search_secret = secret.get("telegram_search", {}).get("api_hash")
    providers = secret.get("router", {}).get("providers", {})
    if listener_secret:
        content.setdefault("listener", {})["telegram_api_hash"] = listener_secret
    if search_secret:
        content.setdefault("telegram_search", {})["api_hash"] = search_secret
    for provider in ("gemini", "openrouter"):
        keys = providers.get(provider, {}).get("keys")
        if keys is not None:
            content.setdefault("router", {}).setdefault("providers", {}).setdefault(provider, {})["keys"] = keys


def _validate_secret_values(content: dict[str, Any]) -> None:
    sensitive = (
        content.get("listener", {}).get("telegram_api_hash"),
        content.get("telegram_search", {}).get("api_hash"),
        *(content.get("router", {}).get("providers", {}).get(name, {}).get("keys", []) for name in ("gemini", "openrouter")),
    )
    if any((isinstance(value, list) and any(_placeholder(item) for item in value)) or (isinstance(value, str) and value in {"__FROM_SECRET__", "__REDACTED__"}) for value in sensitive):
        raise ValueError("credential placeholder was not resolved from protected secret file")


class ManagementBotConfig(BaseModel):
    token_file: str
    deny_message: str = "دسترسی نداری."


class ListenerConfig(BaseModel):
    telegram_api_id: int
    telegram_api_hash: str
    session_path: str
    account_username: str = ""
    allowed_group_ids: list[int] = Field(default_factory=list)
    allowed_group_usernames: list[str] = Field(default_factory=list)
    allowed_group_titles: list[str] = Field(default_factory=list)
    check_for_reply_to_self: bool = True


class PersonaConfig(BaseModel):
    name_en: str = "Zero"
    name_fa: str = "زیرو"
    default_mode: str = "normal"
    available_modes: list[str] = Field(default_factory=lambda: ["normal", "funny", "sarcastic", "serious", "assistant", "teacher", "debate"])
    trigger_words: list[str] = Field(default_factory=lambda: ["zero", "Zero", "زیرو", "صفر"])
    allow_random_interject: bool = True
    interject_probability: float = 0.12
    idle_starter_probability: float = 0.35
    min_interject_gap_seconds: int = 1500
    min_starter_gap_seconds: int = 7200
    idle_after_seconds: int = 2700


class PolicyConfig(BaseModel):
    max_reply_sentences: int = 4
    max_reply_chars: int = 900
    spam_cooldown_seconds: int = 90
    user_window_seconds: int = 1800
    user_max_replies_per_window: int = 8
    user_max_replies_per_day: int = 40
    bot_reply_cooldown_seconds: int = 900
    bot_max_chain_turns: int = 4
    bot_msg_limit: int = 5  # max messages to a bot per window
    bot_msg_window_seconds: int = 900  # 15 minutes
    anti_abuse_enabled: bool = True
    anti_spam_enabled: bool = True
    # Nova (bot-to-bot) conversation limits
    nova_window_seconds: int = 900  # 15 minutes
    nova_max_messages_per_window: int = 10  # max 10 messages in 15 min


class ProviderConfig(BaseModel):
    enabled: bool = True
    model: str
    fallback_models: list[str] = Field(default_factory=list)
    keys: list[str] = Field(default_factory=list)
    quota_scope: str = "unknown"
    weight: int = 1
    rpm: int | None = None
    tpm: int | None = None
    rpd: int | None = None


class RouterProvidersConfig(BaseModel):
    gemini: ProviderConfig
    openrouter: ProviderConfig


class RouterConfig(BaseModel):
    request_timeout_seconds: int = 45
    retry_attempts: int = 1
    simple_message_char_threshold: int = 140
    daily_budget_soft_limit_usd: float = 5.0
    normal_primary: str = "openrouter"
    normal_fallback: str = "gemini"
    search_provider: str = "gemini"
    strategy: str = "weighted_lru"
    max_provider_retries: int = 1
    max_total_attempts: int = 3
    providers: RouterProvidersConfig


class MemoryConfig(BaseModel):
    db_path: str
    recent_messages_limit: int = 5000
    memory_items_limit: int = 200
    long_term_limit: int = 120
    summary_trigger_messages: int = 120
    per_user_profile_limit: int = 250


class ReportingConfig(BaseModel):
    daily_report_hour_local: int = 0
    send_daily_report_to_owner: bool = True


class WebConfig(BaseModel):
    enabled: bool = False
    google_grounding_enabled: bool = True
    tavily_enabled: bool = False
    searxng_base_url: str = "http://127.0.0.1:8888"
    wigolo_enabled: bool = False
    wigolo_base_url: str = "http://127.0.0.1:3333"
    max_search_results: int = 6
    max_fetch_pages_per_query: int = 2
    request_timeout_seconds: int = 12
    provider_retries: int = 1
    cache_ttl_seconds: int = 1800
    context_max_chars: int = 2500


class TelegramSearchConfig(BaseModel):
    enabled: bool = False
    archived: bool = True
    api_id: int = 0
    api_hash: str = ""
    session_path: str = ""
    allowed_chat_usernames: list[str] = Field(default_factory=list)
    max_results_per_query: int = 10
    cache_ttl_seconds: int = 900
    max_joined_dialogs_per_run: int = 50
    max_messages_per_dialog: int = 5
    max_global_pages: int = 2
    max_inspected_channels: int = 5
    max_runtime_seconds: int = 20
    daily_global_search_limit: int = 50
    daily_joined_scan_limit: int = 100
    daily_inspect_limit: int = 100


class VisionConfig(BaseModel):
    enabled: bool = True
    model: str = "gemini-3.5-flash-lite"
    max_images_per_user_per_window: int = 8
    max_gifs_per_user_per_window: int = 4
    window_seconds: int = 900
    cooldown_seconds: int = 30
    max_file_size_mb: int = 10
    allowed_extensions: list[str] = Field(default_factory=lambda: [".jpg", ".jpeg", ".png", ".webp", ".gif"])


class StickersConfig(BaseModel):
    enabled: bool = True
    auto_save_enabled: bool = True
    send_chance: float = 0.10
    chance_percent: int = 10
    limit_per_hour: int = 2
    direct_limit_per_hour: int = 12
    cooldown_seconds: int = 600
    direct_cooldown_seconds: int = 2
    min_messages_between: int = 5
    min_relevance_score: float = 0.70
    generic_min_relevance_score: float = 0.50
    max_spam_score: float = 0.50
    max_candidate_failures: int = 3
    repeat_window: int = 20
    auto_enabled: bool = True
    vision_enabled: bool = True


class GifsConfig(BaseModel):
    enabled: bool = True
    auto_enabled: bool = False
    send_chance: float = 0.05
    limit_per_hour: int = 2
    direct_limit_per_hour: int = 12
    cooldown_seconds: int = 600
    direct_cooldown_seconds: int = 2
    min_messages_between: int = 5
    repeat_window: int = 20
    min_relevance_score: float = 0.70
    max_spam_score: float = 0.50


class ReactionsConfig(BaseModel):
    enabled: bool = False
    chance_percent: int = 45
    max_per_hour: int = 6
    user_cooldown_seconds: int = 300
    global_cooldown_seconds: int = 120
    read_enabled: bool = True


class OfficeQuotaConfig(BaseModel):
    jobs_per_user_per_day: int = Field(default=1, ge=1)
    max_characters_per_job: int = Field(default=40_000, ge=1)
    timezone: str = "Asia/Tehran"
    refund_on_internal_failure: bool = True

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("invalid office quota timezone") from exc
        return value


class OfficeAdminQuotaConfig(BaseModel):
    jobs_per_day: int = Field(default=20, ge=1)
    max_characters_per_job: int = Field(default=150_000, ge=1)


class OfficeLimitsConfig(BaseModel):
    max_input_files_per_job: int = Field(default=1, ge=1, le=1)
    max_file_size_mb: int = Field(default=25, ge=1)
    max_uncompressed_size_mb: int = Field(default=100, ge=1)
    max_runtime_seconds: int = Field(default=300, ge=1)
    max_repair_attempts: int = Field(default=2, ge=0, le=5)
    max_slides: int = Field(default=40, ge=1)
    max_sheets: int = Field(default=20, ge=1)
    max_non_empty_cells: int = Field(default=20_000, ge=1)
    max_rows_per_sheet: int = Field(default=10_000, ge=1)
    max_columns_per_sheet: int = Field(default=200, ge=1)
    max_formulas: int = Field(default=5_000, ge=0)
    max_embedded_images: int = Field(default=100, ge=0)
    max_zip_entries: int = Field(default=10_000, ge=10)
    max_compression_ratio: int = Field(default=1000, ge=2)
    max_xml_entry_mb: int = Field(default=20, ge=1)
    max_memory_mb: int = Field(default=1024, ge=256)
    max_output_size_mb: int = Field(default=50, ge=1)
    max_cpu_seconds: int = Field(default=300, ge=1)
    max_processes: int = Field(default=64, ge=8)


class OfficeConcurrencyConfig(BaseModel):
    global_jobs: int = Field(default=1, ge=1)
    per_user_jobs: int = Field(default=1, ge=1)


class OfficeRetentionConfig(BaseModel):
    completed_job_hours: int = Field(default=24, ge=1)
    failed_job_hours: int = Field(default=24, ge=1)


class OfficeConfig(BaseModel):
    enabled: bool = False
    cli_path: str = "/usr/local/lib/zero-office/officecli"
    workspace_root: str = Field(default_factory=lambda: str(zero_home_path("office_jobs")))
    visual_review_enabled: bool = True
    pending_attachment_ttl_minutes: int = Field(default=30, ge=1, le=1440)
    lease_seconds: int = Field(default=360, ge=30)
    max_attempts: int = Field(default=3, ge=1, le=10)
    admin_user_ids: list[int] = Field(default_factory=list)
    unlimited_admin_user_ids: list[int] = Field(default_factory=list)
    rollout_required: bool = False
    rollout_user_ids: list[int] = Field(default_factory=list)
    rollout_chat_ids: list[int] = Field(default_factory=list)
    quota: OfficeQuotaConfig = Field(default_factory=OfficeQuotaConfig)
    admin_quota: OfficeAdminQuotaConfig = Field(default_factory=OfficeAdminQuotaConfig)
    limits: OfficeLimitsConfig = Field(default_factory=OfficeLimitsConfig)
    concurrency: OfficeConcurrencyConfig = Field(default_factory=OfficeConcurrencyConfig)
    retention: OfficeRetentionConfig = Field(default_factory=OfficeRetentionConfig)

    @model_validator(mode="after")
    def validate_consistency(self) -> "OfficeConfig":
        if self.concurrency.per_user_jobs > self.concurrency.global_jobs:
            raise ValueError("office per-user concurrency exceeds global concurrency")

        def _absolute_on_any_platform(value: str) -> bool:
            # Accept POSIX-style paths ("/usr/local/...") even when running on
            # Windows: the Office worker is Linux-targeted and its default
            # cli_path is a POSIX path. Path(...).is_absolute() alone rejects
            # it on Windows and makes every ZeroConfig unconstructible.
            return PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()

        if not _absolute_on_any_platform(self.cli_path):
            raise ValueError("office cli_path must be absolute")
        if not _absolute_on_any_platform(self.workspace_root):
            raise ValueError("office workspace_root must be absolute")
        return self

    @classmethod
    def from_env(cls, base: dict[str, Any] | None = None) -> "OfficeConfig":
        data = dict(base or {})
        quota = dict(data.get("quota") or {})
        admin = dict(data.get("admin_quota") or {})
        limits = dict(data.get("limits") or {})
        concurrency = dict(data.get("concurrency") or {})
        retention = dict(data.get("retention") or {})

        def env_bool(name: str, current: bool) -> bool:
            raw = os.getenv(name)
            if raw is None:
                return current
            if raw.strip().lower() not in {"true", "false", "1", "0", "yes", "no"}:
                raise ValueError(f"invalid boolean for {name}")
            return raw.strip().lower() in {"true", "1", "yes"}

        def env_int(name: str, current: int) -> int:
            raw = os.getenv(name)
            return current if raw is None else int(raw)

        def env_ids(name: str, current: list[int]) -> list[int]:
            raw = os.getenv(name)
            if raw is None:
                return current
            return [int(item.strip()) for item in raw.split(",") if item.strip()]

        data["enabled"] = env_bool("ZERO_OFFICE_ENABLED", bool(data.get("enabled", False)))
        data["cli_path"] = os.getenv("ZERO_OFFICE_CLI_PATH", data.get("cli_path", cls.model_fields["cli_path"].default))
        data["workspace_root"] = os.getenv("ZERO_OFFICE_WORKSPACE_ROOT", data.get("workspace_root", cls.model_fields["workspace_root"].default))
        data["visual_review_enabled"] = env_bool("ZERO_OFFICE_VISUAL_REVIEW_ENABLED", bool(data.get("visual_review_enabled", True)))
        data["pending_attachment_ttl_minutes"] = env_int("ZERO_OFFICE_PENDING_ATTACHMENT_TTL_MINUTES", int(data.get("pending_attachment_ttl_minutes", 30)))
        data["lease_seconds"] = env_int("ZERO_OFFICE_LEASE_SECONDS", int(data.get("lease_seconds", 360)))
        data["max_attempts"] = env_int("ZERO_OFFICE_MAX_ATTEMPTS", int(data.get("max_attempts", 3)))
        data["admin_user_ids"] = env_ids("ZERO_OFFICE_ADMIN_IDS", list(data.get("admin_user_ids") or []))
        data["unlimited_admin_user_ids"] = env_ids("ZERO_OFFICE_UNLIMITED_ADMIN_IDS", list(data.get("unlimited_admin_user_ids") or []))
        data["rollout_required"] = env_bool("ZERO_OFFICE_ROLLOUT_REQUIRED", bool(data.get("rollout_required", False)))
        data["rollout_user_ids"] = env_ids("ZERO_OFFICE_ROLLOUT_USER_IDS", list(data.get("rollout_user_ids") or []))
        data["rollout_chat_ids"] = env_ids("ZERO_OFFICE_ROLLOUT_CHAT_IDS", list(data.get("rollout_chat_ids") or []))
        quota["jobs_per_user_per_day"] = env_int("ZERO_OFFICE_USER_JOBS_PER_DAY", int(quota.get("jobs_per_user_per_day", 1)))
        quota["max_characters_per_job"] = env_int("ZERO_OFFICE_USER_MAX_CHARACTERS", int(quota.get("max_characters_per_job", 40_000)))
        quota["timezone"] = os.getenv("ZERO_OFFICE_TIMEZONE", quota.get("timezone", "Asia/Tehran"))
        quota["refund_on_internal_failure"] = env_bool("ZERO_OFFICE_REFUND_ON_INTERNAL_FAILURE", bool(quota.get("refund_on_internal_failure", True)))
        admin["jobs_per_day"] = env_int("ZERO_OFFICE_ADMIN_JOBS_PER_DAY", int(admin.get("jobs_per_day", 20)))
        admin["max_characters_per_job"] = env_int("ZERO_OFFICE_ADMIN_MAX_CHARACTERS", int(admin.get("max_characters_per_job", 150_000)))
        for env_name, key, default in (
            ("ZERO_OFFICE_MAX_FILE_SIZE_MB", "max_file_size_mb", 25), ("ZERO_OFFICE_MAX_UNCOMPRESSED_MB", "max_uncompressed_size_mb", 100),
            ("ZERO_OFFICE_MAX_RUNTIME_SECONDS", "max_runtime_seconds", 300), ("ZERO_OFFICE_MAX_REPAIR_ATTEMPTS", "max_repair_attempts", 2),
            ("ZERO_OFFICE_MAX_SLIDES", "max_slides", 40), ("ZERO_OFFICE_MAX_SHEETS", "max_sheets", 20),
            ("ZERO_OFFICE_MAX_NONEMPTY_CELLS", "max_non_empty_cells", 20_000), ("ZERO_OFFICE_MAX_ROWS_PER_SHEET", "max_rows_per_sheet", 10_000),
            ("ZERO_OFFICE_MAX_COLUMNS_PER_SHEET", "max_columns_per_sheet", 200), ("ZERO_OFFICE_MAX_FORMULAS", "max_formulas", 5_000),
            ("ZERO_OFFICE_MAX_EMBEDDED_IMAGES", "max_embedded_images", 100), ("ZERO_OFFICE_MAX_ZIP_ENTRIES", "max_zip_entries", 10_000),
            ("ZERO_OFFICE_MAX_COMPRESSION_RATIO", "max_compression_ratio", 1000), ("ZERO_OFFICE_MAX_XML_ENTRY_MB", "max_xml_entry_mb", 20),
            ("ZERO_OFFICE_MAX_MEMORY_MB", "max_memory_mb", 1024), ("ZERO_OFFICE_MAX_OUTPUT_SIZE_MB", "max_output_size_mb", 50),
            ("ZERO_OFFICE_MAX_CPU_SECONDS", "max_cpu_seconds", 300), ("ZERO_OFFICE_MAX_PROCESSES", "max_processes", 64),
        ):
            limits[key] = env_int(env_name, int(limits.get(key, default)))
        limits["max_input_files_per_job"] = env_int("ZERO_OFFICE_MAX_INPUT_FILES_PER_JOB", int(limits.get("max_input_files_per_job", 1)))
        concurrency["global_jobs"] = env_int("ZERO_OFFICE_GLOBAL_CONCURRENCY", int(concurrency.get("global_jobs", 1)))
        concurrency["per_user_jobs"] = env_int("ZERO_OFFICE_PER_USER_CONCURRENCY", int(concurrency.get("per_user_jobs", 1)))
        retention["completed_job_hours"] = env_int("ZERO_OFFICE_COMPLETED_RETENTION_HOURS", int(retention.get("completed_job_hours", 24)))
        retention["failed_job_hours"] = env_int("ZERO_OFFICE_FAILED_RETENTION_HOURS", int(retention.get("failed_job_hours", 24)))
        data.update(quota=quota, admin_quota=admin, limits=limits, concurrency=concurrency, retention=retention)
        return cls.model_validate(data)


class LogsConfig(BaseModel):
    listener_log: str
    panel_log: str
    router_log: str



def _expand_runtime_paths(content: dict[str, Any]) -> None:
    runtime_home = Path(os.environ.get("ZERO_HOME", "~/.zero")).expanduser()
    def expand(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        value = value.replace("${ZERO_HOME}", str(runtime_home)).replace("$ZERO_HOME", str(runtime_home))
        return os.path.expanduser(os.path.expandvars(value))
    path_fields = (("management_bot", "token_file"), ("listener", "session_path"), ("telegram_search", "session_path"), ("memory", "db_path"), ("office", "workspace_root"), ("logs", "listener_log"), ("logs", "panel_log"), ("logs", "router_log"))
    for section, field in path_fields:
        section_data = content.get(section)
        if isinstance(section_data, dict) and field in section_data:
            section_data[field] = expand(section_data[field])

class ZeroConfig(BaseModel):
    owner_user_id: int
    owner_username: str = "OWNER_USERNAME"
    panel_viewer_usernames: list[str] = Field(default_factory=list)
    panel_viewer_user_ids: list[int] = Field(default_factory=list)
    management_bot: ManagementBotConfig
    listener: ListenerConfig
    persona: PersonaConfig
    policy: PolicyConfig
    router: RouterConfig
    memory: MemoryConfig
    reporting: ReportingConfig
    web: WebConfig = Field(default_factory=WebConfig)
    telegram_search: TelegramSearchConfig = Field(default_factory=TelegramSearchConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    stickers: StickersConfig = Field(default_factory=StickersConfig)
    gifs: GifsConfig = Field(default_factory=GifsConfig)
    reactions: ReactionsConfig = Field(default_factory=ReactionsConfig)
    office: OfficeConfig = Field(default_factory=OfficeConfig)
    logs: LogsConfig


    @classmethod
    def load(cls, path: str | Path) -> "ZeroConfig":
        config_path = Path(path)
        content: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        _expand_runtime_paths(content)
        default_secret = config_path.resolve().parents[1] / "runtime" / "secrets" / "zero.secrets.yaml"
        secret_path = Path(os.environ.get(_SECRET_ENV, str(default_secret)))
        _apply_secret_file(content, secret_path)
        _validate_secret_values(content)
        content["office"] = OfficeConfig.from_env(content.get("office") or {}).model_dump()
        return cls.model_validate(content)
