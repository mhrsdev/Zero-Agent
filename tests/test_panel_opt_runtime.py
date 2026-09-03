"""Panel runtime regressions: blocking I/O on the loop, per-client polling, unbounded state.

Each test reproduces a defect measured on the running panel:

* ``panel_store.get_session`` ran synchronous sqlite on the event loop for every
  authenticated request (0.44ms measured, unbounded under lock contention);
* ``/api/settings`` stat-ed and DACL-inspected two secret files on the loop;
* ``/api/logs`` and both SSE endpoints read up to 1 MiB of four log files on the
  loop -- once per connected browser tab per 2s/5s tick, for identical data;
* the login-code map and the rate-limit window map grew without bound, and the
  ``panel_sessions`` table was never swept.

The loop-stall assertions inject a *synchronous* sleep into the blocking call and
measure the longest gap between event-loop ticks while one request runs. A gap
near the injected sleep proves the work ran on the loop; a small gap proves it
did not. Windows' timer granularity is ~15ms, so the threshold sits well above
it and well below the injected sleep.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from zero import panel_api as panel_api_module
from zero.panel_api import PanelAPI
from zero.panel_store import PanelStore
from zero.sqlite_tx import sqlite_txn

BLOCK_SECONDS = 0.25
MAX_LOOP_GAP = 0.12
PASSWORD = "pw" + "x" * 10


async def _max_loop_gap(awaitable):
    """Return ``(result, longest observed pause between loop ticks)``."""
    gaps: list[float] = []

    async def monitor():
        last = time.perf_counter()
        while True:
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    monitor_task = asyncio.ensure_future(monitor())
    await asyncio.sleep(0.02)
    try:
        result = await awaitable
    finally:
        monitor_task.cancel()
        await asyncio.wait({monitor_task})
    return result, max(gaps, default=0.0)


class FakeBot:
    """Records outgoing messages; ``fail_after`` makes later sends raise."""

    def __init__(self, fail_after: int | None = None):
        self.messages: list[tuple[int, str]] = []
        self.fail_after = fail_after

    async def send_message(self, user_id, text):
        if self.fail_after is not None and len(self.messages) >= self.fail_after:
            raise RuntimeError("telegram transport rejected the notification")
        self.messages.append((user_id, text))


class FakeRouter:
    def status(self):
        return {"providers": {"openrouter": {"model": "safe/model", "keys": []}}}


class FakeStore:
    """Only the async surface these panel routes touch."""

    async def panel_get_settings(self, keys):
        return {}

    async def set_setting(self, key, value):
        return None


def build_config(tmp_path: Path):
    log = tmp_path / "panel.log"
    log.write_text("ERROR trace_id=trace-ok token=test-placeholder\n", encoding="utf-8")
    token_file = tmp_path / "bot.env"
    token_file.write_text("configured", encoding="utf-8")
    return SimpleNamespace(
        owner_user_id=111111111, owner_username="owner",
        panel_viewer_usernames=["viewer1"], panel_viewer_user_ids=[222222222],
        memory=SimpleNamespace(db_path=str(tmp_path / "zero.db"), recent_messages_limit=10, long_term_limit=10),
        router=SimpleNamespace(normal_primary="openrouter", normal_fallback="gemini", strategy="weighted_lru",
                               search_provider="gemini", providers=SimpleNamespace(openrouter=SimpleNamespace(quota_scope="project"))),
        web=SimpleNamespace(enabled=True, google_grounding_enabled=True),
        listener=SimpleNamespace(account_username="zero", allowed_group_usernames=["safe"]),
        policy=SimpleNamespace(model_dump=lambda: {}), reactions=SimpleNamespace(model_dump=lambda: {}),
        stickers=SimpleNamespace(model_dump=lambda: {}),
        management_bot=SimpleNamespace(token_file=str(token_file)),
        logs=SimpleNamespace(listener_log=str(log), panel_log=str(log), router_log=str(log)),
    )


def build_panel(tmp_path: Path, *, bot=None):
    config = build_config(tmp_path)
    panel_store = PanelStore(tmp_path / "panel.db")
    api = PanelAPI(config, FakeStore(), FakeRouter(), bot or FakeBot(), static_dir=tmp_path, panel_store=panel_store)
    return api, config, panel_store


@pytest.fixture
async def panel(tmp_path):
    api, config, panel_store = build_panel(tmp_path)
    client = TestClient(TestServer(api.app))
    await client.start_server()
    yield SimpleNamespace(client=client, api=api, store=panel_store, config=config)
    await client.close()
    await api.stop()


async def login(panel) -> dict[str, str]:
    panel.store.create_admin("admin", PASSWORD)
    response = await panel.client.post("/api/local/auth/login", json={"username": "admin", "password": PASSWORD})
    assert response.status == 200
    body = await response.json()
    return {
        "Cookie": f"zero_admin_session={response.cookies['zero_admin_session'].value}",
        "X-CSRF-Token": body["csrf"],
    }


# ------------------------------------------------------- blocking work off the loop

async def test_session_lookup_does_not_block_the_event_loop(panel, monkeypatch):
    headers = await login(panel)
    authoritative = panel.store.get_session

    def slow(token):
        time.sleep(BLOCK_SECONDS)
        return authoritative(token)

    monkeypatch.setattr(panel.store, "get_session", slow)
    response, gap = await _max_loop_gap(panel.client.get("/api/local/auth/me", headers=headers))

    assert response.status == 200
    assert gap < MAX_LOOP_GAP, f"the session lookup blocked the loop for {gap * 1000:.0f}ms"


async def test_secret_status_does_not_block_the_event_loop(panel, monkeypatch):
    headers = await login(panel)
    monkeypatch.setattr(panel_api_module, "path_is_private", lambda path: (time.sleep(BLOCK_SECONDS), True)[1])
    response, gap = await _max_loop_gap(panel.client.get("/api/settings", headers=headers))

    assert response.status == 200
    assert (await response.json())["secrets"]["management_bot"]["configured"] is True
    assert gap < MAX_LOOP_GAP, f"the secret stat blocked the loop for {gap * 1000:.0f}ms"


async def test_log_read_does_not_block_the_event_loop(panel, monkeypatch):
    headers = await login(panel)
    monkeypatch.setattr(panel_api_module, "_tail_lines",
                        lambda *args, **kwargs: (time.sleep(BLOCK_SECONDS), ["ERROR sample line"])[1])
    response, gap = await _max_loop_gap(panel.client.get("/api/logs", headers=headers))

    assert response.status == 200
    assert (await response.json())["items"], "the tail must still be reported"
    assert gap < MAX_LOOP_GAP, f"the log tail blocked the loop for {gap * 1000:.0f}ms"


async def test_host_metrics_are_sampled_off_the_event_loop(panel, monkeypatch):
    headers = await login(panel)
    monkeypatch.setattr(panel_api_module, "listener_status",
                        lambda: (time.sleep(BLOCK_SECONDS), {"running": False, "pid": 0, "state": "stopped"})[1])
    response, gap = await _max_loop_gap(panel.client.get("/api/local/dashboard", headers=headers))

    assert response.status == 200
    assert gap < MAX_LOOP_GAP, f"the host sample blocked the loop for {gap * 1000:.0f}ms"


# --------------------------------------------------- one sample per interval, not per client

async def test_concurrent_dashboard_readers_sample_the_host_once(panel, monkeypatch):
    headers = await login(panel)
    samples: list[float] = []

    def counting():
        samples.append(time.monotonic())
        time.sleep(0.05)
        return {"running": False, "pid": 0, "state": "stopped"}

    monkeypatch.setattr(panel_api_module, "listener_status", counting)
    responses = await asyncio.gather(*[panel.client.get("/api/local/dashboard", headers=headers) for _ in range(5)])
    payloads = [await response.json() for response in responses]

    assert all(response.status == 200 for response in responses)
    assert len(samples) == 1, f"5 concurrent readers sampled the host {len(samples)} times"
    assert len({payload["sampled_at"] for payload in payloads}) == 1, "every reader must see one shared sample"


async def test_two_log_stream_clients_share_one_error_log_read(panel, monkeypatch):
    headers = await login(panel)
    reads: list[dict] = []
    authoritative = panel.api._read_logs

    def counting(**kwargs):
        reads.append(kwargs)
        return authoritative(**kwargs)

    monkeypatch.setattr(panel.api, "_read_logs", counting)
    streams = [await panel.client.get("/api/logs/stream", headers=headers) for _ in range(2)]
    try:
        for stream in streams:
            assert stream.status == 200
            assert await stream.content.readany()
        assert len(reads) == 1, f"two connected tabs read the log files {len(reads)} times"
    finally:
        for stream in streams:
            stream.close()


# ----------------------------------------------------------------- bounded in-memory state

async def test_expired_login_codes_are_swept(panel):
    stale = time.time() - 600
    panel.api.pending = {
        str(index): {"hash": "x", "sent": stale, "expires": stale + 120, "attempts": 0}
        for index in range(300)
    }

    response = await panel.client.post("/api/auth/request", json={"identity": str(panel.config.owner_user_id)})

    assert response.status == 200
    assert list(panel.api.pending) == [str(panel.config.owner_user_id)], (
        f"expired login codes survived; kept {len(panel.api.pending)} entries"
    )


async def test_idle_rate_limit_windows_are_reclaimed(panel):
    stale = time.time() - 600
    panel.api._request_hits = {f"198.51.100.{index}:api": deque([stale]) for index in range(300)}

    response = await panel.client.get("/api/health")

    assert response.status == 200
    assert len(panel.api._request_hits) <= 2, (
        f"windows whose last hit aged out were not reclaimed; kept {len(panel.api._request_hits)}"
    )


def _session_rows(store: PanelStore) -> int:
    with sqlite_txn(store._connect()) as db:
        return int(db.execute("SELECT COUNT(*) FROM panel_sessions").fetchone()[0])


def test_expired_panel_sessions_are_deleted_from_the_table(tmp_path):
    """An installation that has been logging in for months accumulated one dead row per login."""
    store = PanelStore(tmp_path / "panel.db")
    admin_id = store.create_admin("admin", PASSWORD)
    with sqlite_txn(store._connect()) as db:
        for index in range(3):
            db.execute("INSERT INTO panel_sessions VALUES(?,?,?,?,?)", (f"expired-{index}", admin_id, "csrf", 0, 1))
    assert _session_rows(store) == 3

    live_token, _csrf = store.create_session(admin_id)

    assert _session_rows(store) == 1, "expired session rows survived the next login"
    assert store.get_session(live_token) is not None, "the live session must be the row that remains"


# ------------------------------------------------------------------ real socket lifecycle

async def test_real_socket_lifecycle_releases_the_port_and_the_shared_samples(tmp_path):
    """``stop()`` must release the listening socket and leave no sampling task behind."""
    import aiohttp

    api, _config, _store = build_panel(tmp_path)
    runner = await api.start(host="127.0.0.1", port=0)
    host, port = runner.addresses[0][:2]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://{host}:{port}/api/health") as health:
                assert health.status == 200
            async with session.get(f"http://{host}:{port}/api/logs") as unauthenticated:
                assert unauthenticated.status == 401
    finally:
        await api.stop()

    assert api.is_sampling is False, "a shared sample was still refreshing after shutdown"
    with pytest.raises(OSError):
        await asyncio.open_connection(host, port)


async def test_stop_does_not_wait_out_an_open_event_stream(tmp_path):
    """AppRunner.cleanup() waits for connections; a sleeping SSE handler held one.

    Measured against the previous implementation: 59,974ms for one open tab,
    which is the whole graceful shutdown timeout.
    """
    import aiohttp

    api, _config, store = build_panel(tmp_path)
    store.create_admin("admin", PASSWORD)
    runner = await api.start(host="127.0.0.1", port=0)
    host, port = runner.addresses[0][:2]
    session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
    try:
        login = await session.post(f"http://{host}:{port}/api/local/auth/login",
                                   json={"username": "admin", "password": PASSWORD})
        assert login.status == 200
        stream = await session.get(f"http://{host}:{port}/api/logs/stream")
        assert stream.status == 200
        assert await asyncio.wait_for(stream.content.readany(), timeout=10)
        started = time.perf_counter()
        await api.stop()
        elapsed = time.perf_counter() - started
        stream.close()
    finally:
        await session.close()

    assert elapsed < 5.0, f"shutdown waited {elapsed:.1f}s for an idle event stream"
