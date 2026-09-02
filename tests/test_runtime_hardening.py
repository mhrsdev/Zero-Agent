"""Runtime hardening regressions from the full-project audit.

Every test here reproduces a defect that the suite did not catch before:

* the panel read whole log files on the event loop on a 2s/5s SSE timer;
* the panel SSE handlers caught ``asyncio.CancelledError``, so shutdown could
  not terminate them;
* ``HybridWeb.search`` ran ``asyncio.run`` inside ``asyncio.to_thread``, binding
  the shared per-host semaphore to a throwaway loop and permanently breaking
  news/knowledge search with ``RuntimeError: bound to a different event loop``;
* a failing template job never advanced ``next_run_at``, so the 30s coordinator
  loop re-ran -- and re-delivered -- it forever;
* listener background loops were unreferenced, unlogged and never cancelled;
* 17 sites used ``with sqlite3.connect(...)``, which commits but never closes;
* the connection pool held one keep-alive socket set per host, forever.
"""
from __future__ import annotations

import ast
import asyncio
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import CONFIG_EXAMPLE

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("zero", "scripts")


def _python_sources() -> list[Path]:
    return [p for directory in SOURCE_DIRS for p in (ROOT / directory).rglob("*.py")]


def _source_of(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


# --------------------------------------------------------------- panel logs

def test_panel_log_read_is_bounded_by_bytes_not_file_size(tmp_path: Path):
    """A production listener.log reaches hundreds of MB; the SSE endpoints
    re-read it every 2s per connected client."""
    from zero.panel_api import _tail_lines

    log = tmp_path / "listener.log"
    log.write_text("".join(f"line-{i}\n" for i in range(200_000)), encoding="utf-8")
    assert log.stat().st_size > 1_000_000

    lines = _tail_lines(log, 4096, 2000)
    assert lines, "the tail must still be readable"
    assert lines[-1] == "line-199999", "the newest line must survive"
    assert sum(len(line) + 1 for line in lines) <= 4096, (
        "the reader must honour its byte budget instead of loading the whole file"
    )


def test_panel_log_read_drops_a_partial_leading_line(tmp_path: Path):
    """Seeking into the middle of a file lands mid-record; a truncated line must
    not be reported or redaction-matched as a whole record."""
    from zero.panel_api import _tail_lines

    log = tmp_path / "panel.log"
    log.write_bytes(b"aaaaaaaaaaaaaaaaaaaa\nbbbb\ncccc\n")
    # 31 bytes total; a 12-byte window starts inside the first record.
    assert _tail_lines(log, 12, 10) == ["bbbb", "cccc"]


def test_panel_log_read_survives_a_missing_file(tmp_path: Path):
    from zero.panel_api import _tail_lines

    assert _tail_lines(tmp_path / "absent.log", 1024, 10) == []


# ------------------------------------------------------- cancellation safety

def _caught_exception_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        targets = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
        for target in targets:
            if target is None:
                names.add("<bare except>")
            else:
                names.add(ast.unparse(target))
    return names


def test_panel_sse_handlers_do_not_swallow_cancellation():
    """CancelledError is a BaseException. Catching it leaves the handler running
    after shutdown cancelled it, so AppRunner.cleanup() waits out its graceful
    timeout instead of completing."""
    caught = _caught_exception_names(ast.parse(_source_of("zero/panel_api.py")))
    offenders = {name for name in caught if "CancelledError" in name or name == "<bare except>"}
    assert not offenders, (
        f"zero/panel_api.py must let cancellation propagate; caught: {sorted(offenders)}"
    )


def test_shared_service_methods_never_start_their_own_event_loop():
    """A method on a long-lived service can be called while a loop is already
    running, or from a worker thread. ``asyncio.run`` there creates a second,
    throwaway loop; shared asyncio primitives -- the web transport's per-host
    semaphores -- bind to whichever loop contends first and then raise
    ``RuntimeError: bound to a different event loop`` for the rest of the
    process. Module-level ``main``/CLI entry points are the legitimate place to
    start a loop and are not covered here.
    """
    offenders: list[str] = []
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for member in node.body:
                if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if member.name.endswith("_sync_for_test"):
                    continue
                for inner in ast.walk(member):
                    if (
                        isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "run"
                        and isinstance(inner.func.value, ast.Name)
                        and inner.func.value.id == "asyncio"
                    ):
                        offenders.append(
                            f"{path.relative_to(ROOT).as_posix()}:{inner.lineno} "
                            f"in {node.name}.{member.name}"
                        )
    assert not offenders, (
        "await the coroutine instead of starting a nested event loop: " f"{offenders}"
    )


# ------------------------------------------------------- sqlite handle safety

def test_no_connection_is_used_as_a_bare_context_manager():
    """sqlite3.Connection.__exit__ commits or rolls back; it never closes.
    zero.sqlite_tx exists for exactly this and documents the hazard, but 17
    sites bypassed it and leaked a handle each -- including a per-row loop in
    the V1->V3 migration, and on Windows an open handle locks the database
    file against the rollback path."""
    offenders: list[str] = []
    for path in _python_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "with sqlite3.connect(" in stripped:
                offenders.append(f"{path.relative_to(ROOT).as_posix()}:{number}")
    assert not offenders, (
        "wrap the connection in zero.sqlite_tx.sqlite_txn so it is closed: "
        f"{offenders}"
    )


# -------------------------------------------------- listener task supervision

def test_listener_background_loops_are_referenced_and_shut_down():
    """A task started with bare create_task keeps only a weak reference in the
    loop, so it is garbage-collectable while suspended, and an escaping
    exception surfaces only as "Task exception was never retrieved" at
    interpreter shutdown."""
    source = _source_of("scripts/run_listener.py")
    tree = ast.parse(source)
    bare = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr in {"create_task", "ensure_future"}
        and isinstance(node.value.func.value, ast.Name)
        and node.value.func.value.id == "asyncio"
    ]
    assert not bare, f"discarded task handles at lines {bare}"
    assert "BACKGROUND_LOOP_CRASHED" in source, (
        "a background loop that dies must be logged, not lost"
    )
    assert "task.cancel()" in source and "run_until_disconnected" in source, (
        "the listener must cancel its background loops on the way out"
    )


