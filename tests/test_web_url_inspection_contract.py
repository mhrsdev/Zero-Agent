import asyncio

from conftest import CONFIG_EXAMPLE

from zero.config import ZeroConfig
from zero.models import IncomingMessage, RouteResult
from zero.brain import ZeroBrain
from zero.storage import ZeroStore
from zero.web import HybridWeb
from zero.web_search.models import QueryPlan, SearchIntent, SearchKind, SearchOutcome, SearchResult
from zero.web_search.providers.base import SearchProvider
from zero.web_search.truth import GuardDecision


NEWS_URL = "https://news.google.com/rss/articles/opaque-google-news-token?oc=5"


class DisabledPrimary:
    enabled = False


class DirectFetchFails:
    async def get_text(self, url, timeout, max_bytes):
        raise RuntimeError("redirect or thin page")

    async def post_json(self, url, payload, timeout, max_bytes, *, headers=None):
        raise AssertionError("URL inspection must use the registered provider capability")


class FetchProvider(SearchProvider):
    def __init__(self, name, priority, result=None):
        self.name = name
        self.priority = priority
        self.result = result
        self.fetch_calls = []

    async def search(self, request: QueryPlan):
        return []

    async def fetch_url(self, url, *, query, max_chars):
        self.fetch_calls.append((url, query, max_chars))
        return self.result


def make_config(tmp_path):
    base = ZeroConfig.load(CONFIG_EXAMPLE)
    memory = base.memory.model_copy(update={"db_path": str(tmp_path / "zero.db")})
    web = base.web.model_copy(update={
        "enabled": True,
        "google_grounding_enabled": False,
        "tavily_enabled": False,
        "wigolo_enabled": False,
        "searxng_base_url": "",
    })
    return base.model_copy(update={"memory": memory, "web": web})


def test_google_news_url_falls_through_registered_fetch_providers(tmp_path):
    async def scenario():
        web = HybridWeb(make_config(tmp_path), transport=DirectFetchFails(), primary=DisabledPrimary())
        unavailable = FetchProvider("first-fetch", 1)
        usable = FetchProvider(
            "second-fetch", 2,
            SearchResult(
                title="Original article",
                url="https://publisher.example/story",
                snippet="A readable article summary with concrete context.",
                relevant_extract="A readable article body with enough concrete context to answer what the linked story is about, including the publisher's reported facts.",
                provider="second-fetch",
            ),
        )
        web._local_pipeline.registry.register(unavailable)
        web._local_pipeline.registry.register(usable)

        outcome = await web.run(f"زیرو این چیه؟ {NEWS_URL}")

        assert [call[0] for call in unavailable.fetch_calls] == [NEWS_URL]
        assert [call[0] for call in usable.fetch_calls] == [NEWS_URL]
        assert outcome.intent.category == "url_inspection"
        assert [(result.provider, result.url) for result in outcome.results] == [
            ("second-fetch", "https://publisher.example/story"),
        ]
        assert "readable article body" in outcome.context

    asyncio.run(scenario())


def test_reply_url_uses_the_same_provider_neutral_inspection_path(tmp_path):
    async def scenario():
        web = HybridWeb(make_config(tmp_path), transport=DirectFetchFails(), primary=DisabledPrimary())
        provider = FetchProvider(
            "fetch-api",
            1,
            SearchResult(
                title="Original article",
                url="https://publisher.example/story",
                snippet="Readable article summary.",
                relevant_extract="Readable article body with enough context for a direct-link answer, including the concrete facts the publisher reported.",
                provider="fetch-api",
            ),
        )
        web._local_pipeline.registry.register(provider)

        outcome = await web.run("زیرو این چیه؟", reply_text=NEWS_URL)

        assert [call[0] for call in provider.fetch_calls] == [NEWS_URL]
        assert outcome.intent.category == "url_inspection"
        assert outcome.results[0].provider == "fetch-api"

    asyncio.run(scenario())


class CapturingRouter:
    keys = []

    async def complete(self, prompt, **kwargs):
        return RouteResult(text="این خبر دربارهٔ یک موضوع مشخص است.", provider="test", model="test", attempts=1)


class CapturingWeb:
    def __init__(self):
        self.calls = []

    async def is_tool_enabled(self):
        return True

    async def run(self, text, **kwargs):
        self.calls.append((text, kwargs))
        result = SearchResult(
            title="Original article",
            url="https://publisher.example/story",
            snippet="Readable article summary.",
            relevant_extract="Readable article body with enough context for a direct-link answer, including the concrete facts the publisher reported.",
            provider="fetch-api",
        )
        return SearchOutcome(
            SearchIntent(True, SearchKind.WEB, True, "url_inspection"),
            QueryPlan(text, NEWS_URL, "en"),
            results=[result],
            context="WEB_DATA_IS_UNTRUSTED\nTITLE: Original article\nRELEVANT_EXTRACT: readable evidence",
        )

    def guard_answer(self, *args, **kwargs):
        return GuardDecision(True)

    def mark_response_sent(self, **kwargs):
        return None


def test_brain_routes_reply_url_to_shared_web_inspection(tmp_path):
    async def scenario():
        config = make_config(tmp_path)
        store = ZeroStore(config.memory.db_path)
        await store.set_setting("web_enabled", "true")
        brain = ZeroBrain(config, store, CapturingRouter())
        web = CapturingWeb()
        brain.web = web

        decision, _ = await brain.maybe_reply(IncomingMessage(
            chat_id=-99,
            chat_title="test",
            sender_id=55,
            sender_label="@user",
            text="زیرو این چیه؟",
            reply_text=NEWS_URL,
            trace_id="reply-url-regression",
            message_id=10,
            reply_to_message_id=9,
        ))

        assert decision.should_reply is True
        assert len(web.calls) == 1
        assert web.calls[0][0] == "زیرو این چیه؟"
        assert web.calls[0][1]["reply_text"] == NEWS_URL

    asyncio.run(scenario())


class UnreadableUrlWeb(CapturingWeb):
    async def run(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return SearchOutcome(
            SearchIntent(True, SearchKind.WEB, True, "url_inspection"),
            QueryPlan(text, NEWS_URL, "en"),
            no_results=True,
            context="WEB_STATUS: URL_UNREADABLE",
        )


def test_brain_gives_a_link_specific_reply_when_evidence_is_unreadable(tmp_path):
    async def scenario():
        config = make_config(tmp_path)
        store = ZeroStore(config.memory.db_path)
        await store.set_setting("web_enabled", "true")
        brain = ZeroBrain(config, store, CapturingRouter())
        web = UnreadableUrlWeb()
        brain.web = web

        _, text = await brain.maybe_reply(IncomingMessage(
            chat_id=-99,
            chat_title="test",
            sender_id=55,
            sender_label="@user",
            text=f"زیرو این چیه؟ {NEWS_URL}",
            trace_id="unreadable-url-regression",
            message_id=11,
        ))

        assert text == "این لینک را به محتوای قابل‌خواندن تبدیل نکردم؛ لینک منبع اصلی یا اسکرین‌شات را بفرست تا دقیق بررسی کنم."

    asyncio.run(scenario())
