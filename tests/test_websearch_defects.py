"""Web search defect contracts.

Every test here reproduces a defect that was verified by execution against
`82174d1` and that the existing 60-test web-search suite did not catch.

The subsystem is optimised, not rebuilt: the control flow in `pipeline.py` and
`orchestrator.py` is sound — providers walk priority tiers, the orchestrator
prefers a grounding primary and falls back — and each defect turned out to be a
local condition rather than the shape of the code. A rewrite would have thrown
away the parts the 60 existing tests already pin.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from conftest import CONFIG_EXAMPLE
from zero.config import ZeroConfig
from zero.web import HybridWeb
from zero.web_search.dedup import deduplicate_results
from zero.web_search.models import QueryPlan, SearchIntent, SearchKind, SearchOutcome, SearchResult
from zero.web_search.pipeline import SearchPipeline
from zero.web_search.providers.base import ProviderRegistry, SearchProvider
from zero.web_search.query import QueryRewriter
from zero.web_search.ranking import ResultRanker
from zero.web_search.truth import TruthfulnessGuard, build_numeric_fallback, site_name


def _plan(query: str = "latest ai news") -> QueryPlan:
    return QueryPlan(original=query, query=query, language="en")


def _result(title="Headline", url="https://example.com/a", snippet="body", **kw) -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet, provider=kw.pop("provider", "p"), **kw)


class Recorder(SearchProvider):
    """A provider that records that it was asked."""

    def __init__(self, name: str, priority: int, results: list[SearchResult], calls: list[str]):
        self.name = name
        self.priority = priority
        self._results = results
        self._calls = calls

    async def search(self, plan):
        self._calls.append(self.name)
        return [
            SearchResult(title=r.title, url=r.url, snippet=r.snippet, provider=r.provider)
            for r in self._results
        ]


def _pipeline(registry, **kw) -> SearchPipeline:
    kw.setdefault("provider_timeout", 1.0)
    kw.setdefault("max_parallel_providers", 1)
    return SearchPipeline(registry=registry, **kw)


# ------------------------------------------------------------------- BLOCKER

@pytest.mark.asyncio
async def test_deep_search_survives_an_empty_provider_registry():
    """The shipped Google-Grounding-only configuration registers no local
    provider. Deep mode flattened the priority groups into `[()]` — one empty
    group — and then read `group[0].priority`, so every /deep_search raised
    IndexError. The orchestrator runs the local pipeline first for deep, so the
    exception escaped before the working primary was ever tried, and the caller
    had already spent one of the user's hourly deep-search reservations."""
    pipeline = _pipeline(ProviderRegistry())
    outcome = await pipeline.run("latest ai news", deep=True, force_search=True)
    assert outcome.results == []
    assert outcome.all_providers_failed is True, "no provider ran, so nothing could succeed"
    assert "PROVIDERS_FAILED" in outcome.context


@pytest.mark.asyncio
async def test_deep_search_ignores_an_empty_tier_but_still_asks_the_rest():
    calls: list[str] = []
    registry = ProviderRegistry()
    registry.register(Recorder("tier-20", 20, [
        _result(title="AI news roundup", url="https://example.org/ai", snippet="ai models this week"),
    ], calls))
    outcome = await _pipeline(registry).run("latest ai news", deep=True, force_search=True)
    # Deep mode asks each provider once per expanded query variant, so the count
    # is the variant count; what matters here is that it was asked at all.
    assert set(calls) == {"tier-20"} and calls
    assert outcome.results, "a registered provider must still be asked in deep mode"


def test_the_deep_relevance_gate_keeps_a_result_about_the_subject():
    """The gate required two matches among `plan.exact_terms`, but for a news
    query two of the three terms are words the rewriter invented — so a real
    article about the subject matched only one and was rejected. Reproduced with
    zoomit.ir articles against "آخرین اخبار زومیت"."""
    plan = QueryRewriter().rewrite("سرچ عمیق کن آخرین اخبار زومیت")
    article = SearchResult(
        title="بررسی گوشی جدید در زومیت", url="https://www.zoomit.ir/mobile/1",
        snippet="مشخصات کامل", provider="p", publisher="zoomit.ir",
    )
    assert ResultRanker().is_relevant(plan, article), plan.exact_terms


