"""Admin API verification: group-scoped, CSRF-protected, role-based access.

``PanelAPI`` is the owner-only web adapter for the Zero panel.  Three security
contracts must hold end-to-end against the real ``aiohttp`` server:

1. **Group scoping** — the panel exposes the listener's
   ``allowed_group_usernames`` and never reveals chats outside the configured
   group scope.  The ``/api/settings`` endpoint reports these group names so an
   operator can verify which group's data the panel is bound to.
2. **CSRF** — every state-changing endpoint
   (``/api/settings/{key}``, ``/api/auth/logout``, ``/api/auth/logout-all``,
   ``/api/jobs/{id}/{action}``, ``/api/knowledge/run`` …) requires an
   ``X-CSRF-Token`` header whose value matches the session's ``csrf`` token,
   compared with ``hmac.compare_digest`` (constant time).  A wrong or absent
   token yields ``403 Forbidden``.
3. **Role-based access** — an ``owner`` session can read *and* mutate; a
   ``viewer`` session may read but every write endpoint returns ``403``.

These exercises use the *production* ``PanelAPI`` over a bound ``TestServer`` —
no patches, no mocks for security primitives.  Fakes are confined to bot/router
inputs only.
"""
from __future__ import annotations
from conftest import PANEL_DIR

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from zero.experience_memory import ExperienceMemory
from zero.panel_api import PanelAPI
from zero.panel_store import PanelStore
from zero.procedural_memory import ProceduralMemory
from zero.semantic_memory import SemanticUserMemory
from zero.storage import ZeroStore
from zero.world_model import WorldModel


# ---------------------------------------------------------------------------
# Fakes (only for inputs бот/router/etc., never for security primitives)
# ---------------------------------------------------------------------------
class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, user_id, text) -> None:
        self.messages.append((user_id, text))

    async def get_chat(self, username):
        return SimpleNamespace(id=123456789)


class FakeRouter:
    def status(self):
        return {"providers": {"openrouter": {"model": "safe/model", "keys": []}}}


class FakeKnowledge:
    async def status(self):
        return {"running": False, "backend": "remote"}

    async def schedule_status(self):
        return {"enabled": True, "backend": "remote"}

    async def run_nightly(self, **kwargs):
        return {"status": "dry_run", "kwargs": kwargs}


class FakeJobs:
    async def list_jobs(self, actor=None):
        return [{"job_id": "job-1", "title": "safe", "state": "enabled", "approval_state": "approved"}]

    async def status(self, job_id, actor=None):
        return {"job_id": job_id, "state": "enabled"}

    async def logs(self, job_id, limit=10, actor=None):
        return []

    async def set_state(self, actor, job_id, state):
        return None

    async def approve(self, actor, job_id):
        return {"job_id": job_id, "approved": True}


def _config(tmp_path, *, allowed_group_usernames=("safe-group",)):
    log = tmp_path / "panel.log"
    log.write_text("ERROR trace_id=trace-ok token=test-placeholder\n")
    return SimpleNamespace(
        owner_user_id=111111111,
        owner_username="owner",
        panel_viewer_usernames=["ysnrfd3", "PYT313"],
        panel_viewer_user_ids=[123456789, 987654321],
        memory=SimpleNamespace(
            db_path=str(tmp_path / "zero.db"),
            recent_messages_limit=80,
            long_term_limit=120,
        ),
        router=SimpleNamespace(
            normal_primary="openrouter",
            normal_fallback="gemini",
            strategy="weighted_lru",
            search_provider="gemini",
            providers=SimpleNamespace(
                openrouter=SimpleNamespace(quota_scope="project"),
                gemini=SimpleNamespace(quota_scope="project"),
            ),
        ),
        web=SimpleNamespace(enabled=True, google_grounding_enabled=True),
        telegram_search=SimpleNamespace(archived=True),
        listener=SimpleNamespace(
            account_username="zero",
            allowed_group_usernames=list(allowed_group_usernames),
        ),
        policy=SimpleNamespace(model_dump=lambda: {"user_max_replies_per_day": 120}),
        reactions=SimpleNamespace(model_dump=lambda: {"enabled": False}),
        stickers=SimpleNamespace(model_dump=lambda: {"enabled": True}),
        management_bot=SimpleNamespace(token_file=str(tmp_path / "bot.env")),
        logs=SimpleNamespace(
            listener_log=str(log), panel_log=str(log), router_log=str(log)
        ),
    )