# ------------------------------------------------------ failing template jobs

def _jobs_service(tmp_path: Path):
    from zero.config import ZeroConfig
    from zero.storage import ZeroStore
    from zero.template_jobs import TemplateJobService

    config = ZeroConfig.load(CONFIG_EXAMPLE)
    config = config.model_copy(update={
        "owner_user_id": 1,
        "memory": config.memory.model_copy(update={"db_path": str(tmp_path / "jobs.db")}),
    })
    return TemplateJobService(ZeroStore(config.memory.db_path), config)


def test_failing_job_moves_off_its_due_slot_and_does_not_redeliver(tmp_path: Path):
    """The failure path used to update cron_runs only. next_run_at stayed <= now,
    so the 30s coordinator loop re-selected the same job on every tick -- and if
    the failure happened after deliver() succeeded, re-sent the same digest to
    the group every 30 seconds."""
    async def scenario():
        from zero.sqlite_tx import sqlite_txn

        jobs = _jobs_service(tmp_path)
        draft = await jobs.create_draft(
            1, -100, 'reminder job', 'reminder', {'text': 'hydrate'},
            {'kind': 'interval', 'seconds': 60, 'explanation': 'every minute'},
        )
        await jobs.approve(1, draft['job_id'])
        async with jobs.store._lock:
            with sqlite_txn(jobs.store._conn()) as conn:
                conn.execute('UPDATE cron_jobs SET next_run_at=? WHERE job_id=?', (1, draft['job_id']))
                conn.commit()

        deliveries: list[str] = []

        async def deliver(job, result):
            deliveries.append(result)
            raise RuntimeError('telegram send failed after the message was accepted')

        await jobs.run_due(now=2, deliver=deliver)
        assert len(deliveries) == 1, 'the first attempt delivers once'
        state = await jobs.status(draft['job_id'])
        assert state['next_run_at'] > 2, (
            f"a failed run must advance next_run_at; got {state['next_run_at']}"
        )
        assert (await jobs.logs(draft['job_id']))[0]['state'] == 'failed'

        await jobs.run_due(now=2, deliver=deliver)
        assert len(deliveries) == 1, (
            'the job is no longer due, so the same digest must not be re-delivered'
        )

    asyncio.run(scenario())