def test_the_deep_relevance_gate_still_rejects_an_unrelated_result():
    """The counterpart: loosening the gate must not make it accept anything."""
    plan = QueryRewriter().rewrite("سرچ عمیق کن آخرین اخبار زومیت")
    unrelated = SearchResult(
        title="Cheap flights to Antalya", url="https://example.com/flights",
        snippet="book now", provider="p", publisher="example.com",
    )
    assert ResultRanker().is_relevant(plan, unrelated) is False


# ------------------------------------------------------------------ CRITICAL

@pytest.mark.asyncio
async def test_an_unusable_result_in_the_top_tier_does_not_end_the_fallback_chain():
    """`if collected and not deep: break` tested raw provider output, before the
    guard drops results with no title or a non-public URL. One bad row in tier 10
    therefore stopped tiers 20 and 30 from ever being called, and the user got
    "no results" while healthy providers sat unused."""
    calls: list[str] = []
    registry = ProviderRegistry()
    registry.register(Recorder("tier-10", 10, [_result(title="")], calls))
    registry.register(Recorder("tier-20", 20, [_result(title="Good", url="https://example.org/b")], calls))
    outcome = await _pipeline(registry).run("latest ai news", force_search=True)
    assert calls == ["tier-10", "tier-20"], f"tier-20 was never asked: {calls}"
    assert [r.url for r in outcome.results] == ["https://example.org/b"]
    assert outcome.no_results is False


@pytest.mark.asyncio
async def test_empty_results_after_validation_are_reported_as_validation_not_relevance():
    """Only the deep path runs a relevance gate, so 'relevance_gate' on a normal
    turn sent whoever read the log to the wrong function."""
    calls: list[str] = []
    registry = ProviderRegistry()
    registry.register(Recorder("only", 10, [_result(title="")], calls))
    outcome = await _pipeline(registry).run("latest ai news", force_search=True)
    assert outcome.no_results is True
    assert "validation_rejected_all_results" in outcome.context


def test_guard_accepts_a_publisher_the_model_was_actually_shown():
    """Google Grounding returns redirect URIs and carries the real outlet in the
    title or publisher field. Checking the answer's domains against result URLs
    alone rejected the correct, evidence-backed citation as `unsupported_domain`
    and replaced the whole answer with a fallback."""
    grounding = [_result(
        title="Reuters", url="https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc",
        snippet="gold price rose", provider="google-grounding", publisher="reuters.com",
        published_at="2026-09-01", relevant_extract="gold price rose",
    )]
    assert TruthfulnessGuard().guard_answer("Reuters (reuters.com) reported the price rose.", grounding).allowed


@pytest.mark.parametrize("answer", [
    "Edit config.py and then restart the service.",
    "Update package.json before deploying.",
    "See README.md for details.",
    "The fix is in src/main.rs and Cargo.toml.",
])
def test_guard_does_not_treat_a_filename_as_an_unsupported_domain(answer):
    """`_DOMAIN_RE` matches any `word.word`, so every answer on a programming
    topic was rejected outright — the cost was the whole reply, not a citation."""
    decision = TruthfulnessGuard().guard_answer(answer, [_result()])
    assert decision.allowed, decision.reason


def test_guard_still_rejects_a_domain_that_appears_nowhere_in_the_evidence():
    """The counterpart: relaxing the check must not make it useless."""
    decision = TruthfulnessGuard().guard_answer(
        "According to totally-made-up-source.com the price rose.", [_result()])
    assert decision.allowed is False
    assert decision.reason == "unsupported_domain"


