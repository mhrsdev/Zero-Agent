"""The panel must never present an unmeasured figure, or a failure, as data.

The dashboard used to render RAM and CPU as an em dash whenever the read raised,
so "this host has no ``/proc/meminfo``" was indistinguishable from "the read
failed"; ``listener_status`` reported ``running: False`` on any host where the
process identity cannot be checked at all, so an unverifiable listener was
rendered as a definite "Offline"; and an unreadable log file was reported as an
empty result set.

These tests pin the three states apart -- ``measured`` / ``unsupported`` /
``failed`` for host measurements, ``running`` / ``stopped`` / ``unverified`` for
the listener -- and pin that a measured zero stays a zero while an unmeasured
figure carries no value at all.
"""
from __future__ import annotations

import ast
import logging
import os
import re
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from test_panel_opt_runtime import FakeBot, build_config, build_panel, login

ROOT = Path(__file__).resolve().parents[1]
PANEL_MODULES = sorted((ROOT / "zero").glob("panel_*.py"))
PANEL_API_SOURCE = (ROOT / "zero" / "panel_api.py").read_text(encoding="utf-8")
APP_JS = (ROOT / "panel" / "app.js").read_text(encoding="utf-8")
PERSIAN = re.compile(r"[\u0600-\u06FF\u200C\u200D]")


@pytest.fixture
async def panel(tmp_path):
    api, config, panel_store = build_panel(tmp_path)
    client = TestClient(TestServer(api.app))
    await client.start_server()
    from types import SimpleNamespace

    yield SimpleNamespace(client=client, api=api, store=panel_store, config=config)
    await client.close()
    await api.stop()


# ------------------------------------------------------------- host measurement states

def test_memory_metric_is_unsupported_when_the_host_has_no_procfs(tmp_path):
    from zero.panel_metrics import memory_percent_used

    assert memory_percent_used(tmp_path / "absent-meminfo") == {"state": "unsupported", "value": None, "reason": None}


def test_memory_metric_reports_a_malformed_source_as_failed(tmp_path):
    from zero.panel_metrics import memory_percent_used

    source = tmp_path / "meminfo"
    source.write_text("MemTotal: 100 kB\nsomething-without-a-colon\n", encoding="utf-8")

    metric = memory_percent_used(source)

    assert metric["state"] == "failed", "a parse failure must not be reported as an unsupported host"
    assert metric["value"] is None
    assert metric["reason"] == "KeyError"


def test_memory_metric_reports_an_unreadable_source_as_failed(tmp_path):
    from zero.panel_metrics import memory_percent_used

    metric = memory_percent_used(tmp_path)  # a directory exists but cannot be read as a file

    assert metric["state"] == "failed"
    assert metric["reason"] in {"PermissionError", "IsADirectoryError"}


def test_a_measured_zero_stays_a_measurement(tmp_path):
    from zero.panel_metrics import memory_percent_used

    source = tmp_path / "meminfo"
    source.write_text("MemTotal: 100 kB\nMemAvailable: 100 kB\n", encoding="utf-8")

    assert memory_percent_used(source) == {"state": "measured", "value": "0%", "reason": None}


def test_load_average_is_unsupported_without_the_posix_interface(monkeypatch):
    from zero import panel_metrics

    monkeypatch.delattr(os, "getloadavg", raising=False)

    assert panel_metrics.cpu_load_average() == {"state": "unsupported", "value": None, "reason": None}


def test_load_average_reports_a_measured_zero(monkeypatch):
    from zero import panel_metrics

    monkeypatch.setattr(os, "getloadavg", lambda: (0.0, 0.1, 0.2), raising=False)

    assert panel_metrics.cpu_load_average() == {"state": "measured", "value": "0.00", "reason": None}


def test_disk_metric_measures_the_volume_when_the_database_file_is_absent(tmp_path):
    from zero.panel_metrics import disk_free_percent

    metric = disk_free_percent(tmp_path / "not-created-yet.db")

    assert metric["state"] == "measured", f"the volume is measurable; got {metric}"
    assert metric["value"].endswith("% free")


# ------------------------------------------------------------- listener state, not a guess

