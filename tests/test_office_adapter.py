from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from zero.config import OfficeConfig
from zero.office.adapter import AdapterError, OfficeCliAdapter, OfficePlan
from zero.office.workspace import create_workspace


def config(**updates):
    data = OfficeConfig().model_dump()
    for key, value in updates.items():
        data[key] = value
    return OfficeConfig.model_validate(data)


def test_structured_plan_accepts_only_whitelisted_operations():
    plan = OfficePlan.model_validate({
        "version": 1,
        "format": "docx",
        "operation": "create_document",
        "output_required": True,
        "operations": [
            {"command": "add", "parent": "/body", "type": "paragraph", "props": {"text": "سلام", "direction": "rtl"}},
        ],
    })
    assert plan.operations[0].command == "add"


@pytest.mark.parametrize(
    "payload",
    [
        {"command": "raw-set", "path": "/", "props": {"xml": "<x/>"}},
        {"command": "add", "parent": "/body", "type": "ole", "props": {"src": "/etc/passwd"}},
        {"command": "set", "path": "/body/p[1];$(id)", "props": {"text": "x"}},
        {"command": "set", "path": "/body/p[1]", "props": {"link": "https://evil.invalid"}},
        {"command": "set", "path": "/Sheet1/A1", "props": {"formula": "WEBSERVICE(\"https://evil\")"}},
    ],
)
def test_plan_rejects_raw_commands_external_content_shell_paths_and_dangerous_formula(payload):
    with pytest.raises(ValueError):
        OfficePlan.model_validate({
            "version": 1, "format": "docx", "operation": "edit_document", "output_required": True,
            "operations": [payload],
        })


def test_plain_excel_values_are_neutralized_against_formula_injection():
    plan = OfficePlan.model_validate({
        "version": 1, "format": "xlsx", "operation": "create_spreadsheet", "output_required": True,
        "operations": [{"command": "set", "path": "/Sheet1/A1", "props": {"value": "=2+2"}}],
    })
    assert plan.operations[0].props["value"] == "'=2+2"


def test_read_only_plan_cannot_contain_mutations_or_require_output():
    with pytest.raises(ValueError):
        OfficePlan.model_validate({
            "version": 1, "format": "docx", "operation": "read_document", "output_required": False,
            "operations": [{"command": "remove", "path": "/body/p[1]"}],
        })


def test_read_only_plan_requires_bounded_response_text():
    plan = OfficePlan.model_validate({
        "version": 1, "format": "docx", "operation": "read_document", "output_required": False,
        "operations": [], "response_text": "موضوع سند آزمایشی است.",
    })
    assert plan.response_text == "موضوع سند آزمایشی است."


def test_adapter_uses_argv_without_shell_and_fixed_workspace_paths(tmp_path, monkeypatch):
    workspace = create_workspace(tmp_path, chat_id=1, job_id="job1")
    calls = []

    class FakeProcess:
        returncode = 0
        pid = 123
        def communicate(self, timeout=None):
            return ('{"success":true,"data":{}}', "")

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    cli = tmp_path / "officecli"
    cli.write_text("#!/bin/sh" + chr(10), encoding="utf-8")
    adapter = OfficeCliAdapter(config(cli_path=str(cli)), workspace)
    adapter.run_cli(["validate", str(workspace.output / "safe.docx"), "--json"])
    argv, kwargs = calls[0]
    assert argv[0] == str(cli)
    assert kwargs.get("shell", False) is False
    assert kwargs["cwd"] == workspace.working
    assert kwargs["env"]["OFFICECLI_SKIP_UPDATE"] == "1"
    assert "https://" not in " ".join(argv)


def test_adapter_rejects_any_file_argument_outside_workspace(tmp_path):
    workspace = create_workspace(tmp_path, chat_id=1, job_id="job1")
    cli = tmp_path / "officecli"
    cli.write_text("#!/bin/sh" + chr(10), encoding="utf-8")
    adapter = OfficeCliAdapter(config(cli_path=str(cli)), workspace)
    with pytest.raises(AdapterError, match="workspace_escape"):
        adapter.run_cli(["validate", "/etc/passwd", "--json"])


def test_timeout_maps_to_safe_error_and_kills_process_group(tmp_path, monkeypatch):
    workspace = create_workspace(tmp_path, chat_id=1, job_id="job1")
    killed = []

    class HungProcess:
        returncode = None

        def kill(self):
            killed.append((self.pid, "KILL"))
        pid = 456
        def communicate(self, timeout=None):
            if killed:
                self.returncode = -9
                return ("", "")
            raise subprocess.TimeoutExpired("officecli", timeout)

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: HungProcess())
    if hasattr(os, "killpg"):
        monkeypatch.setattr("os.killpg", lambda pid, sig: killed.append((pid, sig)))
    cli = tmp_path / "officecli"
    cli.write_text("#!/bin/sh" + chr(10), encoding="utf-8")
    adapter = OfficeCliAdapter(config(cli_path=str(cli), limits={**OfficeConfig().limits.model_dump(), "max_runtime_seconds": 1}), workspace)
    with pytest.raises(AdapterError, match="officecli_timeout"):
        adapter.run_cli(["--version"])
    assert killed and killed[0][0] == 456


def test_nonzero_or_malformed_json_output_is_mapped_without_exposing_stderr(tmp_path, monkeypatch):
    workspace = create_workspace(tmp_path, chat_id=1, job_id="job1")

    class BadProcess:
        pid = 1
        returncode = 7
        def communicate(self, timeout=None):
            return ("not json", "secret /root/path token=abc")

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: BadProcess())
    adapter = OfficeCliAdapter(config(), workspace)
    with pytest.raises(AdapterError) as error:
        adapter.run_cli(["--version"], expect_json=True)
    assert "/root/path" not in str(error.value)
    assert "token" not in str(error.value)