def test_a_redirect_wrapper_is_never_shown_as_the_source_name():
    assert site_name(
        "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc",
        "reuters.com",
    ) == "Reuters"
    assert site_name("https://www.zoomit.ir/x", "zoomit.ir") == "Zoomit"


# ---------------------------------------------------------------------- HIGH

@pytest.mark.parametrize("text,must_contain,must_not_contain", [
    ("best node.js framework 2026", ("node", "framework"), ("site:node.js",)),
    ("vue.js vs react.js performance", ("vue", "performance"), ("site:vue.js",)),
    ("latest verified OpenAI GPT-5 official release research news", ("gpt-5",), ()),
])
def test_the_rewriter_keeps_the_subject_it_was_asked_about(text, must_contain, must_not_contain):
    """Any `word.word` was treated as a host, so the token was pulled out of the
    topic AND re-attached as a `site:` filter: "best node.js framework 2026"
    became "best framework 2026 site:node.js", which cannot match anything and no
    longer names what was asked. The OpenAI branch went further and returned a
    constant, so every OpenAI topic shared one query and one cache entry."""
    query = QueryRewriter().rewrite(text).query.lower()
    for token in must_contain:
        assert token in query, f"{token!r} was dropped from {query!r}"
    for token in must_not_contain:
        assert token not in query, f"{token!r} should not be a site filter in {query!r}"


def test_an_explicit_site_operator_does_not_leave_the_word_site_as_a_term():
    """`site:t.me zero agent` is what zero/telegram_search.py sends. Stripping
    the domain but not the operator left a bare `site` token in the query."""
    plan = QueryRewriter().rewrite("site:t.me zero agent")
    assert plan.preferred_domain == "t.me"
    assert "site:t.me" in plan.query
    assert " site " not in f" {plan.query} ".replace("site:t.me", "")


def test_a_bare_domain_is_not_repeated_as_both_topic_and_filter():
    plan = QueryRewriter().rewrite("digikala.com")
    assert plan.query == "site:digikala.com"


def test_an_english_news_query_stays_english():
    """A Persian prefix on an English subject also set plan.language='fa', and
    the ranker down-weights results whose language does not match — so the query
    was penalised for words the rewriter itself added."""
    plan = QueryRewriter().rewrite("latest Nvidia Blackwell B300 release news")
    assert plan.language == "en", plan.query
    assert "آخرین" not in plan.query


def test_a_persian_news_query_stays_persian():
    plan = QueryRewriter().rewrite("آخرین اخبار زومیت")
    assert plan.language == "fa", plan.query


@pytest.mark.asyncio
async def test_run_refuses_when_web_search_is_switched_off():
    """The enable check lived only in `search_hits`, and two callers —
    zero/knowledge.py and zero/tg_source_manager.py — use `run` and check
    nothing, so a disabled web search still made outbound calls and spent
    grounding quota."""
    class Primary:
        def __init__(self):
            self.calls = 0

        async def run(self, text, **kwargs):
            self.calls += 1
            return SearchOutcome(SearchIntent(True, SearchKind.WEB, True, "x"), _plan())

        async def health_check(self):
            return False, "disabled"

        def invalidate_cache(self):
            return None

        def mark_response_sent(self, **kwargs):
            return None

    config = ZeroConfig.load(CONFIG_EXAMPLE)
    config.web.enabled = False
    primary = Primary()
    web = HybridWeb(config, primary=primary)
    outcome = await web.run("latest ai news")
    assert primary.calls == 0, "a disabled search must not reach a provider"
    assert outcome.results == []
    assert outcome.context == "WEB_STATUS: DISABLED"
    # Distinct from a failure: nothing failed, so the model must not report one.
    assert outcome.all_providers_failed is False
    assert outcome.no_results is False


# -------------------------------------------------------------------- MEDIUM

