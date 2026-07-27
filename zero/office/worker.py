from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
from typing import Any, Callable

from zero.config import OfficeConfig
from .adapter import AdapterError, OfficeCliAdapter, OfficePlan
from .db import OfficeRepository
from .planner import OfficePlanner, PlanningError
from .preflight import MIMES, PreflightError, inspect_ooxml
from .workspace import WorkspaceError, open_workspace


class OutputValidationError(RuntimeError):
    pass


def _safe_result_text(value: Any, limit: int = 150_000) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return encoded[:limit]


class PlanningCoordinator:
    def __init__(self, repository: OfficeRepository, config: OfficeConfig, planner: OfficePlanner, *, worker_id: str = "listener"):
        self.repository, self.config, self.planner, self.worker_id = repository, config, planner, worker_id

    async def tick(self) -> dict[str, Any] | None:
        job = self.repository.claim_for_planning(self.worker_id, lease_seconds=self.config.lease_seconds)
        if not job:
            return None
        try:
            extracted = ""
            if job["input_path"]:
                report = inspect_ooxml(Path(job["input_path"]), declared_mime=job["detected_mime"], limits=self.config.limits)
                extracted = report.normalized_text
            plan = await self.planner.plan(format=job["office_format"], request=job["request_text"], extracted_text=extracted)
            self.repository.update_job_artifacts(job["id"], operation_type=plan.operation, plan_json=plan.model_dump_json())
            self.repository.transition(job["id"], "queued", expected="planning", reason="plan_validated")
            return {"job_id": job["id"], "status": "queued"}
        except (PlanningError, PreflightError, ValueError) as exc:
            current = self.repository.get_job(job["id"])
            if current and int(current["attempt_count"]) < self.config.max_attempts:
                self.repository.transition(job["id"], "quota_reserved", expected="planning", reason="planning_retry", error_code=type(exc).__name__)
                return {"job_id": job["id"], "status": "retry"}
            self.repository.transition(job["id"], "failed", expected="planning", reason="planning_failed", error_code="planning_failed", error_message_safe="برنامه‌ریزی پردازش کامل نشد.")
            self.repository.refund_quota(job["id"], "planning_failed")
            return {"job_id": job["id"], "status": "failed"}


class OfficeWorker:
    def __init__(
        self, repository: OfficeRepository, config: OfficeConfig, *, worker_id: str = "office-worker",
        adapter_factory: Callable[[Any], OfficeCliAdapter] | None = None,
    ):
        self.repository, self.config, self.worker_id = repository, config, worker_id
        self.adapter_factory = adapter_factory

    def _adapter(self, workspace):
        return self.adapter_factory(workspace) if self.adapter_factory else OfficeCliAdapter(self.config, workspace)

    def _fail_or_retry(self, job: dict[str, Any], code: str) -> dict[str, Any]:
        current = self.repository.get_job(job["id"]) or job
        workspace = open_workspace(self.config.workspace_root, chat_id=job["chat_id"], job_id=job["id"])
        output = workspace.output / f"result.{job['office_format']}"
        repairable = output.exists() and int(current["repair_count"]) < self.config.limits.max_repair_attempts and code not in {"officecli_timeout", "officecli_unavailable"}
        if repairable:
            self.repository.update_job_artifacts(job["id"], output_path=str(output), error_code=code, error_message_safe="خروجی نیاز به اصلاح دارد.")
            self.repository.transition(job["id"], "repairing", expected="processing", reason=code, error_code=code)
            return {"job_id": job["id"], "status": "repairing", "error_code": code}
        if int(current["attempt_count"]) < self.config.max_attempts:
            self.repository.transition(job["id"], "queued", expected="processing", reason="technical_retry", error_code=code)
            return {"job_id": job["id"], "status": "retry", "error_code": code}
        self.repository.transition(job["id"], "failed", expected="processing", reason="attempts_exhausted", error_code=code, error_message_safe="پردازش فایل کامل نشد.")
        self.repository.refund_quota(job["id"], code)
        return {"job_id": job["id"], "status": "failed", "error_code": code}

    def tick(self) -> dict[str, Any] | None:
        job = self.repository.claim_next(
            self.worker_id, lease_seconds=self.config.lease_seconds,
            global_limit=self.config.concurrency.global_jobs,
            per_user_limit=self.config.concurrency.per_user_jobs,
        )
        if not job:
            return None
        try:
            plan = OfficePlan.model_validate_json(job["plan_json"])
            workspace = open_workspace(self.config.workspace_root, chat_id=job["chat_id"], job_id=job["id"])
            self.repository.transition(job["id"], "processing", expected="planning", reason="worker_started")
            adapter = self._adapter(workspace)
            result = adapter.execute(plan, input_path=Path(job["input_path"]) if job["input_path"] else None)
            self.repository.transition(job["id"], "validating_output", expected="processing", reason="officecli_complete")
            output_path = str(result.get("output_path") or "")
            final_text = _safe_result_text(result.get("text") or {})
            if not plan.output_required and plan.response_text:
                final_text = plan.response_text
            if plan.output_required:
                if not output_path:
                    raise OutputValidationError("output_missing")
                report = inspect_ooxml(Path(output_path), declared_mime=MIMES[plan.format], limits=self.config.limits)
                lowered = report.normalized_text.casefold()
                if not lowered or "traceback" in lowered or "{{" in report.normalized_text or "}}" in report.normalized_text:
                    raise OutputValidationError("content_validation")
                final_text = report.normalized_text[:150_000]
            previews = list(result.get("preview_paths") or [])
            self.repository.update_job_artifacts(
                job["id"], output_path=output_path, result_text=final_text,
                preview_paths_json=json.dumps(previews, separators=(",", ":")), error_code="", error_message_safe="",
            )
            if plan.output_required:
                self.repository.transition(job["id"], "rendering", expected="validating_output", reason="preview_created")
                next_status = "reviewing" if self.config.visual_review_enabled else "completed"
                self.repository.transition(job["id"], next_status, expected="rendering", reason="visual_review_gate")
            else:
                self.repository.transition(job["id"], "completed", expected="validating_output", reason="read_only_complete")
            duration = max(0, int(time.time()) - int(job["created_at"]))
            self.repository.increment_metric("office_job_duration_seconds_sum", duration)
            self.repository.increment_metric("office_job_duration_seconds_count", 1)
            return {"job_id": job["id"], "status": next_status if plan.output_required else "completed"}
        except AdapterError as exc:
            self.repository.increment_metric("officecli_failures_total")
            current = self.repository.get_job(job["id"])
            if current and current["status"] == "planning":
                self.repository.transition(job["id"], "processing", expected="planning", reason="adapter_boundary")
            return self._fail_or_retry(job, exc.code)
        except (OutputValidationError, PreflightError, WorkspaceError, ValueError, OSError) as exc:
            current = self.repository.get_job(job["id"])
            if current and current["status"] == "validating_output":
                self.repository.transition(job["id"], "repairing", expected="validating_output", reason=type(exc).__name__, error_code="output_invalid")
                return {"job_id": job["id"], "status": "repairing", "error_code": "output_invalid"}
            if current and current["status"] == "planning":
                self.repository.transition(job["id"], "processing", expected="planning", reason="worker_boundary")
            return self._fail_or_retry(job, "worker_internal")