# ----------------------------------------------------------- release scanners

def _fake_checkout(tmp_path: Path) -> Path:
    """A source tree plus a differently named virtualenv holding vendored code.

    The secret-shaped literals are assembled from fragments so this test file
    does not itself trip ``scripts/scan_secrets.py`` -- the same technique
    ``scripts/verify_public_artifact.py`` uses for its own marker list.
    """
    root = tmp_path / "checkout"
    (root / "zero").mkdir(parents=True)
    (root / "zero" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    venv = root / ".venv311w"
    (venv / "Lib" / "site-packages" / "rsa").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    vendored = "\n".join([
        "MARKER = '-----" + "BEGIN RSA PRIVATE" + " KEY-----'",
        "TOKEN = 'Bea" + "rer " + "z" * 30 + "'",
        "api" + "_key = '" + "abcdef0123456789abcdef" + "'",
        "",
    ])
    (venv / "Lib" / "site-packages" / "rsa" / "pem.py").write_text(vendored, encoding="utf-8")
    (root / ".ruff_cache").mkdir()
    (root / ".ruff_cache" / "CACHEDIR.TAG").write_text("0123456789abcdef" * 2 + "\n", encoding="utf-8")
    return root


def _run_scanner(script: str, target: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), str(target)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )


def test_secret_scan_ignores_vendored_dependency_source(tmp_path: Path):
    """CONTRIBUTING documents this command. Matching only the literal name
    ``.venv`` meant any other environment name -- and every tool cache -- was
    scanned as project source, so the gate failed on a clean checkout."""
    result = _run_scanner("scan_secrets.py", _fake_checkout(tmp_path))
    assert result.returncode == 0, f"scanner must ignore vendored source:\n{result.stdout}"
    assert ".venv311w" not in result.stdout
    assert ".ruff_cache" not in result.stdout


def test_secret_scan_still_reports_a_real_finding_in_project_source(tmp_path: Path):
    root = _fake_checkout(tmp_path)
    leak = "token = '" + "AKIA" + "ABCDEFGHIJKLMNOP" + "'\n"
    (root / "zero" / "leak.py").write_text(leak, encoding="utf-8")
    result = _run_scanner("scan_secrets.py", root)
    assert result.returncode == 1, "a real pattern in project source must still fail the gate"
    assert "zero/leak.py" in result.stdout


def test_public_artifact_scan_reports_a_venv_once_and_by_structure(tmp_path: Path):
    """A public artifact must not ship a virtualenv, but the scanner used to
    detect it only by name -- so ``.venv311w`` passed the path check while its
    vendored source was read and reported as project secret findings."""
    import json

    result = _run_scanner("verify_public_artifact.py", _fake_checkout(tmp_path))
    assert result.returncode == 1, "a virtualenv in the artifact must fail the gate"
    findings = json.loads(result.stdout)["findings"]
    assert findings == [{"category": "forbidden_path", "path": ".venv311w"}], (
        f"expected exactly one directory-level finding, got {findings}"
    )


def test_public_artifact_scan_passes_on_a_clean_tree(tmp_path: Path):
    import json

    root = tmp_path / "clean"
    (root / "zero").mkdir(parents=True)
    (root / "zero" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    result = _run_scanner("verify_public_artifact.py", root)
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)["finding_count"] == 0