def test_dedup_keeps_two_outlets_that_ran_the_same_headline():
    """The title key had no host component, so a wire story carried by two
    outlets collapsed to one result — and the numeric fallback needs two
    independent sources before it will average a price, so the collapse silently
    disabled it."""
    same_headline = "Nvidia announces Spark"
    results = deduplicate_results([
        _result(title=same_headline, url="https://nvidia.com/news/spark", snippet="a"),
        _result(title=same_headline, url="https://reuters.com/tech/spark-2026", snippet="b"),
    ])
    assert [r.url for r in results] == [
        "https://nvidia.com/news/spark", "https://reuters.com/tech/spark-2026",
    ]


def test_dedup_still_collapses_the_same_page_seen_twice():
    results = deduplicate_results([
        _result(title="Same", url="https://example.com/a?utm_source=x", snippet="short"),
        _result(title="Same", url="https://www.example.com/a/", snippet="a longer snippet"),
    ])
    assert len(results) == 1
    assert results[0].metadata["duplicate_count"] == 1
    assert results[0].snippet == "a longer snippet", "the richer snippet must win"


def test_numeric_fallback_averages_when_one_source_omits_the_unit():
    """An unlabelled number does not contradict the others, it just says less.
    Adding '' to the unit set made `len(units) != 1` true and refused to answer,
    so a live price query with one unlabelled source reported that no verifiable
    price was found — and it made the `units - {''}` line below unreachable."""
    results = [
        _result(title="A", url="https://a.example/1", snippet="قیمت ۷۲٬۵۰۰٬۰۰۰ تومان است", relevant_extract="قیمت ۷۲٬۵۰۰٬۰۰۰ تومان است"),
        _result(title="B", url="https://b.example/2", snippet="قیمت ۷۲٬۷۰۰٬۰۰۰ تومان", relevant_extract="قیمت ۷۲٬۷۰۰٬۰۰۰ تومان"),
        _result(title="C", url="https://c.example/3", snippet="آخرین رقم ۷۲٬۶۰۰٬۰۰۰", relevant_extract="آخرین رقم ۷۲٬۶۰۰٬۰۰۰"),
    ]
    out = build_numeric_fallback(results, "2026-09-03 10:00 UTC")
    assert out, "three agreeing sources must produce an answer"
    assert "تومان" in out, "the one named unit must be carried"


def test_numeric_fallback_still_refuses_when_two_named_units_disagree():
    results = [
        _result(title="A", url="https://a.example/1", snippet="۷۲٬۵۰۰٬۰۰۰ تومان", relevant_extract="۷۲٬۵۰۰٬۰۰۰ تومان"),
        _result(title="B", url="https://b.example/2", snippet="۷۲۵٬۰۰۰٬۰۰۰ ریال", relevant_extract="۷۲۵٬۰۰۰٬۰۰۰ ریال"),
    ]
    assert build_numeric_fallback(results) == ""


@pytest.mark.parametrize("payload", [{"results": None}, {}, {"results": []}])
def test_a_provider_answering_with_a_null_result_list_reads_as_empty(payload):
    """`data.get("results", [])` returns None for `{"results": null}`, which
    raised TypeError inside the provider — logged by the pipeline as an
    indistinguishable generic failure."""
    from zero.web_search.providers.searxng import SearXNGProvider
    from zero.web_search.providers.tavily import TavilyProvider
    from zero.web_search.providers.wigolo import WigoloProvider

    class Transport:
        async def get_text(self, *a, **k):
            return json.dumps(payload)

        async def post_json(self, *a, **k):
            return json.dumps(payload)

    providers = (
        SearXNGProvider(transport=Transport(), base_url="http://x", timeout=1, max_results=3),
        TavilyProvider(transport=Transport(), api_key="k", timeout=1, max_results=3),
        WigoloProvider(transport=Transport(), base_url="http://x", timeout=1, max_results=3),
    )
    for provider in providers:
        assert asyncio.run(provider.search(_plan())) == [], provider.name


