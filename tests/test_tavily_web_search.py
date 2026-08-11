from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import CONFIG_EXAMPLE
from zero.config import ZeroConfig
from zero.web import HybridWeb
from zero.web_search.models import QueryPlan, SearchIntent, SearchKind, SearchOutcome
from zero.web_search.providers.tavily import TavilyProvider


class RecordingTransport:
    def __init__(self, response: dict):
        self.response = response
        self.calls: list[dict] = []

    async def post_json(self, url, payload, timeout, max_bytes, *, headers=None):
        self.calls.append({
            "url": url,
            "payload": payload,
            "timeout": timeout,
            "max_bytes": max_bytes,
            "headers": dict(headers or {}),
        })
        return json.dumps(self.response)


@pytest.mark.asyncio
async def test_tavily_uses_bearer_header_and_parses_bounded_official_api_results():
    secret = "test-secret-never-log"
    transport = RecordingTransport({
        "results": [
            {
                "title": "First result",
                "url": "https://example.com/first",
                "content": "First snippet",
                "score": 0.91,
            },
            {
                "title": "Second result",
                "url": "https://docs.example.org/second",
                "content": "Second snippet",
                "score": 0.82,
            },
            {
                "title": "Overflow result",
                "url": "https://overflow.example/third",
                "content": "Must be capped",
                "score": 0.5,
            },
        ]
    })
    provider = TavilyProvider(
        secret,
        transport,
        max_results=2,
        timeout=7,
    )

    results = await provider.search(QueryPlan(
        original="latest zero agent news",
        query="latest zero agent news",
        language="en",
    ))

    assert len(results) == 2
    assert [result.provider for result in results] == ["tavily", "tavily"]
    assert results[0].title == "First result"
    assert results[0].snippet == "First snippet"
    assert results[0].publisher == "example.com"
    assert results[0].metadata["tavily_score"] == 0.91

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == "https://api.tavily.com/search"
    assert call["headers"] == {"Authorization": f"Bearer {secret}"}
    assert call["payload"] == {
        "query": "latest zero agent news",
        "search_depth": "basic",
        "max_results": 2,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }
    assert secret not in call["url"]
    assert secret not in json.dumps(call["payload"])


@pytest.mark.asyncio
async def test_connection_pool_transport_posts_json_with_custom_headers():
    received: dict = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            received["path"] = self.path
            received["authorization"] = self.headers.get("Authorization")
            received["content_type"] = self.headers.get("Content-Type")
            received["payload"] = json.loads(self.rfile.read(length))
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = str(server.server_address[0])
    port = int(server.server_address[1])

    from zero.web_search.transport import ConnectionPoolTransport

    transport = ConnectionPoolTransport(
        allowed_private_endpoints={("http", host, port)},
    )
    try:
        response = await transport.post_json(
            f"http://{host}:{port}/search",
            {"query": "test"},
            2,
            1024,
            headers={"Authorization": "Bearer unit-token"},
        )
    finally:
        transport.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert json.loads(response) == {"ok": True}
    assert received == {
        "path": "/search",
        "authorization": "Bearer unit-token",
        "content_type": "application/json",
        "payload": {"query": "test"},
    }


class FailedGrounding:
    async def run(self, text: str, **_kwargs) -> SearchOutcome:
        plan = QueryPlan(original=text, query=text, language="fa")
        intent = SearchIntent(True, SearchKind.WEB, True, "explicit_web_search")
        return SearchOutcome(intent, plan, all_providers_failed=True)

    async def health_check(self):
        return False, "disabled"


class TavilyOnlyTransport(RecordingTransport):
    async def get_text(self, *_args, **_kwargs):
        raise AssertionError("SearXNG must not run after Tavily returns results")


@pytest.mark.asyncio
async def test_hybrid_web_uses_tavily_from_environment_when_configured(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "unit-environment-secret")
    config = ZeroConfig.load(CONFIG_EXAMPLE)
    config.web.enabled = True
    config.web.google_grounding_enabled = False
    config.web.tavily_enabled = True
    transport = TavilyOnlyTransport({
        "results": [{
            "title": "Tavily result",
            "url": "https://example.com/tavily",
            "content": "Fresh web evidence",
            "score": 0.95,
        }]
    })
    web = HybridWeb(config, transport=transport, primary=FailedGrounding())

    outcome = await web.run("جدیدترین خبر زیرو را سرچ کن", trace_id="tavily-wire")

    assert outcome.results
    assert outcome.results[0].provider == "tavily"
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_tavily_health_is_fail_safe_when_configured_key_is_missing(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    config = ZeroConfig.load(CONFIG_EXAMPLE)
    config.web.enabled = True
    config.web.tavily_enabled = True
    web = HybridWeb(config, transport=TavilyOnlyTransport({"results": []}), primary=FailedGrounding())

    healthy, detail = await web.health_check()

    assert healthy is False
    assert detail == "tavily API key missing"
