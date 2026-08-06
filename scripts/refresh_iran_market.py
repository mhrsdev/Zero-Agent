from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from zero.market_prices import MilliGoldPriceClient, NavasanPriceClient, PriceAPIError

DB = '/root/zero/runtime/state/zero.db'
ASSETS = {'18ayar': 'طلای ۱۸ عیار', 'sekkeh': 'سکه امامی'}
FAR_EXPIRY = 4102444800

def sha(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

async def main() -> None:
    milli = MilliGoldPriceClient()
    navasan = NavasanPriceClient()
    prices = {}
    failed = {}
    for asset in ASSETS:
        try:
            prices[asset] = await (milli if asset == '18ayar' else navasan).get_price(asset)
        except PriceAPIError as exc:
            failed[asset] = str(exc)
    if not prices:
        raise PriceAPIError(f'هیچ نرخ طلایی از Navasan معتبر دریافت نشد: {failed}')
    now = int(time.time())
    with sqlite3.connect(DB, timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=60000')
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("INSERT OR IGNORE INTO knowledge_topics(topic,enabled,priority,frequency,last_checked_at,next_check_at) VALUES('Iran Market Rates',1,0,'daily',NULL,?)", (now + 86400,))
        topic_id = conn.execute("SELECT id FROM knowledge_topics WHERE topic='Iran Market Rates'").fetchone()['id']
        updated = 0
        for asset, label in ASSETS.items():
            if asset not in prices:
                continue
            item = prices[asset]
            source_name = item.get('source') or ('Milli Gold API' if asset == '18ayar' else 'Navasan API')
            weight = item.get('weight') or 'واحد منبع'
            fact = f"{label}: {item['value']} تومان برای {weight}؛ تغییر: {item.get('change') or 'نامشخص'}؛ منبع: {source_name}؛ زمان منبع: {item.get('updated_at') or 'نامشخص'}"
            facts = json.dumps([{'text': fact, 'confidence': 0.86, 'source_indices': [0]}], ensure_ascii=False, separators=(',', ':'))
            summary = f"{label} از {source_name}: {item['value']} تومان برای {weight}، تغییر {item.get('change') or 'نامشخص'}، به‌روزرسانی {item.get('updated_at') or 'نامشخص'}."
            semantic = sha('iran-market|' + asset)
            content_hash = sha(facts)
            source_url = item.get('source_url') or 'https://api.navasan.tech/latest/'
            source_domain = 'milli.gold' if asset == '18ayar' else 'api.navasan.tech'
            old = conn.execute("SELECT * FROM knowledge_items WHERE semantic_key=? AND status IN ('active','updated') ORDER BY version DESC LIMIT 1", (semantic,)).fetchone()
            if old:
                conn.execute("UPDATE knowledge_items SET topic_id=?,title=?,summary=?,facts_json=?,tags_json=?,content_hash=?,importance=?,confidence=?,freshness_class='daily',status='active',last_seen_at=?,last_verified_at=?,expires_at=?,version=? WHERE id=?", (topic_id, label, summary, facts, json.dumps(['iran-market', asset, source_domain], ensure_ascii=False), content_hash, .85, .86, now, now, FAR_EXPIRY, int(old['version']) + 1, old['id']))
                item_id = old['id']
            else:
                conn.execute('INSERT INTO knowledge_items(topic_id,title,summary,facts_json,tags_json,content_hash,semantic_key,importance,confidence,freshness_class,status,first_seen_at,last_seen_at,last_verified_at,expires_at,version,created_by_backend,model_name) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (topic_id, label, summary, facts, json.dumps(['iran-market', asset, source_domain], ensure_ascii=False), content_hash, semantic, .85, .86, 'daily', 'active', now, now, now, FAR_EXPIRY, 1, 'iran_market_daily', source_name))
                item_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            conn.execute('DELETE FROM knowledge_sources WHERE knowledge_item_id=?', (item_id,))
            conn.execute('INSERT INTO knowledge_sources(knowledge_item_id,source_url,source_domain,source_title,published_at,fetched_at,content_hash,relevance_score,authority_score) VALUES(?,?,?,?,?,?,?,?,?)', (item_id, source_url, source_domain, label, str(item.get('updated_at') or ''), now, content_hash, 1.0, .9))
            updated += 1
        conn.execute('UPDATE knowledge_topics SET last_checked_at=?,next_check_at=? WHERE id=?', (now, now + 86400, topic_id))
        conn.commit()
    print(json.dumps({'updated': updated, 'failed': failed, 'sources': sorted({item.get('source') for item in prices.values()}), 'assets': list(prices)}, ensure_ascii=False))

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except PriceAPIError as exc:
        raise SystemExit(str(exc))
