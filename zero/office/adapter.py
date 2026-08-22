from __future__ import annotations

import json
import os
from pathlib import Path
import re
try:
    import resource  # POSIX only; guarded so the module imports on Windows.
except ImportError:  # pragma: no cover - Windows has no resource module
    resource = None
import shutil
import signal
import subprocess
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from zero.config import OfficeConfig
from .preflight import DANGEROUS_FORMULA
from .workspace import JobWorkspace


_ALLOWED_COMMANDS = {"add", "set", "remove", "move", "swap"}
_ALLOWED_TYPES = {
    "paragraph", "run", "table", "row", "cell", "header", "footer", "section", "break", "field", "toc", "chart",
    "sheet", "col", "namedrange", "pivottable", "sparkline", "validation", "autofilter", "conditionalformatting", "textbox", "shape",
    "slide", "picture", "connector", "group", "notes", "comment", "transition", "equation", "diagram",
}
_FORBIDDEN_PROPERTY_FRAGMENTS = {
    "src", "url", "link", "hyperlink", "external", "ole", "embed", "macro", "script", "video", "audio", "xml", "xpath", "progid",
}
_SAFE_PATH = re.compile(r"^/[\w\- .!:@=\[\]/]+$", re.UNICODE)
_SAFE_PROP = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_OPERATION_FORMATS = {
    "docx": {"read_document", "create_document", "edit_document"},
    "xlsx": {"read_spreadsheet", "create_spreadsheet", "edit_spreadsheet"},
    "pptx": {"read_presentation", "create_presentation", "edit_presentation"},
}


class AdapterError(RuntimeError):
    def __init__(self, code: str, *, exit_code: int | None = None):
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


