from __future__ import annotations

from pathlib import Path

import pytest

from zero.config import OfficeConfig
from zero.office.adapter import OfficeCliAdapter, OfficePlan
from zero.office.preflight import inspect_ooxml
from zero.office.workspace import create_workspace


CLI = Path("/usr/local/lib/zero-office/officecli")


def adapter(tmp_path, job):
    workspace = create_workspace(tmp_path, chat_id=1, job_id=job)
    cfg = OfficeConfig(
        enabled=True, cli_path=str(CLI), workspace_root=str(tmp_path), visual_review_enabled=False,
        limits={**OfficeConfig().limits.model_dump(), "max_runtime_seconds": 120, "max_memory_mb": 2048},
    )
    return OfficeCliAdapter(cfg, workspace), cfg


@pytest.mark.parametrize(
    ("fmt", "operation", "operations", "expected"),
    [
        ("docx", "create_document", [
            {"command": "add", "parent": "/body", "type": "paragraph", "props": {"text": "گزارش فارسی", "style": "Heading1", "direction": "rtl"}},
            {"command": "add", "parent": "/body", "type": "paragraph", "props": {"text": "این یک متن فارسی با نیم‌فاصله و عدد ۱۲۳ است.", "direction": "rtl"}},
        ], "گزارش فارسی"),
        ("xlsx", "create_spreadsheet", [
            {"command": "set", "path": "/Sheet1/A1", "props": {"value": "نام", "bold": "true", "direction": "rtl"}},
            {"command": "set", "path": "/Sheet1/B1", "props": {"value": "نمره", "bold": "true"}},
            {"command": "set", "path": "/Sheet1/A2", "props": {"value": "علی"}},
            {"command": "set", "path": "/Sheet1/B2", "props": {"value": "19"}},
        ], "علی"),
        ("pptx", "create_presentation", [
            {"command": "add", "parent": "/", "type": "slide", "props": {"title": "انرژی خورشیدی", "background": "F7F3E8"}},
            {"command": "add", "parent": "/slide[1]", "type": "shape", "props": {"text": "مزایا و کاربردها", "x": "2cm", "y": "5cm", "width": "20cm", "height": "2cm", "size": "26", "direction": "rtl"}},
            {"command": "add", "parent": "/slide[1]", "type": "notes", "props": {"text": "یادداشت سخنران", "direction": "rtl"}},
        ], "انرژی خورشیدی"),
    ],
)
def test_real_officecli_create_validate_extract_and_render(tmp_path, fmt, operation, operations, expected):
    assert CLI.exists()
    tool, cfg = adapter(tmp_path, f"real-{fmt}")
    plan = OfficePlan.model_validate({"version": 1, "format": fmt, "operation": operation, "output_required": True, "operations": operations})
    result = tool.execute(plan)
    output = Path(result["output_path"])
    assert output.exists() and output.stat().st_size > 0
    report = inspect_ooxml(output, declared_mime={"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}[fmt], limits=cfg.limits)
    assert expected in report.normalized_text
    assert result["preview_paths"] and Path(result["preview_paths"][0]).stat().st_size > 1000
