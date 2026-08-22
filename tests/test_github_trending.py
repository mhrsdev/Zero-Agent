from zero.sqlite_tx import sqlite_txn
import asyncio
import json
from pathlib import Path

from zero.github_trending import GithubTrendingProvider, clean_readme_excerpt, render_github_digest
from zero.storage import ZeroStore


def test_extracts_ranked_github_repositories_in_dom_order():
    html = '''<a href="https://github.com/acme/one">acme/one</a><a href="https://github.com/acme/two">acme/two</a><a href="https://github.com/acme/one">duplicate</a>'''
    items = GithubTrendingProvider.extract_ranked(html, limit=6)
    assert [(x["rank"], x["full_name"]) for x in items] == [(1, "acme/one"), (2, "acme/two")]


def test_readme_cleaner_drops_html_chinese_and_install_commands():
    cleaned = clean_readme_excerpt('<p>English useful summary</p>\n给 Codex 换皮肤\n```bash\nnpm install bad\n```')
    assert 'English useful summary' in cleaned
    assert '给 Codex' not in cleaned
    assert 'npm install' not in cleaned


def test_render_requires_useful_fields_and_has_github_link():
    text = render_github_digest({
        "full_name": "acme/one", "rank": 1, "description": "ابزار تستی",
        "readme": "این پروژه برای تست ساخته شده است.", "language": "Python",
        "stars": 12, "forks": 3, "open_issues": 0, "updated_at": "2026-07-22T00:00:00Z",
    })
    assert "acme/one" in text
    assert "github.com/acme/one" in text
    assert "UNTRUSTED_REPOSITORY_DATA" not in text


def test_history_is_idempotent(tmp_path: Path):
    async def scenario():
        store = ZeroStore(str(tmp_path / "trend.db"))
        assert await store.github_trending_seen("acme/one") is False
        await store.github_trending_mark("acme/one", rank=1, fingerprint="abc", source_url="https://github.ranbot.online/")
        assert await store.github_trending_seen("acme/one") is True
        await store.github_trending_mark("acme/one", rank=1, fingerprint="abc", source_url="https://github.ranbot.online/")
        async with store._lock:
            with sqlite_txn(store._conn()) as conn:
                assert conn.execute("SELECT COUNT(*) FROM github_trending_items").fetchone()[0] == 1
    asyncio.run(scenario())