class RepairCoordinator:
    def __init__(self, repository: OfficeRepository, config: OfficeConfig, planner: OfficePlanner):
        self.repository, self.config, self.planner = repository, config, planner

    async def tick(self) -> dict[str, Any] | None:
        rows = self.repository.list_jobs({"repairing"}, limit=1)
        if not rows:
            return None
        job = rows[0]
        repair_count = int(job["repair_count"]) + 1
        if repair_count > self.config.limits.max_repair_attempts:
            self.repository.transition(job["id"], "failed", expected="repairing", reason="repair_exhausted", error_code="repair_exhausted", error_message_safe="خروجی نهایی معتبر نشد.")
            self.repository.refund_quota(job["id"], "repair_exhausted")
            return {"job_id": job["id"], "status": "failed"}
        try:
            source = Path(job["output_path"] or job["input_path"])
            report = inspect_ooxml(source, declared_mime=MIMES[job["office_format"]], limits=self.config.limits)
            request = f"{job['request_text']}\nRepair only this specific validation problem: {job['error_code']}"
            plan = await self.planner.plan(format=job["office_format"], request=request, extracted_text=report.normalized_text)
            if plan.operation.startswith("create_") or plan.operation.startswith("read_"):
                raise PlanningError("repair_must_edit")
            workspace = open_workspace(self.config.workspace_root, chat_id=job["chat_id"], job_id=job["id"])
            repair_input = workspace.working / f"repair-{repair_count}.{job['office_format']}"
            shutil.copyfile(source, repair_input)
            repair_input.chmod(0o400)
            self.repository.update_job_artifacts(
                job["id"], input_path=str(repair_input), plan_json=plan.model_dump_json(),
                operation_type=plan.operation, repair_count=repair_count,
            )
            self.repository.transition(job["id"], "queued", expected="repairing", reason="bounded_repair_planned")
            return {"job_id": job["id"], "status": "queued", "repair_count": repair_count}
        except (PlanningError, PreflightError, OSError, WorkspaceError, ValueError):
            self.repository.transition(job["id"], "failed", expected="repairing", reason="repair_planning_failed", error_code="repair_failed", error_message_safe="اصلاح خودکار خروجی کامل نشد.")
            self.repository.refund_quota(job["id"], "repair_failed")
            return {"job_id": job["id"], "status": "failed"}
