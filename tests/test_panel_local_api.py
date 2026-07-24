from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from zero.panel_api import PanelAPI
from zero.panel_store import PanelStore


class Bot:
    async def send_message(self, *_args, **_kwargs):
        return None


class Router:
    def status(self):
        return {"providers": {}}


def config(tmp_path):
    log = tmp_path / "panel.log"
    log.write_text("", encoding="utf-8")
    return SimpleNamespace(
        owner_user_id=1,
        owner_username="owner",
        panel_viewer_usernames=[],
        panel_viewer_user_ids=[],
        memory=SimpleNamespace(db_path=str(tmp_path / "zero.db"), recent_messages_limit=10, long_term_limit=10),
        router=SimpleNamespace(normal_primary="", normal_fallback="", strategy="", search_provider="", providers=SimpleNamespace()),
        web=SimpleNamespace(enabled=False, google_grounding_enabled=False),
        telegram_search=SimpleNamespace(archived=True),
        listener=SimpleNamespace(account_username="", allowed_group_usernames=[]),
        policy=SimpleNamespace(model_dump=lambda: {}), reactions=SimpleNamespace(model_dump=lambda: {}), stickers=SimpleNamespace(model_dump=lambda: {}),
        management_bot=SimpleNamespace(token_file=str(tmp_path / "bot")),
        logs=SimpleNamespace(listener_log=str(log), panel_log=str(log), router_log=str(log)),
    )


@pytest.mark.asyncio
async def test_local_admin_auth_and_setup_api(tmp_path: Path):
    cfg = config(tmp_path)
    panel = PanelAPI(cfg, object(), Router(), Bot(), static_dir=tmp_path, panel_store=PanelStore(tmp_path / "panel.db"))
    client = TestClient(TestServer(panel.app))
    await client.start_server()
    try:
        response = await client.post("/api/local/auth/bootstrap", json={"username": "admin", "password": "correct horse battery staple"})
        assert response.status == 201
        response = await client.post("/api/local/auth/login", json={"username": "admin", "password": "correct horse battery staple"})
        assert response.status == 200
        body = await response.json()
        headers = {"X-CSRF-Token": body["csrf"], "Cookie": f"zero_admin_session={response.cookies['zero_admin_session'].value}"}
        assert (await client.get("/api/local/setup", headers=headers)).status == 200
        assert (await client.get("/api/router", headers=headers)).status == 200
        assert (await client.get("/api/logs", headers=headers)).status == 200
        saved = await client.post("/api/local/setup/telegram", headers=headers, json={"mode": "bot", "bot_token": "secret"})
        assert saved.status == 200
        state = await (await client.get("/api/local/setup", headers=headers)).json()
        assert state["data"]["telegram"]["bot_token"] == "[stored securely]"
        assert (await client.post("/api/local/auth/logout", headers=headers)).status == 200
    finally:
        await client.close()