class PlanOperation(BaseModel):
    command: Literal["add", "set", "remove", "move", "swap"]
    path: str | None = None
    parent: str | None = None
    type: str | None = None
    props: dict[str, str] = Field(default_factory=dict)
    to: str | None = None
    path2: str | None = None
    after: str | None = None
    before: str | None = None
    index: int | None = Field(default=None, ge=0, le=20_000)

    @field_validator("path", "parent", "to", "path2", "after", "before")
    @classmethod
    def safe_document_path(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if len(value) > 300 or (value != "/" and not _SAFE_PATH.fullmatch(value)) or any(ch in value for ch in ";|&$`\\\x00(){}<>"):
            raise ValueError("unsafe document path")
        return value

    @field_validator("type")
    @classmethod
    def safe_type(cls, value: str | None) -> str | None:
        if value is not None and value.casefold() not in _ALLOWED_TYPES:
            raise ValueError("unsupported element type")
        return value.casefold() if value else value

    @field_validator("props")
    @classmethod
    def safe_props(cls, props: dict[str, str]) -> dict[str, str]:
        if len(props) > 50:
            raise ValueError("too many properties")
        clean: dict[str, str] = {}
        for key, raw in props.items():
            lowered = key.casefold()
            if not _SAFE_PROP.fullmatch(key) or any(fragment in lowered for fragment in _FORBIDDEN_PROPERTY_FRAGMENTS):
                raise ValueError("unsafe property")
            value = str(raw)
            if len(value) > 20_000 or "\x00" in value:
                raise ValueError("unsafe property value")
            if lowered == "formula":
                if DANGEROUS_FORMULA.search(value):
                    raise ValueError("dangerous formula")
            elif lowered in {"value", "text"} and value.lstrip().startswith(("=", "+", "-", "@")) and key.casefold() == "value":
                value = "'" + value
            clean[key] = value
        return clean

    @model_validator(mode="after")
    def required_fields(self) -> "PlanOperation":
        if self.command == "add" and (not self.parent or not self.type):
            raise ValueError("add requires parent and type")
        if self.command in {"set", "remove", "move"} and not self.path:
            raise ValueError(f"{self.command} requires path")
        if self.command == "swap" and (not self.path or not self.path2):
            raise ValueError("swap requires two paths")
        return self


class OfficePlan(BaseModel):
    version: Literal[1]
    format: Literal["docx", "xlsx", "pptx"]
    operation: Literal[
        "read_document", "create_document", "edit_document",
        "read_spreadsheet", "create_spreadsheet", "edit_spreadsheet",
        "read_presentation", "create_presentation", "edit_presentation",
    ]
    output_required: bool
    operations: list[PlanOperation] = Field(default_factory=list, max_length=500)
    response_text: str | None = Field(default=None, max_length=150_000)

    @model_validator(mode="after")
    def validate_plan(self) -> "OfficePlan":
        if self.operation not in _OPERATION_FORMATS[self.format]:
            raise ValueError("operation format mismatch")
        if self.operation.startswith("read_"):
            if self.output_required or self.operations:
                raise ValueError("read-only plan cannot mutate")
        elif not self.output_required:
            raise ValueError("mutation plan requires output")
        return self


class OfficeCliAdapter:
    def __init__(self, config: OfficeConfig, workspace: JobWorkspace):
        self.config = config
        self.workspace = workspace
        self.cli = Path(config.cli_path).resolve()

    def _ensure_workspace_args(self, args: list[str]) -> None:
        root = self.workspace.root.resolve()
        for item in args:
            if not item or item.startswith("--"):
                continue
            candidate = Path(item)
            if candidate.is_absolute() or item.startswith("/"):
                try:
                    candidate.resolve().relative_to(root)
                except ValueError as exc:
                    raise AdapterError("workspace_escape") from exc

    def _preexec(self) -> None:
        limits = self.config.limits
        os.setsid()
        if resource is None:  # pragma: no cover - non-POSIX platform
            return
        resource.setrlimit(resource.RLIMIT_CPU, (limits.max_cpu_seconds, limits.max_cpu_seconds + 1))
        # Do not use RLIMIT_AS here: Chrome reserves a very large sparse virtual
        # address space and fails before rendering under a small address-space
        # cap. The production worker enforces physical RAM with systemd
        # MemoryMax/MemorySwapMax instead; CPU, file size and process caps remain
        # defense-in-depth at the subprocess boundary.
        resource.setrlimit(resource.RLIMIT_FSIZE, (limits.max_output_size_mb * 1024 * 1024,) * 2)
        # Chrome's renderer uses many threads; RLIMIT_NPROC counts threads on
        # Linux and made valid previews fail. The production cgroup uses
        # TasksMax for a process/thread ceiling instead.
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.umask(0o077)

    def run_cli(self, args: list[str], *, expect_json: bool = False) -> dict[str, Any] | str:
        # Validate the workspace boundary first: an escape attempt must be
        # reported as such even when the CLI binary itself is unavailable.
        self._ensure_workspace_args(args)
        if not self.cli.is_absolute() or not self.cli.exists() or not os.access(self.cli, os.X_OK):
            raise AdapterError("officecli_unavailable")
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(self.workspace.working),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "OFFICECLI_SKIP_UPDATE": "1",
            "OFFICECLI_NO_AUTO_RESIDENT": "1",
            "DOTNET_EnableDiagnostics": "0",
        }
        posix = os.name == "posix"
        process = subprocess.Popen(
            [str(self.cli), *args], cwd=self.workspace.working, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            shell=False, start_new_session=False,
            preexec_fn=self._preexec if posix else None,
        )
        try:
            stdout, _stderr = process.communicate(timeout=self.config.limits.max_runtime_seconds)
        except subprocess.TimeoutExpired as exc:
            try:
                if hasattr(os, "killpg"):
                    os.killpg(process.pid, signal.SIGKILL)
                else:  # Windows: no process groups; kill the direct child.
                    process.kill()
                process.communicate(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
            raise AdapterError("officecli_timeout") from exc
        payload: dict[str, Any] | None = None
        if expect_json or "--json" in args:
            try:
                payload = json.loads(stdout)
            except (json.JSONDecodeError, TypeError) as exc:
                raise AdapterError("officecli_malformed_output", exit_code=process.returncode) from exc
        if process.returncode != 0 or (payload is not None and payload.get("success") is False):
            code = "officecli_failure"
            if payload is None and stdout.lstrip().startswith("{"):
                try:
                    parsed_error = json.loads(stdout)
                    if isinstance(parsed_error, dict):
                        payload = parsed_error
                except json.JSONDecodeError:
                    pass
            if payload:
                code = str((payload.get("error") or {}).get("code") or code)
            raise AdapterError(code, exit_code=process.returncode)
        return payload if payload is not None else stdout.strip()

    def health(self) -> dict[str, Any]:
        try:
            version = self.run_cli(["--version"])
            return {"available": True, "version": str(version)}
        except AdapterError as exc:
            return {"available": False, "error_code": exc.code}

    def execute(self, plan: OfficePlan, *, input_path: Path | None = None) -> dict[str, Any]:
        extension = plan.format
        target = self.workspace.output / f"result.{extension}"
        if plan.operation.startswith("read_"):
            if input_path is None:
                raise AdapterError("missing_input")
            document = input_path.resolve()
        elif plan.operation.startswith("create_"):
            args = ["create", str(target)]
            if plan.format == "docx":
                args += ["--locale", "fa-IR"]
            args.append("--json")
            self.run_cli(args, expect_json=True)
            document = target
        else:
            if input_path is None:
                raise AdapterError("missing_input")
            shutil.copyfile(input_path, target, follow_symlinks=False)
            os.chmod(target, 0o600)
            document = target
        if plan.operations:
            batch_path = self.workspace.working / "plan.json"
            batch_path.write_text(json.dumps([item.model_dump(exclude_none=True) for item in plan.operations], ensure_ascii=False), encoding="utf-8")
            os.chmod(batch_path, 0o600)
            self.run_cli(["batch", str(document), "--input", str(batch_path), "--json"], expect_json=True)
        validation = self.run_cli(["validate", str(document), "--json"], expect_json=True)
        issues = self.run_cli(["view", str(document), "issues", "--limit", "100", "--json"], expect_json=True)
        text = self.run_cli(["view", str(document), "text", "--max-lines", "20000", "--json"], expect_json=True)
        previews: list[str] = []
        if plan.output_required:
            preview = self.workspace.preview / "preview.png"
            render_args = ["view", str(document), "screenshot", "-o", str(preview)]
            if plan.format in {"docx", "pptx"}:
                render_args += ["--grid", "auto"]
            # OfficeCLI 1.0.138 emits the output path as plain text for a
            # successful screenshot even when other view modes support JSON.
            # Do not invent a JSON contract the installed binary does not have.
            self.run_cli(render_args, expect_json=False)
            if preview.exists() and preview.stat().st_size > 0:
                previews.append(str(preview))
            else:
                raise AdapterError("render_missing")
        return {
            "output_path": str(target) if plan.output_required else "",
            "text": text,
            "validation": validation,
            "issues": issues,
            "preview_paths": previews,
        }