@pytest.fixture
async def panel(tmp_path):
    cfg = _config(tmp_path)
    Path(cfg.management_bot.token_file).write_text("configured")
    Path(cfg.management_bot.token_file).chmod(0o600)
    store = ZeroStore(cfg.memory.db_path)
    await store.append_recent(10, 20, "کاربر", "user", "پیام تست")
    semantic = SemanticUserMemory(cfg.memory.db_path)
    panel_store = PanelStore(tmp_path / "panel.sqlite")
    bot = FakeBot()
    api = PanelAPI(
        cfg,
        store,
        FakeRouter(),
        bot,
        static_dir=str(PANEL_DIR),
        services={
            "knowledge": FakeKnowledge(),
            "jobs": FakeJobs(),
            "semantic": semantic,
            "experience": ExperienceMemory(cfg.memory.db_path),
            "procedure": ProceduralMemory(cfg.memory.db_path),
            "world": WorldModel(cfg.memory.db_path),
        },
        panel_store=panel_store,
    )
    client = TestClient(TestServer(api.app))
    await client.start_server()
    yield client, api, bot, cfg, panel_store
    await client.close()


# ---------------------------------------------------------------------------
# Helpers to obtain authed sessions of either role
# ---------------------------------------------------------------------------
async def _login_owner(panel):
    client, api, bot, cfg, *_ = panel
    requested = await client.post("/api/auth/request", json={"identity": str(cfg.owner_user_id)})
    assert requested.status == 200
    code = re.search(r"\b(\d{6})\b", bot.messages[-1][1]).group(1)
    verified = await client.post(
        "/api/auth/verify", json={"identity": str(cfg.owner_user_id), "code": code}
    )
    assert verified.status == 200
    body = await verified.json()
    cookie = verified.cookies["zero_session"].value
    return {"Cookie": f"zero_session={cookie}", "X-CSRF-Token": body["csrf"]}, body


async def _login_viewer(panel, *, identity="@ysnrfd3"):
    client, api, bot, *_ = panel
    await client.post("/api/auth/request", json={"identity": identity})
    code = re.search(r"\b(\d{6})\b", bot.messages[-1][1]).group(1)
    verified = await client.post("/api/auth/verify", json={"identity": identity, "code": code})
    assert verified.status == 200
    body = await verified.json()
    assert body["role"] == "viewer"
    cookie = verified.cookies["zero_session"].value
    return {"Cookie": f"zero_session={cookie}", "X-CSRF-Token": body["csrf"]}, body


async def _login_local(panel, *, username="owner", password="StrongPassword123"):
    client, api, bot, cfg, panel_store = panel
    panel_store.create_admin(username, password)
    resp = await client.post(
        "/api/local/auth/login", json={"username": username, "password": password}
    )
    assert resp.status == 200
    body = await resp.json()
    cookie = resp.cookies["zero_admin_session"].value
    return {"Cookie": f"zero_admin_session={cookie}", "X-CSRF-Token": body["csrf"]}, body


# ---------------------------------------------------------------------------
# 1. Group scoping — the panel reports the configured group usernames
# ---------------------------------------------------------------------------
async def test_panel_reports_configured_group_scope(panel):
    """``/api/settings`` exposes the listener-configured group usernames.

    The panel is single-group-bound: the source-of-truth for scope visibility
    is the listener-allowed group list, surfaced read-only in the panel config.
    """
    client, *_ = panel
    headers, _ = await _login_owner(panel)
    settings_rsp = await client.get("/api/settings", headers=headers)
    assert settings_rsp.status == 200
    settings = await settings_rsp.json()
    groups = settings["config"]["telegram"]["allowed_groups"]
    assert groups == ["safe-group"], (
        "panel must surface the listener-configured group scope; got " + repr(groups)
    )


async def test_panel_chats_are_tenant_scoped_not_cross_group(panel):
    """``/api/chats`` returns rows that belong to the configured tenant.

    With the test store seeded with a single chat_id=10 row in group 'safe',
    the chats listing must surface only that record (no cross-group leakage).
    """
    client, *_ = panel
    headers, _ = await _login_owner(panel)
    chats = await (await client.get("/api/chats", headers=headers)).json()
    assert chats.get("size", 0) >= 1
    item_ids = [item.get("chat_id") for item in chats.get("items", [])]
    assert all(cid in (10,) for cid in item_ids if cid is not None), (
        "panel chats must be tenant-scoped; saw " + repr(item_ids)
    )