# ------------------------------------------------------- bounded socket reuse

def test_connection_pool_bounds_the_hosts_that_hold_idle_sockets():
    """The idle pool was keyed by host with no eviction, so a listener that
    searched thousands of distinct result hosts kept an open keep-alive socket to
    every one of them until the peer reset it."""
    from zero.web_search.transport import ConnectionPoolTransport

    closed: list[str] = []

    class FakeConnection:
        def __init__(self, host: str) -> None:
            self.host = host

        def close(self) -> None:
            closed.append(self.host)

    transport = ConnectionPoolTransport(max_connections_per_host=2, max_idle_hosts=3)
    for index in range(6):
        host = f"host{index}.example"
        key = ("https", host, 443)
        pool = transport._acquire_pool(key)
        pool.put_nowait(FakeConnection(host))

    assert len(transport._idle) == 3, "the host map must stay bounded"
    assert closed == ["host0.example", "host1.example", "host2.example"], (
        f"evicted hosts must have their sockets closed, not leaked; closed={closed}"
    )
    assert transport.pool_size == 3


def test_connection_pool_never_holds_more_idle_sockets_than_its_concurrency_limit():
    from zero.web_search.transport import ConnectionPoolTransport

    transport = ConnectionPoolTransport(max_connections_per_host=2)
    pool = transport._acquire_pool(("https", "one.example", 443))
    assert pool.maxsize == 2


# -------------------------------------------------------- bounded search time

def test_every_web_search_branch_is_time_bounded():
    """Only the deep branch used to be wrapped in wait_for. A normal search could
    run for the sum of every provider budget while holding the listener's global
    message lock, freezing replies in every group."""
    source = _source_of("zero/brain.py")
    tree = ast.parse(source)
    bare_awaits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Name) and node.value.id == "run_search":
            bare_awaits.append(node.lineno)
    assert not bare_awaits, (
        f"the search coroutine must always be awaited through wait_for; bare await at {bare_awaits}"
    )
    assert "_SEARCH_TIMEOUT_SECONDS" in source and "_DEEP_SEARCH_TIMEOUT_SECONDS" in source, (
        "both search budgets must be named constants, not inline literals"
    )


# --------------------------------------------------------- bounded in-memory state

def test_telegram_search_state_expires_entries_that_are_never_read_again():
    """TTL used to be applied only on lookup, so states for conversations that
    were never revisited survived for the lifetime of the listener."""
    from zero.telegram_search import TelegramSearchConversationState

    class Req:
        def __init__(self, chat_id: int) -> None:
            self.chat_id = chat_id
            self.sender_id = 7
            self.thread_id = None
            self.reply_to_message_id = None
            self.trace_id = f"trace-{chat_id}"
            self.query = "q"
            self.intent = "i"

    state = TelegramSearchConversationState(ttl_seconds=300)
    for chat_id in range(5):
        state.save(Req(chat_id))
    assert len(state._data) == 5, "fresh entries are kept"
    # Age every entry past the TTL without waiting for wall-clock time.
    state._data = {key: (saved_at - 301, value) for key, (saved_at, value) in state._data.items()}
    state.save(Req(99))
    assert list(state._data) == [(99, 7, None, None)], (
        f"expired entries must be swept on write; kept {sorted(state._data)}"
    )


def test_vision_rate_limiter_prunes_its_cooldown_map():
    from zero.config import ZeroConfig
    from zero.vision import VisionRateLimiter

    config = ZeroConfig.load(CONFIG_EXAMPLE)
    limiter = VisionRateLimiter(config, store=None)
    limiter._last_request = {user_id: 0.0 for user_id in range(100)}
    limiter._clean_old(limiter._image_counts, now=10**9)
    assert limiter._last_request == {}, (
        "_last_request kept one entry per distinct sender forever"
    )
