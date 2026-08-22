"""Small, chat-scoped Social Awareness VNext layer.

Only bounded behavioural aggregates are persisted; raw history remains in the
existing short/medium memory layers.
"""
from __future__ import annotations

from .sqlite_tx import sqlite_txn
import json
import logging
import re
import time
import uuid
from datetime import datetime
from typing import Any

from .memory import detect_topics

logger = logging.getLogger('zero.social_plus')
_EMOJIS = re.compile(r'[😂🤣😄😁😆😹❤️❤🔥🎉👍👏👀🤦😍🥰😢😡🤔🫠]+')
_STOP = {'این', 'برای', 'من', 'تو', 'که', 'چی', 'چرا', 'ولی', 'اگر', 'همین', 'خیلی', 'یک', 'با', 'از', 'به', 'و'}


class SocialAwarenessPlus:
    def __init__(self, store):
        self.store = store

    async def observe(self, chat_id: int, user_id: int, text: str, *, label: str = '', media_type: str = '', now: int | None = None) -> None:
        now = int(now or time.time())
        topics = detect_topics(text or '')[:8]
        emojis = _EMOJIS.findall(text or '')[:8]
        await self.store.observe_social_plus(chat_id, user_id, text or '', label=label, media_type=media_type, topics=topics, emojis=emojis, now=now)
        await self._thread(chat_id, user_id, text or '', topics, now)
        await self._joke(chat_id, user_id, text or '', now)
        await self._quote(chat_id, user_id, text or '', now)
        logger.info('SOCIAL_PROFILE_UPDATED chat_id=%s confidence=bounded', chat_id)
        if emojis:
            logger.info('EMOJI_STYLE_UPDATED chat_id=%s sample_count=%s', chat_id, len(emojis))

    async def _thread(self, chat_id: int, user_id: int, text: str, topics: list[str], now: int) -> None:
        topic = topics[0] if topics else 'general'
        async with self.store._lock:
            with sqlite_txn(self.store._conn()) as conn:
                row = conn.execute('SELECT * FROM social_threads WHERE chat_id=? AND topic=? AND last_activity>=? ORDER BY last_activity DESC LIMIT 1', (chat_id, topic, now - 6 * 3600)).fetchone()
                if row:
                    participants = sorted(set(json.loads(row['participants_json'] or '[]') + [user_id]))[:20]
                    conn.execute('UPDATE social_threads SET participants_json=?,last_activity=?,summary=?,confidence=MIN(1.0,confidence+0.03) WHERE thread_id=?', (json.dumps(participants), now, text[:240], row['thread_id']))
                    logger.info('THREAD_UPDATED chat_id=%s topic=%s confidence=bounded', chat_id, topic)
                else:
                    thread_id = uuid.uuid4().hex
                    conn.execute('INSERT INTO social_threads(thread_id,chat_id,topic,participants_json,started_at,last_activity,summary,confidence) VALUES (?,?,?,?,?,?,?,?)', (thread_id, chat_id, topic, json.dumps([user_id]), now, now, text[:240], 0.55))
                    logger.info('THREAD_CREATED chat_id=%s topic=%s confidence=0.55', chat_id, topic)
                conn.commit()

    async def _joke(self, chat_id: int, user_id: int, text: str, now: int) -> None:
        candidates = [x for x in re.findall(r'[\wآ-ی‌]{3,}', text.casefold()) if x not in _STOP][:4]
        if not candidates or not any(x in text for x in ('😂', '🤣', 'خخ', 'lol')):
            return
        day = datetime.fromtimestamp(now).strftime('%Y-%m-%d')
        async with self.store._lock:
            with sqlite_txn(self.store._conn()) as conn:
                for phrase in candidates[:2]:
                    row = conn.execute('SELECT * FROM inside_jokes WHERE chat_id=? AND phrase=?', (chat_id, phrase)).fetchone()
                    users = sorted(set(json.loads(row['users_json'] or '[]') if row else []) | {user_id})[:20]
                    days = sorted(set(json.loads(row['days_json'] or '[]') if row else []) | {day})[-14:]
                    occurrences = int(row['occurrences']) + 1 if row else 1
                    confidence = min(1.0, 0.2 * min(5, occurrences) + 0.15 * min(3, len(users)) + 0.1 * min(2, len(days)))
                    conn.execute('INSERT INTO inside_jokes(chat_id,phrase,occurrences,users_json,days_json,confidence,first_seen,last_seen) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(chat_id,phrase) DO UPDATE SET occurrences=?,users_json=?,days_json=?,confidence=?,last_seen=?', (chat_id, phrase, occurrences, json.dumps(users), json.dumps(days), confidence, now, now, occurrences, json.dumps(users), json.dumps(days), confidence, now))
                    if len(users) >= 3 and len(days) >= 2 and confidence >= 0.65:
                        logger.info('INSIDE_JOKE_LEARNED chat_id=%s confidence=%.2f', chat_id, confidence)
                conn.commit()

    async def _quote(self, chat_id: int, user_id: int, text: str, now: int) -> None:
        quote = text.strip()[:240]
        if len(quote) < 12 or not any(x in quote for x in ('😂', '🤣', '🔥', '❤️')):
            return
        async with self.store._lock:
            with sqlite_txn(self.store._conn()) as conn:
                row = conn.execute('SELECT * FROM social_quotes WHERE chat_id=? AND quote=?', (chat_id, quote)).fetchone()
                users = sorted(set(json.loads(row['users_json'] or '[]') if row else []) | {user_id})[:20]
                occurrences = int(row['occurrences']) + 1 if row else 1
                conn.execute('INSERT INTO social_quotes(chat_id,quote,users_json,occurrences,first_seen,last_seen) VALUES (?,?,?,?,?,?) ON CONFLICT(chat_id,quote) DO UPDATE SET users_json=?,occurrences=?,last_seen=?', (chat_id, quote, json.dumps(users), occurrences, now, now, json.dumps(users), occurrences, now))
                conn.commit()
        if occurrences >= 2 or len(users) >= 2:
            logger.info('QUOTE_SAVED chat_id=%s occurrences=%s', chat_id, occurrences)

    async def context(self, chat_id: int, query: str = '') -> list[dict[str, Any]]:
        rows = await self.store.get_social_plus_context(chat_id, query)
        if rows:
            logger.info('THREAD_RETRIEVED chat_id=%s count=%s', chat_id, len(rows))
        return rows