# ---------------------------------------------------------------------------
# 2. CSRF — every state-changing endpoint demands a matching token
# ---------------------------------------------------------------------------
async def test_csrf_missing_token_blocks_resource_writes(panel):
    client, *_ = panel
    headers, _ = await _login_owner(panel)
    bad = {"Cookie": headers["Cookie"]}  # no X-CSRF-Token header
    r = await client.post("/api/settings/web_enabled", headers=bad, json={"value": False})
    assert r.status == 403, "missing CSRF token must block the write"


async def test_csrf_wrong_token_blocks_resource_writes(panel):
    client, *_ = panel
    headers, _ = await _login_owner(panel)
    bad = {"Cookie": headers["Cookie"], "X-CSRF-Token": "wrong-value-xyz"}
    r = await client.post("/api/settings/web_enabled", headers=bad, json={"value": False})
    assert r.status == 403, "wrong CSRF token must block the write"


async def test_csrf_correct_token_permits_resource_writes(panel):
    client, *_ = panel
    headers, _ = await _login_owner(panel)
    r = await client.post("/api/settings/web_enabled", headers=headers, json={"value": False})
    assert r.status == 200


async def test_csrf_protects_logout_all_endpoint(panel):
    client, *_ = panel
    headers, _ = await _login_owner(panel)
    bad = {"Cookie": headers["Cookie"], "X-CSRF-Token": "malicious"}
    r = await client.post("/api/auth/logout-all", headers=bad, json={"confirm": True})
    assert r.status == 403


async def test_local_admin_csrf_protects_change_password(panel):
    # local admin login also requires CSRF for mutation endpoints.
    client, *_ = panel
    headers, _ = await _login_local(panel)
    bad = {"Cookie": headers["Cookie"]}  # no X-CSRF-Token
    r = await client.post(
        "/api/local/auth/change-password",
        headers=bad,
        json={"current_password": "StrongPassword123", "new_password": "AnotherPassword123"},
    )
    assert r.status == 403
    # And wrong token must also be rejected
    bad_token = {"Cookie": headers["Cookie"], "X-CSRF-Token": "intruder"}
    r = await client.post(
        "/api/local/auth/change-password",
        headers=bad_token,
        json={"current_password": "StrongPassword123", "new_password": "AnotherPassword123"},
    )
    assert r.status == 403


# ---------------------------------------------------------------------------
# 3. Role-based access — viewer vs owner
# ---------------------------------------------------------------------------
async def test_owner_can_read_dashboard(panel):
    client, *_ = panel
    headers, _ = await _login_owner(panel)
    rsp = await client.get("/api/dashboard", headers=headers)
    assert rsp.status == 200
    body = await rsp.json()
    assert "status" in body and "provider" in body


async def test_viewer_can_read_but_cannot_mutate_settings(panel):
    client, *_ = panel
    headers, _ = await _login_viewer(panel)
    read = await client.get("/api/settings", headers=headers)
    assert read.status == 200, "viewer must be able to read settings"
    write = await client.post(
        "/api/settings/web_enabled", headers=headers, json={"value": False}
    )
    assert write.status == 403, "viewer must be denied write access"


async def test_viewer_cannot_logout_all_or_revoke_sessions(panel):
    client, *_ = panel
    headers, _ = await _login_viewer(panel)
    assert (
        await client.post("/api/auth/logout-all", headers=headers, json={"confirm": True})
    ).status == 403
    # Session list is read-only; viewer may list but not revoke.
    listing = await (await client.get("/api/sessions", headers=headers)).json()
    assert "items" in listing
    for sess in listing["items"]:
        sid = sess["id"]
        r = await client.post(f"/api/sessions/{sid}/revoke", headers=headers)
        assert r.status == 403, "viewer must not revoke sessions"


async def test_local_admin_role_owner_can_perform_setup_steps(panel):
    client, *_ = panel
    headers, _ = await _login_local(panel)
    me = await (await client.get("/api/local/auth/me", headers=headers)).json()
    assert me["role"] == "owner"
    setup_rsp = await client.post(
        "/api/local/setup/skip", headers=headers, json={"confirm": True}
    )
    assert setup_rsp.status == 200