def test_listener_reports_stopped_without_a_pid_record(tmp_path, monkeypatch):
    from zero import runtime_control

    monkeypatch.setattr(runtime_control, "LISTENER_PID", tmp_path / "absent.pid")

    assert runtime_control.listener_status() == {"running": False, "pid": 0, "state": "stopped"}


def test_listener_reports_unverified_when_process_identity_cannot_be_checked(tmp_path, monkeypatch):
    from zero import runtime_control

    pid_file = tmp_path / "listener.pid"
    pid_file.write_text("4321", encoding="utf-8")
    monkeypatch.setattr(runtime_control, "LISTENER_PID", pid_file)
    monkeypatch.setattr(runtime_control, "_identity_is_verifiable", lambda: False)

    status = runtime_control.listener_status()

    assert status["pid"] == 4321
    assert status["state"] == "unverified", "an unverifiable listener must not be reported as stopped"
    assert status["running"] is False, "'running' stays reserved for a verified process"


def test_listener_reports_running_when_the_process_identity_matches(tmp_path, monkeypatch):
    from zero import runtime_control

    pid_file = tmp_path / "listener.pid"
    pid_file.write_text("4321", encoding="utf-8")
    monkeypatch.setattr(runtime_control, "LISTENER_PID", pid_file)
    monkeypatch.setattr(runtime_control, "_identity_is_verifiable", lambda: True)
    monkeypatch.setattr(runtime_control, "_process_identity_matches", lambda pid: True)

    assert runtime_control.listener_status() == {"running": True, "pid": 4321, "state": "running"}


def test_an_unreadable_pid_record_is_not_reported_as_stopped(tmp_path, monkeypatch):
    from zero import runtime_control

    monkeypatch.setattr(runtime_control, "LISTENER_PID", tmp_path)  # a directory cannot be read

    assert runtime_control.listener_status()["state"] == "unverified"


# ------------------------------------------------------------- what the dashboard reports

async def test_the_dashboard_reports_a_state_for_every_host_metric(panel, monkeypatch):
    from zero import panel_metrics

    monkeypatch.setattr(panel_metrics, "memory_percent_used",
                        lambda *args, **kwargs: {"state": "failed", "value": None, "reason": "PermissionError"})
    monkeypatch.setattr(panel_metrics, "cpu_load_average",
                        lambda: {"state": "unsupported", "value": None, "reason": None})
    headers = await login(panel)

    payload = await (await panel.client.get("/api/local/dashboard", headers=headers)).json()
    status = payload["status"]

    assert set(status["metrics"]) == {"cpu", "ram", "disk"}
    assert status["metrics"]["ram"] == {"state": "failed", "value": None, "reason": "PermissionError"}
    assert status["metrics"]["cpu"]["state"] == "unsupported"
    for name in ("cpu", "ram"):
        assert status[name] is None, "an unmeasured figure must not carry a value"
    assert status["listener_state"] in {"running", "stopped", "unverified"}
    assert isinstance(payload["sampled_at"], int)


async def test_a_provider_snapshot_failure_is_reported_not_rendered_as_no_model(panel):
    class BrokenRouter:
        def status(self):
            raise RuntimeError("router snapshot unavailable")

    panel.api.router = BrokenRouter()
    headers = await login(panel)

    response = await panel.client.get("/api/local/dashboard", headers=headers)
    payload = await response.json()

    assert response.status == 200, "one broken widget must not take the dashboard down"
    assert payload["provider"] == {"active": "openrouter", "model": None, "state": "failed", "reason": "RuntimeError"}


async def test_an_unreadable_log_component_is_reported_not_silently_empty(panel, tmp_path):
    headers = await login(panel)
    unreadable = tmp_path / "unreadable-log"
    unreadable.mkdir()
    panel.config.logs.router_log = str(unreadable)

    payload = await (await panel.client.get("/api/logs", headers=headers)).json()

    assert [entry["component"] for entry in payload["unreadable"]] == ["unreadable-log"]
    assert payload["unreadable"][0]["reason"] in {"PermissionError", "IsADirectoryError"}
    assert str(unreadable) not in str(payload), "the payload must not carry an absolute path"


