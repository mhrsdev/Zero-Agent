from __future__ import annotations

import asyncio
import json
import random
from dataclasses import asdict
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from .fsprivacy import restrict_private_path
from .world_model import WorldModel

CATEGORIES = {
    "iran_news": "کانال تلگرام اخبار ایران",
    "world_news": "Telegram world news channels",
    "technology_ai": "Telegram AI technology channels",
    "programming": "Telegram programming Python Linux channels",
    "finance_crypto": "Telegram crypto finance channels",
    "science_space": "Telegram science space channels",
    "culture_books": "کانال تلگرام علمی فرهنگی کتاب",
}
PUBLIC_LINK = re.compile(r"https?://t\.me/(?:s/)?([A-Za-z0-9_]{4,})", re.I)


def extract_candidates(results: list[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for result in results:
        blob = " ".join(str(result.get(k, "")) for k in ("url", "title", "content", "snippet"))
        for username in PUBLIC_LINK.findall(blob):
            username = username.lower()
            if username in {"share", "joinchat", "addstickers"}:
                continue
            found.setdefault(username, {
                "username": username,
                "category": category,
                "title": str(result.get("title", ""))[:200],
                "snippet": str(result.get("content", result.get("snippet", "")))[:500],
                "source": "web_discovery",
            })
    return list(found.values())


def merge_manifest(existing: list[dict[str, Any]], discovered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {x["username"].lower(): dict(x) for x in existing if x.get("username")}
    for item in discovered:
        username = item["username"].lower()
        if username not in merged:
            merged[username] = dict(item)
        else:
            # Preserve the curated category/source, but keep fresh inspection metadata.
            merged[username].update({k: v for k, v in item.items() if k not in {"category", "source"}})
    return sorted(merged.values(), key=lambda x: (x.get("category", ""), x["username"]))


class TelegramSourceManager:
    """Bounded source discovery/join/inspection for the separate Telegram session.

    ponytail: one Telethon client per command; never overlap with the live search client.
    """

    def __init__(self, config, manifest_path: str | Path, state_path: str | Path):
        self.config = config
        self.manifest_path = Path(manifest_path)
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def load_manifest(self) -> list[dict[str, Any]]:
        if not self.manifest_path.exists():
            return []
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def save_manifest(self, rows: list[dict[str, Any]]) -> None:
        self.manifest_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        restrict_private_path(self.manifest_path)

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"joined": {}, "attempts": []}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save_state(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        restrict_private_path(self.state_path)

    async def discover_web(self, web, *, per_category: int = 25) -> list[dict[str, Any]]:
        rows = self.load_manifest()
        discovered = []
        for category, query in CATEGORIES.items():
            outcome = await web.run(query, trace_id="tg-source-discovery")
            results = [asdict(x) for x in outcome.results[:per_category]]
            if not results:
                # Bounded local SearXNG fallback; discovery only, never page analysis.
                base = getattr(getattr(web, "config", None), "web", None)
                endpoint = getattr(base, "searxng_base_url", "") or "http://127.0.0.1:8888"
                try:
                    url = endpoint.rstrip("/") + "/search?" + urlencode({"q": query, "format": "json"})
                    with urlopen(url, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    results = payload.get("results", [])[:per_category]
                except Exception:
                    results = []
            discovered.extend(extract_candidates(results, category))
        rows = merge_manifest(rows, discovered)
        self.save_manifest(rows)
        return rows

    async def inspect(self, usernames: list[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
        from telethon import TelegramClient
        cfg = self.config.telegram_search
        client = TelegramClient(cfg.session_path, cfg.api_id, cfg.api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError("unauthorized_session")
            wanted = {x.lower().lstrip("@") for x in (usernames or [])}
            rows = self.load_manifest()
            output = []
            for row in rows:
                if wanted and row["username"] not in wanted:
                    continue
                if len(output) >= limit:
                    break
                try:
                    entity = await client.get_entity(row["username"])
                    samples = []
                    async for message in client.iter_messages(entity, limit=3):
                        if getattr(message, "message", None):
                            samples.append(message.message[:300])
                    kind = "group" if getattr(entity, "megagroup", False) or getattr(entity, "broadcast", False) is False else "channel"
                    output.append({**row, "title": getattr(entity, "title", "") or row["title"], "kind": kind, "public": bool(getattr(entity, "username", None)), "member_count": getattr(entity, "participants_count", None), "samples": samples, "inspect_status": "ok"})
                except Exception as exc:
                    output.append({**row, "inspect_status": "failed", "error": type(exc).__name__})
            self.save_manifest(merge_manifest(rows, output))
            return output
        finally:
            await client.disconnect()

    async def join_due(self, *, max_per_window: int = 4, window_seconds: int = 600, daily_limit: int = 50, max_total: int = 150) -> dict[str, Any]:
        from telethon import TelegramClient
        from telethon.errors import FloodWaitError, UserAlreadyParticipantError, ChannelPrivateError, ChannelInvalidError
        from telethon.tl.functions.channels import JoinChannelRequest
        state = self.load_state()
        now = int(time.time())
        state["attempts"] = [x for x in state.get("attempts", []) if now - int(x.get("at", 0)) < 86400]
        recent = [x for x in state["attempts"] if now - int(x["at"]) < window_seconds]
        if len(recent) >= max_per_window or len(state["attempts"]) >= daily_limit:
            self.save_state(state)
            return {"status": "limit", "joined": [], "reason": "window_or_daily_limit"}
        cfg = self.config.telegram_search
        client = TelegramClient(cfg.session_path, cfg.api_id, cfg.api_hash)
        await client.connect()
        joined, skipped = [], []
        try:
            if not await client.is_user_authorized():
                return {"status": "blocked", "reason": "unauthorized_session"}
            rows = self.load_manifest()
            for row in rows:
                username = row["username"]
                if len(state.get("joined", {})) >= max_total or len(joined) >= max_per_window or len(state["attempts"]) >= daily_limit:
                    break
                if username in state.get("joined", {}):
                    continue
                try:
                    entity = await client.get_entity(username)
                    if not getattr(entity, "username", None):
                        skipped.append({"username": username, "reason": "not_public"}); continue
                    await client(JoinChannelRequest(entity))
                    state.setdefault("joined", {})[username] = {"at": now, "kind": "group" if getattr(entity, "megagroup", False) else "channel", "category": row.get("category", "")}
                    state["attempts"].append({"username": username, "at": now})
                    joined.append(username)
                    await asyncio.sleep(random.randint(90, 180))
                except UserAlreadyParticipantError:
                    state.setdefault("joined", {})[username] = {"at": now, "already": True, "category": row.get("category", "")}
                except (ChannelPrivateError, ChannelInvalidError) as exc:
                    skipped.append({"username": username, "reason": type(exc).__name__})
                except FloodWaitError as exc:
                    self.save_state(state)
                    return {"status": "flood_wait", "seconds": int(exc.seconds), "joined": joined, "skipped": skipped}
                except Exception as exc:
                    skipped.append({"username": username, "reason": type(exc).__name__})
            self.save_state(state)
            return {"status": "ok", "joined": joined, "skipped": skipped, "daily_used": len(state["attempts"]), "total_joined": len(state.get("joined", {}))}
        finally:
            await client.disconnect()

    async def digest(self, *, limit_per_source: int = 5) -> dict[str, Any]:
        from telethon import TelegramClient
        state = self.load_state()
        cfg = self.config.telegram_search
        client = TelegramClient(cfg.session_path, cfg.api_id, cfg.api_hash)
        await client.connect()
        model = WorldModel(self.config.memory.db_path)
        count = 0
        try:
            if not await client.is_user_authorized():
                return {"status": "blocked", "reason": "unauthorized_session"}
            for username, meta in state.get("joined", {}).items():
                try:
                    entity = await client.get_entity(username)
                    snippets = []
                    async for message in client.iter_messages(entity, limit=limit_per_source):
                        if getattr(message, "message", None): snippets.append(message.message[:350])
                    if not snippets: continue
                    model.entity(username, "telegram_source", {"category": meta.get("category", ""), "title": getattr(entity, "title", username), "digest": "\n".join(snippets), "updated_at": int(time.time()), "evidence": [f"https://t.me/{username}"]})
                    count += 1
                except Exception:
                    continue
            return {"status": "ok", "sources_injected": count}
        finally:
            await client.disconnect()