def test_transport_failures_carry_their_http_status():
    """Every transport failure was a bare RuntimeError and the pipeline logs only
    the exception type, so a 403 from a SearXNG instance with JSON disabled, a 400
    from a bad payload, a 502 and a blocked private destination were one
    indistinguishable log line."""
    from zero.web_search.transport import (
        DestinationRejected, HttpRedirectRejected, HttpStatusError, TransportError,
    )

    assert issubclass(HttpStatusError, TransportError)
    assert issubclass(HttpRedirectRejected, TransportError)
    # Still a ValueError so the existing orchestrator contract test holds.
    assert issubclass(DestinationRejected, ValueError)
    assert HttpStatusError(403).status == 403
    assert HttpRedirectRejected(301, "https://example.com/moved").location == "https://example.com/moved"


def test_tavily_results_carry_the_publication_date_the_api_reported():
    """Tavily reports `published_date` and it was dropped, so every Tavily result
    scored the constant unknown-age freshness and the news fallback always said
    the date was unclear."""
    from zero.web_search.providers.tavily import TavilyProvider

    class Transport:
        async def post_json(self, *a, **k):
            return json.dumps({"results": [{
                "url": "https://example.com/a", "title": "T", "content": "c",
                "published_date": "2026-09-01T10:00:00Z", "score": 0.9,
            }]})

    provider = TavilyProvider(transport=Transport(), api_key="k", timeout=1, max_results=3)
    results = asyncio.run(provider.search(_plan()))
    assert results and results[0].published_at.startswith("2026-09-01")


def test_tavily_and_wigolo_are_not_in_the_same_priority_tier():
    """Sharing a priority put both in one group, so a paid Tavily call was made on
    every search even when the self-hosted provider had already answered."""
    from zero.web_search.providers.tavily import TavilyProvider
    from zero.web_search.providers.wigolo import WigoloProvider

    assert TavilyProvider.priority != WigoloProvider.priority


def test_forcing_a_search_keeps_the_detected_category():
    """Overwriting the category with 'explicit_web_search' cost a price query its
    numeric fallback, its live-market disclosure, and the `live` freshness
    discriminator in the cache key — so a /search price query was cached for the
    full TTL like any other question."""
    from zero.web_search.intent import SearchIntentDetector
    from zero.web_search.truth import numeric_fallback_eligible

    detected = SearchIntentDetector().detect("قیمت طلا امروز چنده")
    assert numeric_fallback_eligible(detected.category), (
        f"fixture assumption broken: {detected.category}"
    )

    class Detector:
        def detect(self, text, *, reply_text=""):
            return detected

    pipeline = _pipeline(ProviderRegistry(), intent_detector=Detector())
    outcome = asyncio.run(pipeline.run("قیمت طلا امروز چنده", force_search=True))
    assert outcome.intent.category == detected.category
    assert numeric_fallback_eligible(outcome.intent.category)


def test_page_extraction_is_bounded_by_a_deadline():
    """extract_many gathers one fetch per result and had no overall bound — only
    per-socket timeouts — so slow hosts could outlast the caller's whole search
    budget. On timeout the results are kept and rendered from their snippets."""
    calls: list[str] = []
    registry = ProviderRegistry()
    registry.register(Recorder("only", 10, [
        _result(title="AI news roundup", url="https://example.org/ai", snippet="ai models this week"),
    ], calls))

    class SlowExtractor:
        async def extract_many(self, results, query, limit):
            await asyncio.sleep(30)
            return results

    async def scenario():
        pipeline = _pipeline(registry, extractor=SlowExtractor(), extract_timeout=0.05)
        started = asyncio.get_running_loop().time()
        outcome = await pipeline.run("latest ai news", force_search=True)
        return outcome, asyncio.get_running_loop().time() - started

    outcome, elapsed = asyncio.run(scenario())
    assert elapsed < 5, f"extraction was not bounded: {elapsed:.1f}s"
    assert outcome.results, "a timed-out extraction must keep the results it had"