async def test_a_failed_login_notification_is_logged_without_identifying_the_admin(tmp_path):
    """The owner alert used to fail into an in-memory deque only, lost on restart."""
    api, _config, _store = build_panel(tmp_path, bot=FakeBot(fail_after=1))
    client = TestClient(TestServer(api.app))
    await client.start_server()
    records: list[logging.LogRecord] = []

    class Sink(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("zero.panel")
    sink = Sink()
    logger.addHandler(sink)
    previous_level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        assert (await client.post("/api/auth/request", json={"identity": "viewer1"})).status == 200
        code = re.search(r"\b(\d{6})\b", api.bot.messages[-1][1]).group(1)
        assert (await client.post("/api/auth/verify", json={"identity": "viewer1", "code": code})).status == 200
        messages = [record.getMessage() for record in records]
        assert any("PANEL_LOGIN_NOTIFY_FAILED" in message and "RuntimeError" in message for message in messages), messages
        assert not any("viewer1" in message or "222222222" in message for message in messages), (
            "diagnostics must not identify the account"
        )
    finally:
        logger.removeHandler(sink)
        logger.setLevel(previous_level)
        await client.close()
        await api.stop()


# ------------------------------------------------------------------- source-level contracts

def _except_handlers(source: str):
    return [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ExceptHandler)]


def _caught_names(handler: ast.ExceptHandler) -> set[str]:
    targets = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return {"<bare except>" if target is None else ast.unparse(target) for target in targets}


def test_no_panel_failure_is_swallowed_without_a_record():
    offenders = [
        f"{path.name}:{handler.lineno}"
        for path in PANEL_MODULES
        for handler in _except_handlers(path.read_text(encoding="utf-8"))
        if all(isinstance(statement, ast.Pass) for statement in handler.body)
    ]
    assert not offenders, f"a discarded exception leaves nothing to diagnose: {offenders}"


def test_the_request_body_parser_does_not_swallow_transport_failures():
    """A dropped connection or a read timeout is not an invalid payload."""
    parser = next(
        node for node in ast.walk(ast.parse(PANEL_API_SOURCE))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_body"
    )
    caught = {name for handler in ast.walk(parser) if isinstance(handler, ast.ExceptHandler)
              for name in _caught_names(handler)}

    assert "Exception" not in caught, f"the body parser hides every failure as a 400; caught {sorted(caught)}"


def test_every_panel_module_lets_cancellation_propagate():
    """Extends the zero/panel_api.py contract to every module the panel grew."""
    offenders = [
        f"{path.name}:{handler.lineno} {name}"
        for path in PANEL_MODULES
        for handler in _except_handlers(path.read_text(encoding="utf-8"))
        for name in _caught_names(handler)
        if "CancelledError" in name or name == "<bare except>"
    ]
    assert not offenders, f"cancellation must propagate out of every panel module: {offenders}"


# ------------------------------------------------------------------------ browser rendering

def test_the_frontend_bootstraps_the_session_once():
    assert APP_JS.count("enter();") == 1, "the entry sequence ran once per duplicated handler block"


def test_the_frontend_renders_a_metric_state_instead_of_truthiness():
    assert "s.cpu||" not in APP_JS and "s.ram||" not in APP_JS, (
        "a truthiness fallback cannot tell a measured zero from an unmeasured metric"
    )
    assert "metric.state" in APP_JS


def test_the_frontend_does_not_report_an_unverified_listener_as_offline():
    assert "s.listener?'Online':'Offline'" not in APP_JS
    assert "listener_state" in APP_JS


def test_the_frontend_offers_no_control_the_panel_csp_disables():
    """``script-src 'self'`` blocks inline handler attributes, so an onclick= button is dead."""
    assert 'onclick="' not in APP_JS
    assert 'onclick="' not in (ROOT / "panel" / "index.html").read_text(encoding="utf-8")


def test_the_panel_frontend_stays_english_only():
    for name in ("app.js", "index.html", "styles.css"):
        text = (ROOT / "panel" / name).read_text(encoding="utf-8")
        assert not PERSIAN.search(text), f"panel/{name} must stay English-only"
