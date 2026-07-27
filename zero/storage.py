from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import re
import sqlite3
import stat
import time
import uuid

from pathlib import Path
from typing import Any

logger = logging.getLogger('zero.storage')


def _memory_key(value: str) -> str:
    return re.sub(r'\s+', ' ', (value or '').strip().casefold()).strip()


SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS group_context_state (chat_id INTEGER PRIMARY KEY,last_message_id INTEGER,last_timestamp INTEGER,summary_json TEXT NOT NULL DEFAULT '{}',summary_version INTEGER NOT NULL DEFAULT 0,pending_from_message_id INTEGER,optimistic_version INTEGER NOT NULL DEFAULT 0,updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS group_context_consumed (chat_id INTEGER NOT NULL,message_id INTEGER NOT NULL,edited_at INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(chat_id,message_id));
CREATE TABLE IF NOT EXISTS recent_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL,
  sender_id INTEGER NOT NULL,
  sender_label TEXT NOT NULL,
  role TEXT NOT NULL,
  text TEXT NOT NULL,
  platform TEXT,
  account_scope TEXT,
  telegram_message_id INTEGER,
  reply_to_message_id INTEGER,
  thread_id INTEGER,
  sender_username TEXT NOT NULL DEFAULT '',
  sender_display_name TEXT NOT NULL DEFAULT '',
  trace_id TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recent_messages_chat_id_desc ON recent_messages(chat_id, id DESC, created_at DESC);
CREATE TABLE IF NOT EXISTS incoming_message_dedup (
  platform TEXT NOT NULL,
  account_scope TEXT NOT NULL,
  chat_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  thread_id INTEGER,
  sender_id INTEGER,
  status TEXT NOT NULL CHECK(status IN ('processing','replied','failed','expired')),
  trace_id TEXT NOT NULL,
  reply_message_id INTEGER,
  reason TEXT NOT NULL DEFAULT '',
  attempt_count INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  finished_at INTEGER,
  expires_at INTEGER,
  PRIMARY KEY(platform, account_scope, chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_incoming_message_dedup_status ON incoming_message_dedup(status, updated_at);
CREATE TABLE IF NOT EXISTS user_profiles (
  sender_id INTEGER PRIMARY KEY,
  label TEXT NOT NULL,
  nicknames_json TEXT NOT NULL DEFAULT '[]',
  topics_json TEXT NOT NULL DEFAULT '[]',
  projects_json TEXT NOT NULL DEFAULT '[]',
  style_notes_json TEXT NOT NULL DEFAULT '[]',
  reputation INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS user_profiles_scoped (
  chat_id INTEGER NOT NULL,
  sender_id INTEGER NOT NULL,
  label TEXT NOT NULL,
  username TEXT NOT NULL DEFAULT '',
  display_name TEXT NOT NULL DEFAULT '',
  nicknames_json TEXT NOT NULL DEFAULT '[]',
  topics_json TEXT NOT NULL DEFAULT '[]',
  projects_json TEXT NOT NULL DEFAULT '[]',
  style_notes_json TEXT NOT NULL DEFAULT '[]',
  reputation INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(chat_id, sender_id)
);
CREATE INDEX IF NOT EXISTS idx_user_profiles_scoped_sender ON user_profiles_scoped(sender_id, chat_id);
CREATE TABLE IF NOT EXISTS memory_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  score INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sender_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_events_scoped (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL,
  sender_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate_events_scoped_identity ON rate_events_scoped(chat_id, sender_id, kind, created_at);
CREATE TABLE IF NOT EXISTS vision_rate_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sender_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS stats (
  day TEXT PRIMARY KEY,
  message_count INTEGER NOT NULL DEFAULT 0,
  reply_count INTEGER NOT NULL DEFAULT 0,
  api_calls INTEGER NOT NULL DEFAULT 0,
  retries INTEGER NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0,
  input_chars INTEGER NOT NULL DEFAULT 0,
  output_chars INTEGER NOT NULL DEFAULT 0,
  total_cost_usd REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS router_keys (
  alias TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  state_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS group_user_state (
  user_id INTEGER NOT NULL,
  chat_id INTEGER NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  first_seen INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  message_count INTEGER NOT NULL DEFAULT 0,
  joined_at INTEGER,
  left_at INTEGER,
  welcome_sent INTEGER NOT NULL DEFAULT 0,
  last_inactive_ping INTEGER,
  dm_allowed INTEGER NOT NULL DEFAULT 0,
  social_opt_out INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(user_id, chat_id)
);
CREATE INDEX IF NOT EXISTS idx_group_user_inactive ON group_user_state(chat_id, last_seen);

CREATE TABLE IF NOT EXISTS social_group_state (
  chat_id INTEGER PRIMARY KEY,
  social_reputation INTEGER NOT NULL DEFAULT 0,
  positive_feedback_count INTEGER NOT NULL DEFAULT 0,
  negative_feedback_count INTEGER NOT NULL DEFAULT 0,
  reply_acceptance_count INTEGER NOT NULL DEFAULT 0,
  ignored_reply_count INTEGER NOT NULL DEFAULT 0,
  daily_reply_budget INTEGER NOT NULL DEFAULT 24,
  social_confidence REAL NOT NULL DEFAULT 1.0,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS social_feedback_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL,
  sender_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_social_feedback_recent ON social_feedback_events(chat_id, kind, created_at);
CREATE TABLE IF NOT EXISTS social_profiles (
  chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, nickname TEXT NOT NULL DEFAULT '',
  favorite_topics_json TEXT NOT NULL DEFAULT '[]', activity_hours_json TEXT NOT NULL DEFAULT '{}',
  humor_score REAL NOT NULL DEFAULT 0, sticker_usage INTEGER NOT NULL DEFAULT 0,
  reaction_usage INTEGER NOT NULL DEFAULT 0, preferred_language TEXT NOT NULL DEFAULT 'unknown',
  interaction_style TEXT NOT NULL DEFAULT 'unknown', confidence REAL NOT NULL DEFAULT 0.1,
  updated_at INTEGER NOT NULL, PRIMARY KEY(chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS inside_jokes (
  chat_id INTEGER NOT NULL, phrase TEXT NOT NULL, occurrences INTEGER NOT NULL DEFAULT 0,
  users_json TEXT NOT NULL DEFAULT '[]', days_json TEXT NOT NULL DEFAULT '[]', confidence REAL NOT NULL DEFAULT 0,
  first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, PRIMARY KEY(chat_id, phrase)
);
CREATE TABLE IF NOT EXISTS social_threads (
  thread_id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, topic TEXT NOT NULL,
  participants_json TEXT NOT NULL DEFAULT '[]', started_at INTEGER NOT NULL, last_activity INTEGER NOT NULL,
  summary TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_social_threads_active ON social_threads(chat_id, last_activity DESC);
CREATE TABLE IF NOT EXISTS social_quotes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, quote TEXT NOT NULL,
  users_json TEXT NOT NULL DEFAULT '[]', occurrences INTEGER NOT NULL DEFAULT 0,
  first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, UNIQUE(chat_id, quote)
);
CREATE TABLE IF NOT EXISTS social_stats (
  chat_id INTEGER NOT NULL, day TEXT NOT NULL, message_count INTEGER NOT NULL DEFAULT 0,
  sticker_count INTEGER NOT NULL DEFAULT 0, gif_count INTEGER NOT NULL DEFAULT 0, image_count INTEGER NOT NULL DEFAULT 0,
  user_counts_json TEXT NOT NULL DEFAULT '{}', topic_counts_json TEXT NOT NULL DEFAULT '{}',
  emoji_counts_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(chat_id, day)
);
CREATE TABLE IF NOT EXISTS social_action_messages (
  chat_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS cron_permissions (
  user_id INTEGER PRIMARY KEY,
  role TEXT NOT NULL,
  capabilities_json TEXT NOT NULL DEFAULT '[]',
  granted_by INTEGER NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cron_templates (
  template_id TEXT NOT NULL,
  version TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(template_id, version)
);
CREATE TABLE IF NOT EXISTS cron_jobs (
  job_id TEXT PRIMARY KEY,
  version INTEGER NOT NULL DEFAULT 1,
  template_id TEXT NOT NULL,
  template_version TEXT NOT NULL,
  owner_user_id INTEGER NOT NULL,
  created_by_user_id INTEGER NOT NULL,
  chat_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  input_json TEXT NOT NULL,
  schedule_json TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  approval_state TEXT NOT NULL,
  state TEXT NOT NULL,
  next_run_at INTEGER,
  last_run_at INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cron_jobs_due ON cron_jobs(state, next_run_at);
CREATE TABLE IF NOT EXISTS cron_runs (
  run_id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  job_version INTEGER NOT NULL,
  scheduled_for INTEGER NOT NULL,
  state TEXT NOT NULL,
  started_at INTEGER,
  finished_at INTEGER,
  duration_ms INTEGER,
  exit_code INTEGER,
  result_text TEXT NOT NULL DEFAULT '',
  exception_type TEXT,
  trace_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE(job_id, scheduled_for)
);
CREATE INDEX IF NOT EXISTS idx_cron_runs_job ON cron_runs(job_id, created_at DESC);
CREATE TABLE IF NOT EXISTS github_trending_items (
  repo_full_name TEXT PRIMARY KEY,
  last_seen_rank INTEGER NOT NULL DEFAULT 0,
  last_seen_fingerprint TEXT NOT NULL DEFAULT '',
  last_introduced_at INTEGER,
  intro_count INTEGER NOT NULL DEFAULT 0,
  last_source_url TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cron_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cron_metrics (
  job_id TEXT PRIMARY KEY,
  run_count INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0,
  failure_count INTEGER NOT NULL DEFAULT 0,
  total_duration_ms INTEGER NOT NULL DEFAULT 0,
  last_duration_ms INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cron_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id TEXT NOT NULL,
  actor_user_id INTEGER NOT NULL,
  actor_role TEXT NOT NULL,
  action TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  details_json TEXT NOT NULL,
  previous_hash TEXT NOT NULL,
  event_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cron_audit_object ON cron_audit(object_type, object_id, id);

CREATE TABLE IF NOT EXISTS limit_challenge_progress (
  user_id INTEGER NOT NULL,
  chat_id INTEGER NOT NULL,
  current_stage INTEGER NOT NULL DEFAULT 1,
  completed_stages_json TEXT NOT NULL DEFAULT '[]',
  reward_step INTEGER NOT NULL DEFAULT 0,
  bonus_quota INTEGER NOT NULL DEFAULT 0,
  last_challenge_at INTEGER,
  daily_completed_count INTEGER NOT NULL DEFAULT 0,
  day_key TEXT NOT NULL DEFAULT '',
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(user_id, chat_id)
);
CREATE TABLE IF NOT EXISTS limit_challenge_active (
  user_id INTEGER NOT NULL,
  chat_id INTEGER NOT NULL,
  stage INTEGER NOT NULL,
  challenge_id TEXT NOT NULL,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  answer_hash TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  PRIMARY KEY(user_id, chat_id)
);
CREATE INDEX IF NOT EXISTS idx_limit_challenge_active_expiry ON limit_challenge_active(status, expires_at);
CREATE TABLE IF NOT EXISTS limit_challenge_templates (
  stage INTEGER NOT NULL,
  template_id TEXT NOT NULL,
  template_type TEXT NOT NULL,
  template_json TEXT NOT NULL,
  usage_count INTEGER NOT NULL DEFAULT 0,
  last_used_at INTEGER,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(stage, template_id)
);

-- Stickers tables
CREATE TABLE IF NOT EXISTS stickers (
  doc_id INTEGER PRIMARY KEY,
  access_hash INTEGER NOT NULL,
  file_reference BLOB NOT NULL,
  mime_type TEXT NOT NULL,
  emoji TEXT,
  stickerset_id INTEGER,
  stickerset_access_hash INTEGER,
  stickerset_short_name TEXT,
  is_animated BOOLEAN NOT NULL DEFAULT 0,
  is_video BOOLEAN NOT NULL DEFAULT 0,
  vision_summary TEXT,
  vision_tags TEXT,
  nsfw_score REAL NOT NULL DEFAULT 0.0,
  mood_tags TEXT,
  quality_score REAL NOT NULL DEFAULT 0.5,
  spam_score REAL NOT NULL DEFAULT 0.0,
  usage_count INTEGER NOT NULL DEFAULT 0,
  first_seen INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  first_sender_id INTEGER,
  saved_to_account BOOLEAN NOT NULL DEFAULT 0,
  saved_at INTEGER,
  recent_saved BOOLEAN NOT NULL DEFAULT 0,
  last_message_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_stickers_usage ON stickers(usage_count DESC);
CREATE INDEX IF NOT EXISTS idx_stickers_quality ON stickers(quality_score DESC);
CREATE INDEX IF NOT EXISTS idx_stickers_mood ON stickers(mood_tags);
CREATE INDEX IF NOT EXISTS idx_stickers_saved ON stickers(saved_to_account);
CREATE INDEX IF NOT EXISTS idx_stickers_last_seen ON stickers(last_seen DESC);
CREATE TABLE IF NOT EXISTS sticker_send_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL,
  doc_id INTEGER NOT NULL,
  sent_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sticker_send_history_chat ON sticker_send_history(chat_id, sent_at DESC);

CREATE TABLE IF NOT EXISTS sticker_sets (
  set_id INTEGER PRIMARY KEY,
  access_hash INTEGER NOT NULL,
  short_name TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  count INTEGER,
  is_animated BOOLEAN,
  is_video BOOLEAN,
  is_official BOOLEAN,
  installed BOOLEAN NOT NULL DEFAULT 0,
  installed_at INTEGER,
  updated_at INTEGER NOT NULL
);

-- Three-layer, chat-scoped memory. Legacy memory_items remains read-only during migration.
CREATE TABLE IF NOT EXISTS long_term_memory (
  memory_id TEXT PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  subject_user_id INTEGER,
  category TEXT NOT NULL,
  content TEXT NOT NULL,
  confidence REAL NOT NULL,
  source_message_ids_json TEXT NOT NULL DEFAULT '[]',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  last_confirmed_at INTEGER,
  expires_at INTEGER,
  status TEXT NOT NULL DEFAULT 'active',
  revision INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER NOT NULL,
  sensitivity_level TEXT NOT NULL DEFAULT 'normal'
);
CREATE INDEX IF NOT EXISTS idx_long_memory_chat_status ON long_term_memory(chat_id, status, updated_at);
CREATE TABLE IF NOT EXISTS medium_term_memory (
  event_id TEXT PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  participants_json TEXT NOT NULL DEFAULT '[]',
  topic TEXT NOT NULL,
  summary TEXT NOT NULL,
  source_message_ids_json TEXT NOT NULL DEFAULT '[]',
  importance REAL NOT NULL,
  confidence REAL NOT NULL,
  occurred_at INTEGER NOT NULL,
  last_referenced_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  promotion_candidate INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  revision INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_medium_memory_chat_expiry ON medium_term_memory(chat_id, status, expires_at);
CREATE TABLE IF NOT EXISTS short_term_context (
  chat_id INTEGER PRIMARY KEY,
  active_topic TEXT NOT NULL DEFAULT '',
  active_participants_json TEXT NOT NULL DEFAULT '[]',
  addressed_to_zero INTEGER NOT NULL DEFAULT 0,
  conversation_pairs_json TEXT NOT NULL DEFAULT '[]',
  mood TEXT NOT NULL DEFAULT 'neutral',
  sensitivity TEXT NOT NULL DEFAULT 'normal',
  question_unanswered INTEGER NOT NULL DEFAULT 0,
  zero_recent_reply_count INTEGER NOT NULL DEFAULT 0,
  negative_feedback_score REAL NOT NULL DEFAULT 0,
  should_reply INTEGER NOT NULL DEFAULT 0,
  should_react INTEGER NOT NULL DEFAULT 0,
  should_wait INTEGER NOT NULL DEFAULT 0,
  should_ignore INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS short_term_media_context (
  media_id TEXT PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  sender_id INTEGER NOT NULL,
  media_type TEXT NOT NULL,
  caption TEXT NOT NULL DEFAULT '',
  reply_to_message_id INTEGER,
  summary TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_short_media_chat_time ON short_term_media_context(chat_id, created_at);
CREATE TABLE IF NOT EXISTS memory_revisions (
  revision_id TEXT PRIMARY KEY,
  layer TEXT NOT NULL,
  object_id TEXT NOT NULL,
  chat_id INTEGER NOT NULL,
  before_json TEXT,
  after_json TEXT,
  actor_user_id INTEGER NOT NULL,
  reason TEXT NOT NULL,
  source TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_revisions_chat ON memory_revisions(chat_id, layer, created_at);
CREATE TABLE IF NOT EXISTS memory_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  layer TEXT NOT NULL,
  chat_id INTEGER NOT NULL,
  object_id TEXT,
  actor_user_id INTEGER,
  trace_id TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_audit_chat ON memory_audit(chat_id, created_at);

CREATE TABLE IF NOT EXISTS memory_rag_documents (
  doc_id TEXT PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  subject_user_id INTEGER,
  scope TEXT NOT NULL DEFAULT 'personal',
  layer TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL,
  source_telegram_ids_json TEXT NOT NULL DEFAULT '[]',
  source_trace_ids_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL DEFAULT 0.7,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  expires_at INTEGER,
  status TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_memory_rag_scope ON memory_rag_documents(chat_id, scope, subject_user_id, status);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_rag_fts USING fts5(doc_id UNINDEXED, chat_id UNINDEXED, category, layer, content);

-- Public web-derived Knowledge Memory; deliberately separate from all user/group memory.
CREATE TABLE IF NOT EXISTS knowledge_topics (
  id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT UNIQUE NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
  priority INTEGER NOT NULL DEFAULT 0, frequency TEXT NOT NULL DEFAULT 'daily', last_checked_at INTEGER, next_check_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id INTEGER NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
  facts_json TEXT NOT NULL, tags_json TEXT NOT NULL, content_hash TEXT NOT NULL, semantic_key TEXT NOT NULL,
  importance REAL NOT NULL, confidence REAL NOT NULL, freshness_class TEXT NOT NULL, status TEXT NOT NULL,
  first_seen_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL, last_verified_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
  version INTEGER NOT NULL DEFAULT 1, created_by_backend TEXT NOT NULL, model_name TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_items_retrieval ON knowledge_items(status, expires_at, confidence, topic_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_items_semantic ON knowledge_items(semantic_key, version);
CREATE TABLE IF NOT EXISTS knowledge_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT, knowledge_item_id INTEGER NOT NULL, source_url TEXT NOT NULL,
  source_domain TEXT NOT NULL, source_title TEXT NOT NULL, published_at TEXT NOT NULL, fetched_at INTEGER NOT NULL,
  content_hash TEXT NOT NULL, relevance_score REAL NOT NULL, authority_score REAL NOT NULL,
  UNIQUE(knowledge_item_id, source_url)
);
CREATE TABLE IF NOT EXISTS knowledge_runs (
  run_id TEXT PRIMARY KEY, started_at INTEGER NOT NULL, finished_at INTEGER, status TEXT NOT NULL,
  trace_id TEXT NOT NULL, backend TEXT NOT NULL, model_name TEXT NOT NULL DEFAULT '', llm_calls_used INTEGER NOT NULL DEFAULT 0,
  accepted_count INTEGER NOT NULL DEFAULT 0, rejected_count INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS knowledge_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_audit_run ON knowledge_audit(run_id, created_at);

-- Telegram Search operational state/cache/limits; isolated from Web and conversational memory.
CREATE TABLE IF NOT EXISTS telegram_search_state (
  state_key TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, sender_id INTEGER NOT NULL,
  thread_id INTEGER, reply_to_message_id INTEGER, search_session_id TEXT NOT NULL,
  trace_id TEXT NOT NULL, query TEXT NOT NULL, intent TEXT NOT NULL, payload_json TEXT NOT NULL,
  created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_tg_state_identity ON telegram_search_state(chat_id, sender_id, expires_at);
CREATE TABLE IF NOT EXISTS telegram_search_cache (
  cache_key TEXT PRIMARY KEY, normalized_query TEXT NOT NULL, intent TEXT NOT NULL,
  provider_set TEXT NOT NULL, language TEXT NOT NULL, freshness TEXT NOT NULL, visibility_scope TEXT NOT NULL,
  payload_json TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_tg_cache_expiry ON telegram_search_cache(expires_at, status);
CREATE TABLE IF NOT EXISTS telegram_search_limits (
  account_scope TEXT NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL, used_count INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL, PRIMARY KEY(account_scope, day, kind)
);
CREATE TABLE IF NOT EXISTS telegram_knowledge_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, source_provider TEXT NOT NULL,
  channel_identifier TEXT NOT NULL, message_id INTEGER, canonical_link TEXT NOT NULL,
  text_excerpt TEXT NOT NULL, published_at TEXT NOT NULL, discovered_at INTEGER NOT NULL,
  relevance_score REAL NOT NULL, confidence REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  dedup_key TEXT NOT NULL UNIQUE, expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tg_knowledge_queue ON telegram_knowledge_candidates(status, expires_at, discovered_at);
CREATE TABLE IF NOT EXISTS web_knowledge_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT, query TEXT NOT NULL, normalized_query TEXT NOT NULL,
  title TEXT NOT NULL, url TEXT NOT NULL, publisher TEXT NOT NULL DEFAULT '', snippet TEXT NOT NULL DEFAULT '',
  extracted_relevant_text TEXT NOT NULL DEFAULT '', language TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL,
  freshness TEXT NOT NULL DEFAULT '', discovered_at INTEGER NOT NULL, trace_id TEXT NOT NULL DEFAULT '',
  content_hash TEXT NOT NULL, semantic_key TEXT NOT NULL, source_urls_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'pending', expires_at INTEGER NOT NULL,
  UNIQUE(url, content_hash, semantic_key)
);
CREATE INDEX IF NOT EXISTS idx_web_knowledge_queue ON web_knowledge_candidates(status, expires_at, discovered_at);
'''

# Import sticker models from stickers module
from .stickers.models import Sticker, StickerSet, StickerCandidate, StickerStats


class ZeroStore:
    def __init__(self, db_path: str, *, recent_messages_limit: int = 80, long_term_limit: int = 120):
        self.db_path = Path(db_path)
        self.recent_messages_limit = max(1, int(recent_messages_limit))
        self.long_term_limit = max(1, int(long_term_limit))
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.db_path.parent, 0o700)
        self._restrict_db_permissions()
        self._lock = asyncio.Lock()
        self._init_db()
        self._restrict_db_permissions()

    def _restrict_db_permissions(self) -> None:
        for path in (self.db_path, Path(f'{self.db_path}-wal'), Path(f'{self.db_path}-shm')):
            if path.exists():
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=5000')
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            legacy_profiles = int(conn.execute('SELECT COUNT(*) AS c FROM user_profiles').fetchone()['c'])
            if legacy_profiles:
                logger.warning('IDENTITY_LEGACY_AMBIGUOUS count=%s reason=missing_chat_scope', legacy_profiles)
                logger.info('IDENTITY_MIGRATION_SKIPPED reason=legacy_profiles_not_auto_assigned')
            # CREATE TABLE IF NOT EXISTS cannot extend pre-existing databases.
            # This idempotent migration stores only an aggregate sticker score.
            from .world_model import migrate_world_model
            migrate_world_model(self.db_path)
            from .procedural_memory import migrate_procedural_memory
            migrate_procedural_memory(self.db_path)
            from .experience_memory import migrate_experience_memory
            migrate_experience_memory(self.db_path)
            from .semantic_memory import migrate_semantic_user_memory
            migrate_semantic_user_memory(self.db_path)
            from .office.db import migrate_office
            migrate_office(self.db_path)
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(stickers)')}
            if 'reaction_score' not in columns:
                conn.execute('ALTER TABLE stickers ADD COLUMN reaction_score INTEGER NOT NULL DEFAULT 0')
            for name, definition in {
                'chat_id': 'INTEGER', 'sender_id': 'INTEGER', 'message_id': 'INTEGER',
                'send_count': 'INTEGER NOT NULL DEFAULT 0', 'last_sent_at': 'INTEGER',
                'inferred_mood': 'TEXT', 'sticker_type': "TEXT NOT NULL DEFAULT 'static'",
                'is_available': 'INTEGER NOT NULL DEFAULT 1', 'failure_count': 'INTEGER NOT NULL DEFAULT 0',
            }.items():
                if name not in columns:
                    conn.execute(f'ALTER TABLE stickers ADD COLUMN {name} {definition}')
            profile_columns = {row['name'] for row in conn.execute('PRAGMA table_info(user_profiles_scoped)')}
            for name, definition in {'username': "TEXT NOT NULL DEFAULT ''", 'display_name': "TEXT NOT NULL DEFAULT ''"}.items():
                if name not in profile_columns:
                    conn.execute(f'ALTER TABLE user_profiles_scoped ADD COLUMN {name} {definition}')
            recent_columns = {row['name'] for row in conn.execute('PRAGMA table_info(recent_messages)')}
            for name, definition in {
                'platform': 'TEXT', 'account_scope': 'TEXT', 'telegram_message_id': 'INTEGER',
                'reply_to_message_id': 'INTEGER', 'thread_id': 'INTEGER',
                'sender_username': "TEXT NOT NULL DEFAULT ''",
                'sender_display_name': "TEXT NOT NULL DEFAULT ''",
                'trace_id': "TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in recent_columns:
                    conn.execute(f'ALTER TABLE recent_messages ADD COLUMN {name} {definition}')
            conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_recent_messages_telegram_scope ON recent_messages(platform,account_scope,chat_id,telegram_message_id) WHERE telegram_message_id IS NOT NULL')

    async def github_trending_seen(self, repo_full_name: str) -> bool:
        async with self._lock:
            with self._conn() as conn:
                return conn.execute('SELECT 1 FROM github_trending_items WHERE repo_full_name=? AND last_introduced_at IS NOT NULL', (repo_full_name,)).fetchone() is not None

    async def github_trending_mark(self, repo_full_name: str, *, rank: int, fingerprint: str, source_url: str) -> None:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute('''INSERT INTO github_trending_items(repo_full_name,last_seen_rank,last_seen_fingerprint,last_introduced_at,intro_count,last_source_url,created_at,updated_at)
                    VALUES(?,?,?,?,1,?,?,?)
                    ON CONFLICT(repo_full_name) DO UPDATE SET last_seen_rank=excluded.last_seen_rank,last_seen_fingerprint=excluded.last_seen_fingerprint,last_introduced_at=excluded.last_introduced_at,intro_count=github_trending_items.intro_count+1,last_source_url=excluded.last_source_url,updated_at=excluded.updated_at''', (repo_full_name, int(rank), fingerprint, now, source_url, now, now))
                conn.commit()

    async def github_trending_seen_only(self, repo_full_name: str, *, rank: int, fingerprint: str, source_url: str) -> None:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute('''INSERT INTO github_trending_items(repo_full_name,last_seen_rank,last_seen_fingerprint,last_source_url,created_at,updated_at)
                    VALUES(?,?,?,?,?,?)
                    ON CONFLICT(repo_full_name) DO UPDATE SET last_seen_rank=excluded.last_seen_rank,last_seen_fingerprint=excluded.last_seen_fingerprint,last_source_url=excluded.last_source_url,updated_at=excluded.updated_at''', (repo_full_name, int(rank), fingerprint, source_url, now, now))
                conn.commit()

    async def panel_list_chats(self, *, query: str = '', chat_id: int | None = None, sender_id: int | None = None, page: int = 1, size: int = 25) -> dict[str, Any]:
        """Bounded read-only panel view over retained conversations."""
        page=max(1,int(page)); size=min(100,max(1,int(size))); where=[]; args=[]
        if query:
            where.append('(text LIKE ? OR sender_label LIKE ?)'); args.extend((f'%{query[:100]}%',f'%{query[:100]}%'))
        if chat_id is not None: where.append('chat_id=?'); args.append(int(chat_id))
        if sender_id is not None: where.append('sender_id=?'); args.append(int(sender_id))
        clause=' WHERE '+' AND '.join(where) if where else ''
        async with self._lock:
            with self._conn() as c:
                total=c.execute('SELECT COUNT(*) FROM recent_messages'+clause,args).fetchone()[0]
                rows=c.execute('SELECT * FROM recent_messages'+clause+' ORDER BY id DESC LIMIT ? OFFSET ?',args+[size,(page-1)*size]).fetchall()
        return {'items':[dict(r) for r in rows],'total':total,'page':page,'size':size}

    async def panel_get_chat(self, item_id: int) -> dict[str, Any] | None:
        async with self._lock:
            with self._conn() as c: row=c.execute('SELECT * FROM recent_messages WHERE id=?',(int(item_id),)).fetchone()
        return dict(row) if row else None

    async def panel_list_dataset(self, dataset: str, *, query: str = '', status: str = '', page: int = 1, size: int = 25) -> dict[str, Any]:
        """Explicit allowlisted, bounded operational datasets for the Owner panel."""
        specs={
            'short':('short_term_context',('active_topic','mood','sensitivity'),'updated_at'),
            'medium':('medium_term_memory',('topic','summary'),'last_referenced_at'),
            'long':('long_term_memory',('category','content'),'updated_at'),
            'semantic':('semantic_user_memory',('category','key','value_json'),'id'),
            'semantic-candidates':('semantic_user_memory_candidates',('category','key','value_json'),'id'),
            'experience':('experience_memory',('topic','root_cause','fix'),'id'),
            'experience-candidates':('experience_memory_candidates',('topic','root_cause','fix'),'id'),
            'procedural':('procedural_memory',('name','risk_level'),'id'),
            'procedural-candidates':('procedural_memory_candidates',('name','risk_level'),'id'),
            'world':('world_entities',('canonical_name','entity_type'),'id'),
            'world-relations':('world_relations',('predicate',),'id'),
            'knowledge-items':('knowledge_items',('title','summary'),'id'),
            'knowledge-runs':('knowledge_runs',('run_id','trace_id','reason'),'started_at'),
            'telegram-knowledge':('telegram_knowledge_candidates',('topic','channel_identifier'),'id'),
            'cron-runs':('cron_runs',('job_id','trace_id','state'),'created_at'),
        }
        if dataset not in specs: raise ValueError('unsupported_panel_dataset')
        table,search_cols,order=specs[dataset]; page=max(1,int(page));size=min(100,max(1,int(size)));where=[];args=[]
        if query:
            where.append('('+ ' OR '.join(f'{col} LIKE ?' for col in search_cols)+')');args.extend([f'%{query[:100]}%']*len(search_cols))
        if status:
            with self._conn() as c: columns={r['name'] for r in c.execute(f'PRAGMA table_info({table})')}
            if 'status' in columns:where.append('status=?');args.append(status[:40])
        clause=' WHERE '+' AND '.join(where) if where else ''
        async with self._lock:
            with self._conn() as c:
                total=c.execute(f'SELECT COUNT(*) FROM {table}'+clause,args).fetchone()[0]
                rows=c.execute(f'SELECT * FROM {table}'+clause+f' ORDER BY {order} DESC LIMIT ? OFFSET ?',args+[size,(page-1)*size]).fetchall()
        return {'items':[dict(r) for r in rows],'total':total,'page':page,'size':size}

    async def panel_get_dataset_item(self, dataset: str, item_id: str) -> dict[str, Any] | None:
        specs={
            'short':('short_term_context','chat_id'),'medium':('medium_term_memory','event_id'),'long':('long_term_memory','memory_id'),
            'semantic':('semantic_user_memory','id'),'semantic-candidates':('semantic_user_memory_candidates','id'),
            'experience':('experience_memory','id'),'experience-candidates':('experience_memory_candidates','id'),
            'procedural':('procedural_memory','id'),'procedural-candidates':('procedural_memory_candidates','id'),
            'world':('world_entities','id'),'world-relations':('world_relations','id'),
        }
        if dataset not in specs: raise ValueError('unsupported_panel_dataset')
        table,pk=specs[dataset]
        async with self._lock:
            with self._conn() as c:
                row=c.execute(f'SELECT * FROM {table} WHERE {pk}=?',(item_id,)).fetchone()
                result=dict(row) if row else None
                if result and dataset=='world': result['relations']=[dict(r) for r in c.execute('SELECT * FROM world_relations WHERE subject_entity_id=? OR object_entity_id=?',(item_id,item_id)).fetchall()]
        return result

    async def panel_get_knowledge_item(self, item_id: int) -> dict[str, Any] | None:
        async with self._lock:
            with self._conn() as c:
                row=c.execute('SELECT * FROM knowledge_items WHERE id=?',(int(item_id),)).fetchone()
                sources=[dict(r) for r in c.execute('SELECT * FROM knowledge_sources WHERE knowledge_item_id=?',(int(item_id),)).fetchall()]
        return {'item':dict(row),'sources':sources} if row else None

    async def panel_list_group_users(self, limit: int = 100) -> list[dict[str, Any]]:
        limit=min(100,max(1,int(limit)))
        async with self._lock:
            with self._conn() as c: rows=c.execute('SELECT * FROM group_user_state ORDER BY last_seen DESC LIMIT ?',(limit,)).fetchall()
        return [dict(r) for r in rows]

    async def panel_get_settings(self, allowed_keys: set[str]) -> dict[str, str]:
        keys=sorted(set(allowed_keys))[:100]
        if not keys:return {}
        placeholders=','.join('?' for _ in keys)
        async with self._lock:
            with self._conn() as c: rows=c.execute(f'SELECT key,value FROM settings WHERE key IN ({placeholders})',keys).fetchall()
        return {str(r['key']):str(r['value']) for r in rows}

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
                return row['value'] if row else default
    async def reserve_incoming_message(
        self, *, platform: str, account_scope: str, chat_id: int, message_id: int,
        thread_id: int | None, sender_id: int | None, trace_id: str,
        expires_at: int | None = None,
    ) -> dict[str, Any]:
        """Atomically claim one inbound message across handlers, processes, and restarts."""
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    '''INSERT OR IGNORE INTO incoming_message_dedup
                    (platform,account_scope,chat_id,message_id,thread_id,sender_id,status,trace_id,created_at,updated_at,expires_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                    (platform, account_scope, chat_id, message_id, thread_id, sender_id,
                     'processing', trace_id, now, now, expires_at),
                )
                row = conn.execute(
                    '''SELECT status,trace_id,reply_message_id,attempt_count
                       FROM incoming_message_dedup
                       WHERE platform=? AND account_scope=? AND chat_id=? AND message_id=?''',
                    (platform, account_scope, chat_id, message_id),
                ).fetchone()
                conn.commit()
                if row['trace_id'] == trace_id and row['status'] == 'processing':
                    return {'claimed': True, 'status': 'processing', 'trace_id': trace_id, 'attempt_count': row['attempt_count']}
                return {'claimed': False, **dict(row)}

    async def expire_stale_incoming_messages(self, lease_seconds: int = 300) -> int:
        """Release claims left behind by a crashed handler."""
        cutoff = int(time.time()) - max(1, int(lease_seconds))
        async with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    """UPDATE incoming_message_dedup
                       SET status='expired', reason='stale_processing_reclaimed', updated_at=?, finished_at=?
                       WHERE status='processing' AND updated_at<?""",
                    (int(time.time()), int(time.time()), cutoff),
                )
                conn.commit()
                return int(cur.rowcount)

    async def mark_incoming_message_replied(
        self, *, platform: str, account_scope: str, chat_id: int, message_id: int,
        reply_message_id: int | None, trace_id: str,
    ) -> None:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    '''UPDATE incoming_message_dedup
                       SET status='replied',reply_message_id=?,trace_id=?,updated_at=?,finished_at=?
                       WHERE platform=? AND account_scope=? AND chat_id=? AND message_id=? AND status='processing' ''',
                    (reply_message_id, trace_id, now, now, platform, account_scope, chat_id, message_id),
                )
                conn.commit()

    async def mark_incoming_message_failed(
        self, *, platform: str, account_scope: str, chat_id: int, message_id: int,
        trace_id: str, reason: str,
    ) -> None:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    '''UPDATE incoming_message_dedup
                       SET status='failed',reason=?,trace_id=?,updated_at=?,finished_at=?
                       WHERE platform=? AND account_scope=? AND chat_id=? AND message_id=? AND status='processing' ''',
                    (reason[:300], trace_id, now, now, platform, account_scope, chat_id, message_id),
                )
                conn.commit()

    async def mark_incoming_message_expired(
        self, *, platform: str, account_scope: str, chat_id: int, message_id: int,
        trace_id: str, reason: str,
    ) -> None:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    '''UPDATE incoming_message_dedup
                       SET status='expired',reason=?,trace_id=?,updated_at=?,finished_at=?
                       WHERE platform=? AND account_scope=? AND chat_id=? AND message_id=? AND status='processing' ''',
                    (reason[:300], trace_id, now, now, platform, account_scope, chat_id, message_id),
                )
                conn.commit()

    async def set_setting(self, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        async with self._lock:
            with self._conn() as conn:
                conn.execute('INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, payload))
                conn.commit()

    async def save_telegram_search_state(self, *, state_key: str, chat_id: int, sender_id: int, thread_id: int | None, reply_to_message_id: int | None, search_session_id: str, trace_id: str, query: str, intent: str, payload: dict, expires_at: int) -> None:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute('INSERT OR REPLACE INTO telegram_search_state(state_key,chat_id,sender_id,thread_id,reply_to_message_id,search_session_id,trace_id,query,intent,payload_json,created_at,expires_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (state_key, chat_id, sender_id, thread_id, reply_to_message_id, search_session_id, trace_id, query[:1000], intent, json.dumps(payload, ensure_ascii=False), now, expires_at, 'active'))
                conn.commit()

    async def get_telegram_search_state(self, *, chat_id: int, sender_id: int, thread_id: int | None, reply_to_message_id: int | None, now: int | None = None) -> dict[str, Any] | None:
        now = int(now or time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute("UPDATE telegram_search_state SET status='expired' WHERE status='active' AND expires_at<?", (now,))
                row = conn.execute("SELECT * FROM telegram_search_state WHERE chat_id=? AND sender_id=? AND status='active' AND expires_at>=? AND ((reply_to_message_id IS NOT NULL AND reply_to_message_id=?) OR (reply_to_message_id IS NULL AND (? IS NULL OR thread_id=?))) ORDER BY CASE WHEN reply_to_message_id=? THEN 0 ELSE 1 END, created_at DESC LIMIT 1", (chat_id, sender_id, now, reply_to_message_id, reply_to_message_id, thread_id, reply_to_message_id)).fetchone()
                conn.commit()
                if not row: return None
                data = dict(row); data['payload'] = json.loads(data.pop('payload_json') or '{}'); return data

    async def expire_telegram_search_state(self) -> int:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                count = conn.execute("UPDATE telegram_search_state SET status='expired' WHERE status='active' AND expires_at<?", (now,)).rowcount; conn.commit(); return count

    async def get_telegram_search_cache(self, cache_key: str) -> dict[str, Any] | None:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute("UPDATE telegram_search_cache SET status='expired' WHERE status='active' AND expires_at<?", (now,))
                row = conn.execute("SELECT * FROM telegram_search_cache WHERE cache_key=? AND status='active' AND expires_at>=?", (cache_key, now)).fetchone(); conn.commit()
                if not row: return None
                data=dict(row); data['payload']=json.loads(data.pop('payload_json') or '{}'); return data

    async def set_telegram_search_cache(self, *, cache_key: str, normalized_query: str, intent: str, provider_set: str, language: str, freshness: str, visibility_scope: str, payload: dict, expires_at: int) -> None:
        now=int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute('INSERT OR REPLACE INTO telegram_search_cache(cache_key,normalized_query,intent,provider_set,language,freshness,visibility_scope,payload_json,created_at,expires_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (cache_key,normalized_query[:500],intent,provider_set,language,freshness,visibility_scope,json.dumps(payload,ensure_ascii=False),now,expires_at,'active')); conn.commit()

    async def clear_telegram_search_cache(self) -> int:
        async with self._lock:
            with self._conn() as conn:
                count=conn.execute("UPDATE telegram_search_cache SET status='invalidated' WHERE status='active'").rowcount; conn.commit(); return count

    async def telegram_search_cache_status(self) -> dict[str, int]:
        now=int(time.time())
        async with self._lock:
            with self._conn() as conn:
                return {'active': int(conn.execute("SELECT COUNT(*) FROM telegram_search_cache WHERE status='active' AND expires_at>=?",(now,)).fetchone()[0]), 'expired': int(conn.execute("SELECT COUNT(*) FROM telegram_search_cache WHERE status IN ('expired','invalidated') OR expires_at<?",(now,)).fetchone()[0])}

    async def consume_telegram_search_limit(self, *, account_scope: str, kind: str, daily_limit: int, day: str | None = None) -> tuple[bool, int, int]:
        day=day or datetime.now(timezone.utc).strftime('%Y-%m-%d'); now=int(time.time())
        async with self._lock:
            with self._conn() as conn:
                row=conn.execute('SELECT used_count FROM telegram_search_limits WHERE account_scope=? AND day=? AND kind=?',(account_scope,day,kind)).fetchone(); used=int(row['used_count']) if row else 0
                if used >= int(daily_limit): return False, used, 86400-(int(time.time())%86400)
                used += 1; conn.execute('INSERT INTO telegram_search_limits(account_scope,day,kind,used_count,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(account_scope,day,kind) DO UPDATE SET used_count=excluded.used_count,updated_at=excluded.updated_at',(account_scope,day,kind,used,now)); conn.commit(); return True, used, 86400-(int(time.time())%86400)

    async def telegram_search_limit_status(self, *, account_scope: str, day: str | None = None) -> list[dict[str, Any]]:
        day=day or datetime.now(timezone.utc).strftime('%Y-%m-%d')
        async with self._lock:
            with self._conn() as conn: return [dict(r) for r in conn.execute('SELECT kind,used_count,updated_at FROM telegram_search_limits WHERE account_scope=? AND day=? ORDER BY kind',(account_scope,day)).fetchall()]

    async def reset_telegram_search_limits(self, *, account_scope: str, day: str | None = None) -> int:
        day=day or datetime.now(timezone.utc).strftime('%Y-%m-%d')
        async with self._lock:
            with self._conn() as conn: count=conn.execute('DELETE FROM telegram_search_limits WHERE account_scope=? AND day=?',(account_scope,day)).rowcount; conn.commit(); return count

    async def enqueue_telegram_knowledge_candidate(self, *, topic: str, source_provider: str, channel_identifier: str, message_id: int | None, canonical_link: str, text_excerpt: str, published_at: str, relevance_score: float, confidence: float, dedup_key: str, expires_at: int) -> str:
        now=int(time.time())
        async with self._lock:
            with self._conn() as conn:
                try:
                    conn.execute('INSERT INTO telegram_knowledge_candidates(topic,source_provider,channel_identifier,message_id,canonical_link,text_excerpt,published_at,discovered_at,relevance_score,confidence,status,dedup_key,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(topic,source_provider,channel_identifier,message_id,canonical_link,text_excerpt[:800],published_at[:80],now,float(relevance_score),float(confidence),'pending',dedup_key,expires_at)); conn.commit(); return 'created'
                except sqlite3.IntegrityError: return 'duplicate'

    async def claim_telegram_knowledge_candidates(self, limit: int = 5, lease_seconds: int = 900) -> list[dict[str, Any]]:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute("UPDATE telegram_knowledge_candidates SET status='pending' WHERE status LIKE 'processing:%' AND CAST(substr(status,13) AS INTEGER)<?", (now - lease_seconds,))
                conn.execute("UPDATE telegram_knowledge_candidates SET status='expired' WHERE status='pending' AND expires_at<?", (now,))
                rows = [dict(r) for r in conn.execute("SELECT * FROM telegram_knowledge_candidates WHERE status='pending' ORDER BY relevance_score DESC, discovered_at ASC LIMIT ?", (limit,)).fetchall()]
                for row in rows:
                    conn.execute("UPDATE telegram_knowledge_candidates SET status=? WHERE id=? AND status='pending'", (f'processing:{now}', row['id']))
                conn.commit()
                return rows

    async def update_telegram_knowledge_candidate(self, candidate_id: int, status: str) -> None:
        async with self._lock:
            with self._conn() as conn: conn.execute('UPDATE telegram_knowledge_candidates SET status=? WHERE id=?',(status,candidate_id)); conn.commit()

    async def enqueue_web_knowledge_candidate(self, *, query: str, normalized_query: str, title: str, url: str, publisher: str, snippet: str, extracted_relevant_text: str, language: str, confidence: float, freshness: str, trace_id: str, content_hash: str, semantic_key: str, expires_at: int) -> str:
        now=int(time.time()); source_urls=json.dumps([url], ensure_ascii=False)
        async with self._lock:
            with self._conn() as conn:
                existing=conn.execute('SELECT id,source_urls_json FROM web_knowledge_candidates WHERE url=? OR content_hash=? OR semantic_key=? LIMIT 1',(url,content_hash,semantic_key)).fetchone()
                if existing:
                    urls=list(dict.fromkeys(json.loads(existing['source_urls_json'] or '[]')+[url])); conn.execute('UPDATE web_knowledge_candidates SET source_urls_json=?,discovered_at=?,trace_id=? WHERE id=?',(json.dumps(urls,ensure_ascii=False),now,trace_id[:80],existing['id'])); conn.commit(); return 'duplicate'
                try:
                    conn.execute('INSERT INTO web_knowledge_candidates(query,normalized_query,title,url,publisher,snippet,extracted_relevant_text,language,confidence,freshness,discovered_at,trace_id,content_hash,semantic_key,source_urls_json,status,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(query[:500],normalized_query[:500],title[:300],url[:1000],publisher[:160],snippet[:800],extracted_relevant_text[:1500],language[:40],float(confidence),freshness[:40],now,trace_id[:80],content_hash,semantic_key,source_urls,'pending',expires_at)); conn.commit(); return 'created'
                except sqlite3.IntegrityError:
                    return 'duplicate'

    async def claim_web_knowledge_candidates(self, limit: int = 5, lease_seconds: int = 900) -> list[dict[str, Any]]:
        now=int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute("UPDATE web_knowledge_candidates SET status='pending' WHERE status LIKE 'processing:%' AND CAST(substr(status,13) AS INTEGER)<?",(now-lease_seconds,))
                conn.execute("UPDATE web_knowledge_candidates SET status='expired' WHERE status='pending' AND expires_at<?",(now,))
                rows=[dict(r) for r in conn.execute("SELECT * FROM web_knowledge_candidates WHERE status='pending' ORDER BY confidence DESC, discovered_at ASC LIMIT ?",(limit,)).fetchall()]
                for row in rows: conn.execute("UPDATE web_knowledge_candidates SET status=? WHERE id=? AND status='pending'",(f'processing:{now}',row['id']))
                conn.commit(); return rows

    async def update_web_knowledge_candidate(self, candidate_id: int, status: str) -> None:
        async with self._lock:
            with self._conn() as conn: conn.execute('UPDATE web_knowledge_candidates SET status=? WHERE id=?',(status,candidate_id)); conn.commit()

    async def web_knowledge_queue_status(self) -> dict[str, int]:
        async with self._lock:
            with self._conn() as conn: return {status:int(conn.execute('SELECT COUNT(*) FROM web_knowledge_candidates WHERE status=?',(status,)).fetchone()[0]) for status in ('pending','processed','rejected','duplicate','expired')}

    async def clear_web_knowledge_candidates(self) -> int:
        async with self._lock:
            with self._conn() as conn: count=conn.execute("UPDATE web_knowledge_candidates SET status='expired' WHERE status='pending'").rowcount; conn.commit(); return count

    async def append_recent(self, chat_id: int, sender_id: int, sender_label: str, role: str, text: str, *, platform: str | None = None, account_scope: str | None = None, telegram_message_id: int | None = None, reply_to_message_id: int | None = None, thread_id: int | None = None, sender_username: str = '', sender_display_name: str = '', trace_id: str = '') -> None:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute('INSERT OR IGNORE INTO recent_messages(chat_id,sender_id,sender_label,role,text,platform,account_scope,telegram_message_id,reply_to_message_id,thread_id,sender_username,sender_display_name,trace_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (chat_id, sender_id, sender_label, role, text, platform, account_scope, telegram_message_id, reply_to_message_id, thread_id, sender_username, sender_display_name, trace_id, now))
                conn.execute('DELETE FROM recent_messages WHERE chat_id=? AND id NOT IN (SELECT id FROM recent_messages WHERE chat_id=? ORDER BY id DESC LIMIT ?)', (chat_id, chat_id, self.recent_messages_limit))
                conn.commit()

    async def get_reply_chain(self, platform: str, account_scope: str, chat_id: int, message_id: int, *, max_depth: int = 8) -> list[dict[str, Any]]:
        chain: list[dict[str, Any]] = []
        seen = {int(message_id)}
        current = int(message_id)
        async with self._lock:
            with self._conn() as conn:
                for _ in range(max(0, min(int(max_depth), 32))):
                    row = conn.execute(
                        'SELECT * FROM recent_messages WHERE platform=? AND account_scope=? AND chat_id=? AND telegram_message_id=? LIMIT 1',
                        (platform, account_scope, int(chat_id), current),
                    ).fetchone()
                    parent = int(row['reply_to_message_id'] or 0) if row else 0
                    if not parent or parent in seen:
                        break
                    seen.add(parent)
                    parent_row = conn.execute(
                        'SELECT * FROM recent_messages WHERE platform=? AND account_scope=? AND chat_id=? AND telegram_message_id=? LIMIT 1',
                        (platform, account_scope, int(chat_id), parent),
                    ).fetchone()
                    if not parent_row:
                        break
                    chain.append(dict(parent_row))
                    current = parent
        return chain

    async def get_active_group_chat_ids(self) -> list[int]:
        async with self._lock:
            with self._conn() as conn:
                rows = conn.execute('SELECT DISTINCT chat_id FROM recent_messages WHERE chat_id < 0 ORDER BY chat_id').fetchall()
        return [int(row['chat_id']) for row in rows]

    async def get_group_context_state(self, chat_id: int) -> dict[str, Any]:
        async with self._lock:
            with self._conn() as conn:
                row=conn.execute('SELECT * FROM group_context_state WHERE chat_id=?',(chat_id,)).fetchone()
                return dict(row) if row else {'chat_id':chat_id,'last_message_id':None,'last_timestamp':None,'summary_json':'{}','summary_version':0,'optimistic_version':0}

    async def get_unconsumed_group_messages(self, chat_id:int, limit:int=60) -> list[dict[str,Any]]:
        async with self._lock:
            with self._conn() as conn:
                rows=conn.execute('SELECT m.* FROM recent_messages m LEFT JOIN group_context_consumed c ON c.chat_id=m.chat_id AND c.message_id=m.telegram_message_id WHERE m.chat_id=? AND m.telegram_message_id IS NOT NULL AND c.message_id IS NULL ORDER BY m.id ASC LIMIT ?',(chat_id,max(1,limit))).fetchall()
                return [dict(r) for r in rows]

    async def commit_group_context(self, chat_id:int, rows:list[dict[str,Any]], summary:dict[str,Any]|None, expected_version:int) -> bool:
        if not rows:return True
        now=int(time.time()); last=rows[-1]
        async with self._lock:
            with self._conn() as conn:
                conn.execute('BEGIN IMMEDIATE'); current=conn.execute('SELECT optimistic_version FROM group_context_state WHERE chat_id=?',(chat_id,)).fetchone(); version=int(current['optimistic_version']) if current else 0
                if version!=expected_version: conn.execute('ROLLBACK'); return False
                for row in rows: conn.execute('INSERT OR IGNORE INTO group_context_consumed(chat_id,message_id,edited_at) VALUES(?,?,0)',(chat_id,int(row['telegram_message_id'])))
                old=conn.execute('SELECT summary_version FROM group_context_state WHERE chat_id=?',(chat_id,)).fetchone(); sv=(int(old['summary_version']) if old else 0)+(1 if summary is not None else 0)
                payload=json.dumps(summary or (json.loads(conn.execute('SELECT summary_json FROM group_context_state WHERE chat_id=?',(chat_id,)).fetchone()['summary_json']) if old else {}),ensure_ascii=False)
                conn.execute('INSERT INTO group_context_state(chat_id,last_message_id,last_timestamp,summary_json,summary_version,optimistic_version,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET last_message_id=excluded.last_message_id,last_timestamp=excluded.last_timestamp,summary_json=excluded.summary_json,summary_version=excluded.summary_version,optimistic_version=excluded.optimistic_version,updated_at=excluded.updated_at',(chat_id,int(last['telegram_message_id']),int(last['created_at']),payload,sv,version+1,now));conn.execute('COMMIT');return True

    async def get_recent(self, chat_id: int, limit: int = 40) -> list[dict[str, Any]]:
        async with self._lock:
            with self._conn() as conn:
                rows = conn.execute('SELECT * FROM recent_messages WHERE chat_id=? ORDER BY id DESC LIMIT ?', (chat_id, limit)).fetchall()
                return [dict(r) for r in reversed(rows)]

    async def get_recent_since(self, chat_id: int, *, since_ts: int, limit: int = 5000) -> list[dict[str, Any]]:
        async with self._lock:
            with self._conn() as conn:
                rows = conn.execute(
                    'SELECT * FROM recent_messages '
                    'WHERE chat_id=? AND created_at>=? ORDER BY id DESC LIMIT ?',
                    (chat_id, int(since_ts), max(1, int(limit))),
                ).fetchall()
                return [dict(r) for r in reversed(rows)]

    async def upsert_profile(self, chat_id: int, sender_id: int, label: str, *, username: str = '', display_name: str = '', nicknames: list[str] | None = None, topics: list[str] | None = None, projects: list[str] | None = None, style_notes: list[str] | None = None, reputation_delta: int = 0) -> None:
        """Update only the canonical (chat_id, sender_id) profile. Legacy profiles remain untouched."""
        from .identity import log_identity_resolved
        log_identity_resolved(chat_id, sender_id)
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                collision = conn.execute('SELECT 1 FROM user_profiles_scoped WHERE chat_id=? AND sender_id<>? AND lower(label)=lower(?) LIMIT 1', (chat_id, sender_id, label)).fetchone()
                if collision:
                    logger.warning('IDENTITY_COLLISION_DETECTED chat_id=%s sender_id=%s reason=display_label_not_identity', chat_id, sender_id)
                row = conn.execute('SELECT * FROM user_profiles_scoped WHERE chat_id=? AND sender_id=?', (chat_id, sender_id)).fetchone()
                current = dict(row) if row else None
                data = {
                    'username': username.strip().lstrip('@') or (current.get('username', '') if current else ''),
                    'display_name': display_name.strip() or (current.get('display_name', '') if current else ''),
                    'nicknames_json': json.dumps(sorted(set((json.loads(current['nicknames_json']) if current else []) + (nicknames or []))), ensure_ascii=False),
                    'topics_json': json.dumps(sorted(set((json.loads(current['topics_json']) if current else []) + (topics or []))), ensure_ascii=False),
                    'projects_json': json.dumps(sorted(set((json.loads(current['projects_json']) if current else []) + (projects or []))), ensure_ascii=False),
                    'style_notes_json': json.dumps(sorted(set((json.loads(current['style_notes_json']) if current else []) + (style_notes or []))), ensure_ascii=False),
                    'reputation': int((current['reputation'] if current else 0)) + reputation_delta,
                }
                conn.execute(
                    'INSERT INTO user_profiles_scoped(chat_id, sender_id, label, username, display_name, nicknames_json, topics_json, projects_json, style_notes_json, reputation, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
                    'ON CONFLICT(chat_id, sender_id) DO UPDATE SET label=excluded.label, username=CASE WHEN excluded.username<>\'\' THEN excluded.username ELSE user_profiles_scoped.username END, display_name=CASE WHEN excluded.display_name<>\'\' THEN excluded.display_name ELSE user_profiles_scoped.display_name END, nicknames_json=excluded.nicknames_json, topics_json=excluded.topics_json, projects_json=excluded.projects_json, style_notes_json=excluded.style_notes_json, reputation=excluded.reputation, updated_at=excluded.updated_at',
                    (chat_id, sender_id, label, data['username'], data['display_name'], data['nicknames_json'], data['topics_json'], data['projects_json'], data['style_notes_json'], data['reputation'], now),
                )
                conn.commit()

    async def find_users_by_label(self, chat_id: int, label: str) -> list[int]:
        normalized = (label or '').strip().lstrip('@').casefold()
        if not normalized:
            return []
        async with self._lock:
            with self._conn() as conn:
                rows = conn.execute('SELECT sender_id FROM user_profiles_scoped WHERE chat_id=? AND (lower(ltrim(label, "@"))=? OR lower(username)=? OR lower(display_name)=?)', (chat_id, normalized, normalized, normalized)).fetchall()
        return [int(r['sender_id']) for r in rows]

    async def find_users_by_identity(self, chat_id: int, identity: str) -> list[int]:
        target = (identity or '').strip().lstrip('@').casefold()
        if not target:
            return []
        if target.isdigit():
            async with self._lock:
                with self._conn() as conn:
                    row = conn.execute('SELECT 1 FROM user_profiles_scoped WHERE chat_id=? AND sender_id=?', (chat_id, int(target))).fetchone()
            return [int(target)] if row else []
        found = set(await self.find_users_by_label(chat_id, target))
        async with self._lock:
            with self._conn() as conn:
                rows = conn.execute("SELECT sender_id,value_json FROM semantic_user_memory WHERE chat_id=? AND status='active' AND category='identity'", (chat_id,)).fetchall()
                profiles = conn.execute('SELECT sender_id,nicknames_json FROM user_profiles_scoped WHERE chat_id=?', (chat_id,)).fetchall()
        for row in rows:
            try:
                value = json.loads(row['value_json'])
            except Exception:
                value = row['value_json']
            if str(value).strip().casefold() == target:
                found.add(int(row['sender_id']))
        for row in profiles:
            try:
                names = json.loads(row['nicknames_json'] or '[]')
            except Exception:
                names = []
            if any(str(name).strip().casefold() == target for name in names):
                found.add(int(row['sender_id']))
        return sorted(found)

    async def find_identity_mentions(self, chat_id: int, text: str) -> dict[str, list[int]]:
        """Resolve only exact chat-scoped profile/approved-identity aliases present in text."""
        hay = (text or '').casefold()
        if not hay:
            return {}
        aliases: dict[str, set[int]] = {}
        async with self._lock:
            with self._conn() as conn:
                profiles = conn.execute(
                    'SELECT sender_id,label,username,display_name,nicknames_json FROM user_profiles_scoped WHERE chat_id=?',
                    (int(chat_id),),
                ).fetchall()
                semantic = conn.execute(
                    "SELECT sender_id,value_json FROM semantic_user_memory WHERE chat_id=? AND status='active' AND category='identity'",
                    (int(chat_id),),
                ).fetchall()
        for row in profiles:
            names = [row['label'], row['username'], row['display_name']]
            try:
                names.extend(json.loads(row['nicknames_json'] or '[]'))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            for name in names:
                alias = str(name or '').strip().lstrip('@').casefold()
                if len(alias) >= 2:
                    aliases.setdefault(alias, set()).add(int(row['sender_id']))
        for row in semantic:
            try:
                value = json.loads(row['value_json'])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            alias = str(value or '').strip().lstrip('@').casefold()
            if len(alias) >= 2:
                aliases.setdefault(alias, set()).add(int(row['sender_id']))
        found: dict[str, list[int]] = {}
        for alias, sender_ids in aliases.items():
            explicit_username = f'@{alias}' in hay
            exact_name = re.search(rf'(?<![\wآ-ی‌]){re.escape(alias)}(?![\wآ-ی‌])', hay) is not None
            if explicit_username or exact_name:
                found[alias] = sorted(sender_ids)
        return found

    async def get_user_notes(self, chat_id: int, sender_id: int, query: str, *, limit: int = 6) -> list[dict[str, Any]]:
        terms = {x.casefold() for x in re.findall(r'[\wآ-ی‌]{3,}', query or '')}
        async with self._lock:
            with self._conn() as conn:
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_memory_notes'").fetchone():
                    return []
                rows = conn.execute("SELECT id,section,content,source_message_id FROM user_memory_notes WHERE chat_id=? AND sender_id=? AND status='active' ORDER BY created_at DESC LIMIT 100", (int(chat_id), int(sender_id))).fetchall()
                selected = [dict(row) for row in rows if not terms or terms & {x.casefold() for x in re.findall(r'[\wآ-ی‌]{3,}', row['content'] or '')}][:max(0, min(int(limit), 20))]
                if selected:
                    now = int(time.time())
                    conn.executemany('UPDATE user_memory_notes SET last_used_at=? WHERE id=?', [(now, row['id']) for row in selected])
                    conn.commit()
        return selected

    async def get_profile(self, chat_id: int, sender_id: int) -> dict[str, Any] | None:
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute('SELECT * FROM user_profiles_scoped WHERE chat_id=? AND sender_id=?', (chat_id, sender_id)).fetchone()
                return dict(row) if row else None

    async def top_users(self, chat_id: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
        async with self._lock:
            with self._conn() as conn:
                if chat_id is None:
                    rows = conn.execute('SELECT chat_id, sender_id, label, reputation FROM user_profiles_scoped ORDER BY reputation DESC, updated_at DESC LIMIT ?', (limit,)).fetchall()
                else:
                    rows = conn.execute('SELECT chat_id, sender_id, label, reputation FROM user_profiles_scoped WHERE chat_id=? ORDER BY reputation DESC, updated_at DESC LIMIT ?', (chat_id, limit)).fetchall()
                return [dict(r) for r in rows]

    async def memory_audit_event(self, event_type: str, layer: str, chat_id: int, *, object_id: str | None = None, actor_user_id: int | None = None, trace_id: str = '-', details: dict[str, Any] | None = None) -> None:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute('INSERT INTO memory_audit(event_type,layer,chat_id,object_id,actor_user_id,trace_id,details_json,created_at) VALUES (?,?,?,?,?,?,?,?)', (event_type, layer, chat_id, object_id, actor_user_id, trace_id, json.dumps(details or {}, ensure_ascii=False), now))
                conn.commit()

    async def upsert_short_term_context(self, chat_id: int, **fields: Any) -> None:
        audit_details = fields.pop('_audit_details', {})
        allowed = {'active_topic','active_participants_json','addressed_to_zero','conversation_pairs_json','mood','sensitivity','question_unanswered','zero_recent_reply_count','negative_feedback_score','should_reply','should_react','should_wait','should_ignore','expires_at'}
        values = {k: v for k, v in fields.items() if k in allowed}
        now = int(time.time()); values.setdefault('updated_at', now); values.setdefault('expires_at', now + 6 * 3600)
        cols = ['chat_id'] + list(values); args = [chat_id] + [values[k] for k in values]
        placeholders = ','.join('?' for _ in cols)
        updates = ','.join(f'{k}=excluded.{k}' for k in values)
        async with self._lock:
            with self._conn() as conn:
                conn.execute(f'INSERT INTO short_term_context({",".join(cols)}) VALUES ({placeholders}) ON CONFLICT(chat_id) DO UPDATE SET {updates}', args)
                conn.commit()
        await self.memory_audit_event('MEMORY_SHORT_UPDATED', 'short', chat_id, details=audit_details)

    async def merge_short_term_context(self, chat_id: int, *, sender_id: int, message_id: int, topic: str, addressed_to_zero: int, mood: str, sensitivity: str, question_unanswered: int, should_reply: int, should_react: int, should_wait: int, should_ignore: int, audit_details: dict[str, Any] | None = None) -> None:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute('SELECT * FROM short_term_context WHERE chat_id=?', (chat_id,)).fetchone()
                current = dict(row) if row else {}
                participants = set(json.loads(current.get('active_participants_json') or '[]'))
                participants.add(int(sender_id))
                pairs = json.loads(current.get('conversation_pairs_json') or '[]')
                pairs.append({'sender_id': int(sender_id), 'message_id': int(message_id)})
                values = {
                    'active_topic': topic or current.get('active_topic', ''),
                    'active_participants_json': json.dumps(sorted(participants)),
                    'addressed_to_zero': addressed_to_zero,
                    'conversation_pairs_json': json.dumps(pairs[-12:], ensure_ascii=False),
                    'mood': mood,
                    'sensitivity': sensitivity,
                    'question_unanswered': question_unanswered,
                    'should_reply': should_reply,
                    'should_react': should_react,
                    'should_wait': should_wait,
                    'should_ignore': should_ignore,
                    'updated_at': now,
                    'expires_at': now + 6 * 3600,
                }
                cols = ['chat_id'] + list(values); args = [chat_id] + [values[k] for k in values]
                conn.execute(f"INSERT INTO short_term_context({','.join(cols)}) VALUES ({','.join('?' for _ in cols)}) ON CONFLICT(chat_id) DO UPDATE SET " + ','.join(f'{k}=excluded.{k}' for k in values), args)
                conn.commit()
        await self.memory_audit_event('MEMORY_SHORT_UPDATED', 'short', chat_id, details=audit_details or {})

    async def record_media_context(self, chat_id: int, message_id: int, sender_id: int, media_type: str, caption: str = '', reply_to_message_id: int | None = None, summary: str = '', ttl_seconds: int = 6 * 3600) -> None:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute('INSERT OR REPLACE INTO short_term_media_context(media_id,chat_id,message_id,sender_id,media_type,caption,reply_to_message_id,summary,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)', (f'{chat_id}:{message_id}', chat_id, message_id, sender_id, media_type, (caption or '')[:500], reply_to_message_id, (summary or '')[:800], now, now + ttl_seconds))
                conn.commit()
        await self.memory_audit_event('MEMORY_SHORT_DETAIL_UPDATED', 'short', chat_id, object_id=str(message_id), details={'media_type':media_type,'has_caption':bool(caption)})

    async def get_recent_media_context(self, chat_id: int, query: str = '', limit: int = 5) -> list[dict[str, Any]]:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                rows = conn.execute('SELECT * FROM short_term_media_context WHERE chat_id=? AND expires_at>=? ORDER BY created_at DESC LIMIT ?', (chat_id, now, max(limit * 3, 15))).fetchall()
        terms = set(re.findall(r'[\wآ-ی‌]{3,}', (query or '').lower()))
        wants_media = any(x in (query or '').lower() for x in ('چی فرست', 'چه فرست', 'این چی', 'اینو دیدی', 'درباره این', 'اینو توضیح', 'gif', 'عکس', 'تصویر', 'استیکر', 'media'))
        result = []
        for row in rows:
            item = dict(row); hay = f"{item['media_type']} {item['caption']} {item['summary']}".lower()
            if wants_media or not terms or any(t in hay for t in terms):
                item['relevance_score'] = 1.0 if wants_media else 0.5
                item['recency_score'] = 1.0
                result.append(item)
            if len(result) >= limit: break
        return result

    async def update_daily_summary(self, chat_id: int, date_key: str | None = None) -> dict[str, Any]:
        from .memory import detect_mood, detect_topics
        date_key = date_key or datetime.now().strftime('%Y-%m-%d')
        start = int(datetime.strptime(date_key, '%Y-%m-%d').timestamp()); end = start + 86400
        async with self._lock:
            with self._conn() as conn:
                rows = [dict(r) for r in conn.execute('SELECT sender_id,role,text,created_at FROM recent_messages WHERE chat_id=? AND created_at>=? AND created_at<? ORDER BY created_at ASC', (chat_id, start, end)).fetchall()]
        user_rows = [r for r in rows if r.get('role') == 'user']
        topics = {}
        for row in user_rows:
            for topic in detect_topics(row.get('text','')):
                topics[topic] = topics.get(topic, 0) + 1
        active_members = sorted({int(r.get('sender_id')) for r in user_rows if str(r.get('sender_id', '')).lstrip('-').isdigit()})[:30]
        summary = {'date':date_key, 'topics':sorted(topics, key=topics.get, reverse=True)[:8], 'active_members':active_members, 'message_count':len(user_rows), 'open_questions':sum('?' in (r.get('text') or '') or '؟' in (r.get('text') or '') for r in user_rows), 'mood':detect_mood(user_rows[0]['text']) if user_rows else 'neutral'}
        await self.set_setting(f'daily_memory_summary:{chat_id}:{date_key}', json.dumps(summary, ensure_ascii=False))
        await self.memory_audit_event('MEMORY_DAILY_SUMMARY_UPDATED', 'short', chat_id, details={'date':date_key,'message_count':len(user_rows),'topic_count':len(summary['topics'])})
        return summary

    async def get_daily_summary(self, chat_id: int, date_key: str | None = None) -> dict[str, Any]:
        date_key = date_key or datetime.now().strftime('%Y-%m-%d')
        raw = await self.get_setting(f'daily_memory_summary:{chat_id}:{date_key}', '{}')
        try: return json.loads(raw or '{}')
        except Exception: return {}

    async def build_period_summary(self, chat_id: int, *, days: int, label: str, as_of: int | None = None) -> dict[str, Any]:
        from .memory import detect_topics
        end = int(as_of or time.time()); start = end - int(days) * 86400
        async with self._lock:
            with self._conn() as conn:
                rows = [dict(r) for r in conn.execute('SELECT id,sender_id,text,created_at,platform,account_scope,telegram_message_id FROM recent_messages WHERE chat_id=? AND role="user" AND created_at>=? AND created_at<=? ORDER BY id ASC', (chat_id, start, end)).fetchall()]
        topic_counts: dict[str, int] = {}
        for row in rows:
            for topic in detect_topics(row.get('text') or ''):
                topic_counts[topic] = topic_counts.get(topic, 0) + 1
        source_rows = rows[-100:]
        telegram_rows = [r for r in source_rows if r.get('telegram_message_id') is not None and r.get('platform') and r.get('account_scope')]
        summary = {'period': label, 'chat_id': chat_id, 'start_ts': start, 'end_ts': end, 'source_count': len(rows), 'source_message_ids': [int(r['telegram_message_id']) for r in telegram_rows], 'source_local_row_ids': [int(r['id']) for r in source_rows], 'source_message_scopes': [f"{r['platform']}:{r['account_scope']}:{chat_id}:{int(r['telegram_message_id'])}" for r in telegram_rows], 'topics': sorted(topic_counts, key=topic_counts.get, reverse=True)[:10], 'topic_counts': {k: topic_counts[k] for k in sorted(topic_counts, key=topic_counts.get, reverse=True)[:10]}, 'participant_count': len({int(r['sender_id']) for r in rows}), 'raw_text_included': False}
        await self.set_setting(f'{label}_memory_summary:{chat_id}', json.dumps(summary, ensure_ascii=False))
        await self.memory_audit_event('MEMORY_PERIOD_SUMMARY_UPDATED', 'medium' if days <= 7 else 'long', chat_id, details={'period': label, 'source_count': len(rows), 'start_ts': start, 'end_ts': end})
        return summary

    async def build_monthly_long_summary(self, chat_id: int, *, as_of: int | None = None) -> dict[str, Any]:
        end = int(as_of or time.time()); start = end - 30 * 86400
        async with self._lock:
            with self._conn() as conn:
                rows = [dict(r) for r in conn.execute('SELECT memory_id,category,content,confidence,created_at FROM long_term_memory WHERE chat_id=? AND status="active" AND created_at>=? AND created_at<=? ORDER BY confidence DESC, updated_at DESC', (chat_id, start, end)).fetchall()]
        seen=set(); facts=[]
        for row in rows:
            key=re.sub(r'\s+', ' ', (row['category']+' '+row['content']).lower()).strip()
            if key in seen: continue
            seen.add(key); facts.append({'category':row['category'], 'content':row['content'][:240], 'confidence':float(row['confidence']), 'memory_id':row['memory_id']})
        summary={'period':'monthly_long','chat_id':chat_id,'start_ts':start,'end_ts':end,'source_count':len(rows),'fact_count':len(facts),'facts':facts[:30],'raw_text_included':False,'deduplicated':True}
        await self.set_setting(f'monthly_long_summary:{chat_id}', json.dumps(summary, ensure_ascii=False))
        await self.memory_audit_event('MEMORY_MONTHLY_LONG_SUMMARY_UPDATED', 'long', chat_id, details={'source_count':len(rows),'fact_count':len(facts),'start_ts':start,'end_ts':end})
        return summary

    async def update_monthly_group_memory(self, chat_id: int, *, actor_user_id: int = 0) -> dict[str, Any]:
        summary = await self.build_period_summary(chat_id, days=30, label='monthly_group')
        topics = ', '.join(summary.get('topics') or []) or 'ندارد'
        content = (f"خلاصهٔ ۳۰ روز اخیر گروه: {summary.get('source_count', 0)} پیام انسانی، "
                   f"{summary.get('participant_count', 0)} عضو فعال. موضوعات پرتکرار: {topics}.")
        memory_id = await self.add_long_memory(chat_id, 'group_monthly_summary', content, created_by=actor_user_id, subject_user_id=None, source_message_ids=summary.get('source_message_ids', []), confidence=.91)
        return {'memory_id': memory_id, **summary}

    async def get_period_summary(self, chat_id: int, label: str) -> dict[str, Any]:
        raw = await self.get_setting(f'{label}_memory_summary:{chat_id}', '{}')
        try: return json.loads(raw or '{}')
        except Exception: return {}

    async def get_monthly_long_summary(self, chat_id: int) -> dict[str, Any]:
        raw = await self.get_setting(f'monthly_long_summary:{chat_id}', '{}')
        try: return json.loads(raw or '{}')
        except Exception: return {}

    async def get_short_term_context(self, chat_id: int) -> dict[str, Any]:
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute('SELECT * FROM short_term_context WHERE chat_id=? AND expires_at>=?', (chat_id, int(time.time()))).fetchone()
                return dict(row) if row else {}

    async def add_medium_memory(self, chat_id: int, topic: str, summary: str, *, participants: list[int] | None = None, source_message_ids: list[int] | None = None, importance: float = 0.5, confidence: float = 0.7, ttl_seconds: int = 14 * 86400, event_id: str | None = None, promotion_candidate: bool = False) -> str:
        event_id = event_id or uuid.uuid4().hex
        now = int(time.time()); expires = now + ttl_seconds
        participant_ids = sorted(set(participants or [])); normalized_topic = re.sub(r'\s+', ' ', topic.strip().lower())
        merged = False; conflict = ''
        async with self._lock:
            with self._conn() as conn:
                candidates = conn.execute('SELECT * FROM medium_term_memory WHERE chat_id=? AND status="active" AND lower(topic)=?', (chat_id, normalized_topic)).fetchall()
                existing = next((row for row in candidates if sorted(set(json.loads(row['participants_json'] or '[]'))) == participant_ids), None)
                if existing:
                    old_sources = json.loads(existing['source_message_ids_json'] or '[]')
                    sources = list(dict.fromkeys(old_sources + (source_message_ids or []))) [-50:]
                    old_participants = sorted(set(json.loads(existing['participants_json'] or '[]')).union(participant_ids))
                    same = _memory_key(existing['summary']) == _memory_key(summary)
                    chosen = existing['summary'] if (not same and confidence < float(existing['confidence'])) else summary[:1200]
                    conflict = 'resolved' if not same and chosen == summary[:1200] else ('retained' if not same else '')
                    conn.execute('UPDATE medium_term_memory SET summary=?,source_message_ids_json=?,participants_json=?,importance=?,confidence=?,last_referenced_at=?,expires_at=?,promotion_candidate=? WHERE event_id=?', (chosen, json.dumps(sources), json.dumps(old_participants), max(float(existing['importance']), importance), max(float(existing['confidence']), confidence), now, max(int(existing['expires_at']), expires), int(bool(existing['promotion_candidate'] or promotion_candidate)), existing['event_id']))
                    event_id = existing['event_id']; merged = True
                else:
                    conn.execute('INSERT INTO medium_term_memory(event_id,chat_id,participants_json,topic,summary,source_message_ids_json,importance,confidence,occurred_at,last_referenced_at,expires_at,promotion_candidate) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (event_id, chat_id, json.dumps(participant_ids), topic[:160], summary[:1200], json.dumps(source_message_ids or []), max(0.0,min(1.0,importance)), max(0.0,min(1.0,confidence)), now, now, expires, int(promotion_candidate)))
                conn.commit()
        event_type = ('MEMORY_MEDIUM_CONFLICT_RESOLVED' if conflict == 'resolved' else 'MEMORY_MEDIUM_CONFLICT_RETAINED' if conflict == 'retained' else 'MEMORY_MEDIUM_DEDUPED') if merged else 'MEMORY_MEDIUM_CREATED'
        await self.memory_audit_event(event_type, 'medium', chat_id, object_id=event_id, details={'topic': normalized_topic})
        logger.info('%s chat_id=%s event_id=%s topic=%s', event_type, chat_id, event_id, normalized_topic)
        await self.refresh_rag_index(chat_id)
        return event_id

    async def add_long_memory(self, chat_id: int, category: str, content: str, *, created_by: int, subject_user_id: int | None = None, source_message_ids: list[int] | None = None, confidence: float = 0.9, sensitivity_level: str = 'normal', memory_id: str | None = None) -> str:
        if sensitivity_level != 'normal' or any(x in content.lower() for x in ('token','password','api key','رمز','پسورد','توکن','شماره','آدرس')):
            raise ValueError('sensitive memory is not persistable')
        memory_id = memory_id or uuid.uuid4().hex; now = int(time.time()); expires = now + 180 * 86400
        payload = (memory_id, chat_id, subject_user_id, category[:80], content[:1600], max(0.0,min(1.0,confidence)), json.dumps(source_message_ids or []), now, now, now, expires, 'active', 1, created_by, sensitivity_level)
        event_type = 'MEMORY_LONG_UPDATED'
        async with self._lock:
            with self._conn() as conn:
                existing = conn.execute('SELECT * FROM long_term_memory WHERE chat_id=? AND category=? AND subject_user_id IS ? AND status="active" ORDER BY updated_at DESC LIMIT 1', (chat_id, category[:80], subject_user_id)).fetchone()
                if existing:
                    same = _memory_key(existing['content']) == _memory_key(content)
                    sources = list(dict.fromkeys(json.loads(existing['source_message_ids_json'] or '[]') + (source_message_ids or [])))[-50:]
                    if same:
                        conn.execute('UPDATE long_term_memory SET source_message_ids_json=?,confidence=?,last_confirmed_at=?,updated_at=? WHERE memory_id=?', (json.dumps(sources), max(float(existing['confidence']), confidence), now, now, existing['memory_id']))
                        event_type = 'MEMORY_LONG_DEDUPED'; memory_id = str(existing['memory_id'])
                    elif confidence < float(existing['confidence']):
                        event_type = 'MEMORY_LONG_CONFLICT_RETAINED'; memory_id = str(existing['memory_id'])
                    else:
                        before = dict(existing)
                        conn.execute('UPDATE long_term_memory SET content=?,confidence=?,source_message_ids_json=?,updated_at=?,last_confirmed_at=?,revision=revision+1 WHERE memory_id=?', (content[:1600], max(float(existing['confidence']), confidence), json.dumps(sources), now, now, existing['memory_id']))
                        conn.execute('INSERT INTO memory_revisions(revision_id,layer,object_id,chat_id,before_json,after_json,actor_user_id,reason,source,trace_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)', (uuid.uuid4().hex,'long',existing['memory_id'],chat_id,json.dumps(before,ensure_ascii=False),json.dumps({'category':category[:80],'content':content[:1600],'confidence':confidence},ensure_ascii=False),created_by,'contradiction_resolved','trusted_control','-',now))
                        event_type = 'MEMORY_LONG_CONFLICT_RESOLVED'; memory_id = str(existing['memory_id'])
                else:
                    conn.execute('INSERT INTO long_term_memory(memory_id,chat_id,subject_user_id,category,content,confidence,source_message_ids_json,created_at,updated_at,last_confirmed_at,expires_at,status,revision,created_by,sensitivity_level) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', payload)
                    rows = conn.execute('SELECT memory_id FROM long_term_memory WHERE chat_id=? AND status="active" ORDER BY updated_at DESC LIMIT -1 OFFSET ?', (chat_id, self.long_term_limit)).fetchall()
                    for old_row in rows:
                        before = dict(conn.execute('SELECT * FROM long_term_memory WHERE memory_id=?', (old_row['memory_id'],)).fetchone())
                        conn.execute('UPDATE long_term_memory SET status="archived",updated_at=? WHERE memory_id=?', (now, old_row['memory_id']))
                        conn.execute('INSERT INTO memory_revisions(revision_id,layer,object_id,chat_id,before_json,after_json,actor_user_id,reason,source,trace_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)', (uuid.uuid4().hex, 'long', old_row['memory_id'], chat_id, json.dumps(before, ensure_ascii=False), None, created_by, 'retention_archive', 'policy', '-', now))
                    conn.execute('INSERT INTO memory_revisions(revision_id,layer,object_id,chat_id,before_json,after_json,actor_user_id,reason,source,trace_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)', (uuid.uuid4().hex,'long',memory_id,chat_id,None,json.dumps(dict(zip(('memory_id','chat_id','category','content','confidence'), payload[:7])),ensure_ascii=False),created_by,'explicit_request','trusted_control','-',now))
                conn.commit()
        await self.memory_audit_event(event_type, 'long', chat_id, object_id=memory_id, actor_user_id=created_by, details={'category': category[:80]})
        await self.refresh_rag_index(chat_id)
        return memory_id

    async def refresh_rag_index(self, chat_id: int) -> int:
        """Refresh the chat-scoped RAG index from approved active memory only."""
        now = int(time.time()); docs = []
        async with self._lock:
            with self._conn() as conn:
                long_rows = conn.execute('SELECT memory_id,subject_user_id,category,content,confidence,source_message_ids_json,expires_at FROM long_term_memory WHERE chat_id=? AND status="active" AND (expires_at IS NULL OR expires_at>=?)', (chat_id, now)).fetchall()
                medium_rows = conn.execute('SELECT event_id,participants_json,topic,summary,confidence,source_message_ids_json,expires_at FROM medium_term_memory WHERE chat_id=? AND status="active" AND expires_at>=?', (chat_id, now)).fetchall()
                semantic_rows = conn.execute('SELECT id,sender_id,category,key,value_json,confidence,evidence_message_ids_json FROM semantic_user_memory WHERE chat_id=? AND status="active"', (chat_id,)).fetchall()
                conn.execute('DELETE FROM memory_rag_fts WHERE chat_id=?', (str(chat_id),))
                conn.execute('DELETE FROM memory_rag_documents WHERE chat_id=?', (chat_id,))
                for r in long_rows:
                    docs.append((f'long:{r["memory_id"]}', chat_id, r['subject_user_id'], 'personal' if r['subject_user_id'] else 'group', 'long', r['category'], r['content'], r['source_message_ids_json'], '[]', r['confidence'], now, now, r['expires_at']))
                for r in medium_rows:
                    participants = json.loads(r['participants_json'] or '[]')
                    docs.append((f'medium:{r["event_id"]}', chat_id, participants[0] if len(participants)==1 else None, 'participants' if participants else 'group', 'medium', r['topic'], r['summary'], r['source_message_ids_json'], '[]', r['confidence'], now, now, r['expires_at']))
                for r in semantic_rows:
                    docs.append((f'semantic:{r["id"]}', chat_id, r['sender_id'], 'personal', 'semantic', f'{r["category"]}.{r["key"]}', r['value_json'], r['evidence_message_ids_json'], '[]', r['confidence'], now, now, None))
                for d in docs:
                    conn.execute('INSERT INTO memory_rag_documents(doc_id,chat_id,subject_user_id,scope,layer,category,content,source_telegram_ids_json,source_trace_ids_json,confidence,created_at,updated_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', d)
                    conn.execute('INSERT INTO memory_rag_fts(doc_id,chat_id,category,layer,content) VALUES (?,?,?,?,?)', (d[0], str(chat_id), d[5], d[4], d[6]))
                conn.commit()
        return len(docs)

    async def retrieve_rag(self, chat_id: int, sender_id: int, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        terms = re.findall(r'[\wآ-ی‌]{3,}', (query or '').casefold())
        if not terms:
            return []
        match = ' OR '.join(re.sub(r'[^\wآ-ی‌]', '', t) for t in terms if re.sub(r'[^\wآ-ی‌]', '', t))
        if not match:
            return []
        async with self._lock:
            with self._conn() as conn:
                rows = conn.execute('''SELECT d.*, bm25(memory_rag_fts) AS rank FROM memory_rag_fts JOIN memory_rag_documents d ON d.doc_id=memory_rag_fts.doc_id WHERE memory_rag_fts MATCH ? AND d.chat_id=? AND d.status='active' AND (d.subject_user_id IS NULL OR d.subject_user_id=?) AND (d.expires_at IS NULL OR d.expires_at>=?) ORDER BY rank LIMIT ?''', (match, str(chat_id), sender_id, int(time.time()), max(1, min(limit, 20)))).fetchall()
        return [dict(r) for r in rows]

    async def retrieve_layered_memory(self, chat_id: int, query: str, *, sender_id: int | None = None, short_limit: int = 1, medium_limit: int = 4, long_limit: int = 6) -> dict[str, list[dict[str, Any]]]:
        terms = {x for x in re.findall(r'[\wآ-ی‌]{3,}', (query or '').lower())}
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                short_rows = [dict(r) for r in conn.execute('SELECT * FROM short_term_context WHERE chat_id=? AND expires_at>=?', (chat_id, now)).fetchall()]
                medium_rows = [dict(r) for r in conn.execute('SELECT * FROM medium_term_memory WHERE chat_id=? AND status="active" AND expires_at>=?', (chat_id, now)).fetchall()]
                long_rows = [dict(r) for r in conn.execute('SELECT * FROM long_term_memory WHERE chat_id=? AND status="active" AND (expires_at IS NULL OR expires_at>=?)', (chat_id, now)).fetchall()]
                if sender_id is not None:
                    medium_rows = [r for r in medium_rows if not json.loads(r.get('participants_json') or '[]') or int(sender_id) in {int(x) for x in json.loads(r.get('participants_json') or '[]')}]
                    long_rows = [r for r in long_rows if r.get('subject_user_id') is None or int(r['subject_user_id']) == int(sender_id)]
                    logger.info('MEMORY_IDENTITY_FILTER chat_id=%s sender_id=%s medium=%s long=%s', chat_id, sender_id, len(medium_rows), len(long_rows))
        def score(row: dict[str, Any], text: str, content_keys: tuple[str, ...]) -> dict[str, Any]:
            hay = ' '.join(str(row.get(k, '')) for k in content_keys).lower()
            overlap = len(terms.intersection(set(re.findall(r'[\wآ-ی‌]{3,}', hay)))) if terms else 0
            confidence = float(row.get('confidence', 1.0) or 0.0)
            importance = float(row.get('importance', 0.5) or 0.5)
            age = max(0, now - int(row.get('last_referenced_at', row.get('updated_at', now)) or now))
            recency = 1.0 / (1.0 + age / 86400.0)
            row['relevance_score'] = round(overlap / max(1, len(terms)), 4)
            row['confidence_score'] = round(confidence, 4)
            row['recency_score'] = round(recency, 4)
            row['importance_score'] = round(importance, 4)
            row['participant_match'] = 1.0 if any(str(t) in hay for t in re.findall(r'\d+', text or '')) else 0.0
            row['topic_match'] = row['relevance_score']
            row['retrieval_score'] = round(0.55 * row['relevance_score'] + 0.30 * confidence + 0.15 * recency + (0.05 * importance if 'importance' in row else 0), 4)
            return row
        short = [score(row, query, ('active_topic','mood')) for row in short_rows]
        medium_scored = [score(row, query, ('topic','summary')) for row in medium_rows]
        long_scored = [score(row, query, ('category','content')) for row in long_rows]
        if terms:
            short = [r for r in short if r['relevance_score'] > 0]
            medium_scored = [r for r in medium_scored if r['relevance_score'] > 0]
            long_scored = [r for r in long_scored if r['relevance_score'] > 0]
        medium = sorted(medium_scored, key=lambda r: r['retrieval_score'], reverse=True)[:medium_limit]
        long = sorted(long_scored, key=lambda r: r['retrieval_score'], reverse=True)[:long_limit]
        short.sort(key=lambda r: r['retrieval_score'], reverse=True)
        short = short[:short_limit]
        await self.memory_audit_event('MEMORY_RETRIEVED', 'layered', chat_id, details={'short':len(short),'medium':len(medium),'long':len(long),'terms':len(terms)})
        return {'short': short, 'medium': medium, 'long': long}

    async def rebuild_short_from_recent(self, chat_id: int, limit: int = 100) -> dict[str, Any]:
        from .memory import detect_mood, detect_topics
        async with self._lock:
            with self._conn() as conn:
                rows = [dict(r) for r in conn.execute('SELECT id,telegram_message_id,sender_id,text FROM recent_messages WHERE chat_id=? AND role="user" ORDER BY id DESC LIMIT ?', (chat_id, min(1000, max(1, limit)))).fetchall()]
        if not rows:
            await self.memory_audit_event('MEMORY_SHORT_SKIPPED', 'short', chat_id, details={'reason':'no_recent_messages'})
            return {}
        participants = sorted({int(r['sender_id']) for r in rows})
        pairs = [{'sender_id': int(r['sender_id']), 'message_id': int(r['telegram_message_id']) if r.get('telegram_message_id') is not None else f"local:{int(r['id'])}"} for r in reversed(rows[-12:])]
        topic_counts = {}
        for row in rows:
            for topic in detect_topics(row['text']): topic_counts[topic] = topic_counts.get(topic, 0) + 1
        topic = max(topic_counts, key=topic_counts.get) if topic_counts else ''
        mood = detect_mood(rows[0]['text'])
        await self.upsert_short_term_context(chat_id, active_topic=topic, active_participants_json=json.dumps(participants), conversation_pairs_json=json.dumps(pairs), mood=mood, expires_at=int(time.time()) + 6 * 3600, _audit_details={'reason':'restart_rebuild','participants_count':len(participants),'active_topic':topic})
        await self.memory_audit_event('MEMORY_SHORT_REBUILT', 'short', chat_id, details={'source_messages':len(rows),'participants_count':len(participants)})
        return await self.get_short_term_context(chat_id)

    async def backfill_memory(self, chat_id: int, count: int = 500) -> dict[str, int]:
        from .memory import extract_medium_candidate
        count = min(1000, max(1, int(count)))
        await self.memory_audit_event('MEMORY_BACKFILL_STARTED', 'medium', chat_id, details={'count':count})
        await self.rebuild_short_from_recent(chat_id, count)
        async with self._lock:
            with self._conn() as conn:
                rows = [dict(r) for r in conn.execute('SELECT id,telegram_message_id,sender_id,text FROM recent_messages WHERE chat_id=? AND role="user" ORDER BY id DESC LIMIT ?', (chat_id, count)).fetchall()]
        created = 0
        for row in reversed(rows):
            candidate = extract_medium_candidate(row['text'])
            if not candidate:
                continue
            topic, summary, ttl = candidate
            source_ids = [int(row['telegram_message_id'])] if row.get('telegram_message_id') is not None else []
            await self.add_medium_memory(chat_id, topic, summary, participants=[int(row['sender_id'])], source_message_ids=source_ids, importance=0.7, confidence=0.78, ttl_seconds=ttl)
            created += 1
        await self.memory_audit_event('MEMORY_BACKFILL_COMPLETED', 'medium', chat_id, details={'scanned':len(rows),'candidates':created})
        return {'scanned':len(rows), 'candidates':created}

    async def memory_status(self, chat_id: int) -> dict[str, int]:
        async with self._lock:
            with self._conn() as conn:
                return {layer: int(conn.execute(query, (chat_id,)).fetchone()[0]) for layer, query in {'long':'SELECT count(*) FROM long_term_memory WHERE chat_id=? AND status="active"','medium':'SELECT count(*) FROM medium_term_memory WHERE chat_id=? AND status="active" AND expires_at>=strftime("%s","now")','short':'SELECT count(*) FROM short_term_context WHERE chat_id=? AND expires_at>=strftime("%s","now")'}.items()}

    async def soft_clear_memory(self, chat_id: int, layer: str, *, actor_user_id: int, trace_id: str, reason: str) -> int:
        table = {'short':'short_term_context','medium':'medium_term_memory','long':'long_term_memory'}.get(layer)
        if not table: raise ValueError('invalid memory scope')
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                rows = conn.execute(f'SELECT * FROM {table} WHERE chat_id=?' + ('' if layer == 'short' else ' AND status="active"'), (chat_id,)).fetchall()
                for row in rows:
                    object_id = str(row['chat_id'] if layer == 'short' else row['event_id'] if layer == 'medium' else row['memory_id'])
                    conn.execute('INSERT INTO memory_revisions(revision_id,layer,object_id,chat_id,before_json,after_json,actor_user_id,reason,source,trace_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)', (uuid.uuid4().hex, layer, object_id, chat_id, json.dumps(dict(row), ensure_ascii=False), None, actor_user_id, reason, 'trusted_control', trace_id, now))
                if layer == 'short':
                    count = conn.execute('DELETE FROM short_term_context WHERE chat_id=?', (chat_id,)).rowcount
                else:
                    if layer == 'medium':
                        count = conn.execute('UPDATE medium_term_memory SET status="soft_deleted" WHERE chat_id=? AND status="active"', (chat_id,)).rowcount
                    else:
                        count = conn.execute('UPDATE long_term_memory SET status="soft_deleted",updated_at=? WHERE chat_id=? AND status="active"', (now, chat_id)).rowcount
                conn.commit()
        await self.memory_audit_event('MEMORY_SOFT_DELETED', layer, chat_id, actor_user_id=actor_user_id, trace_id=trace_id, details={'count':count,'reason':reason,'snapshot':'memory_revisions'})
        if layer in {'medium', 'long'}:
            await self.refresh_rag_index(chat_id)
        return count

    async def expire_medium_memory(self, chat_id: int | None = None) -> int:
        now = int(time.time())
        affected_chats: list[int] = []
        async with self._lock:
            with self._conn() as conn:
                if chat_id is None:
                    affected_chats = [int(r['chat_id']) for r in conn.execute('SELECT DISTINCT chat_id FROM medium_term_memory WHERE status="active" AND expires_at<?', (now,)).fetchall()]
                    count = conn.execute('UPDATE medium_term_memory SET status="archived" WHERE status="active" AND expires_at<?', (now,)).rowcount
                else:
                    count = conn.execute('UPDATE medium_term_memory SET status="archived" WHERE chat_id=? AND status="active" AND expires_at<?', (chat_id, now)).rowcount
                conn.commit()
        if chat_id is not None:
            await self.memory_audit_event('MEMORY_MEDIUM_EXPIRED', 'medium', chat_id, details={'count': count})
            if count:
                await self.refresh_rag_index(chat_id)
        else:
            for affected_chat in affected_chats:
                await self.refresh_rag_index(affected_chat)
        return count

    async def promote_medium_memory(self, event_id: str, *, actor_user_id: int, trace_id: str) -> str:
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute('SELECT * FROM medium_term_memory WHERE event_id=? AND status="active"', (event_id,)).fetchone()
        if not row or not row['promotion_candidate'] or float(row['confidence']) < 0.85:
            raise ValueError('medium memory is not an eligible deterministic promotion candidate')
        memory_id = await self.add_long_memory(int(row['chat_id']), 'promoted:' + row['topic'], row['summary'], created_by=actor_user_id, source_message_ids=json.loads(row['source_message_ids_json']), confidence=float(row['confidence']))
        async with self._lock:
            with self._conn() as conn:
                conn.execute('UPDATE medium_term_memory SET status="promoted" WHERE event_id=?', (event_id,)); conn.commit()
        await self.refresh_rag_index(int(row['chat_id']))
        await self.memory_audit_event('MEMORY_PROMOTED_TO_LONG', 'medium', int(row['chat_id']), object_id=event_id, actor_user_id=actor_user_id, trace_id=trace_id, details={'memory_id':memory_id})
        return memory_id

    async def update_recent_medium_memory(self, chat_id: int, summary: str, *, participant_id: int | None = None, source_message_id: int | None = None) -> bool:
        async with self._lock:
            with self._conn() as conn:
                rows = conn.execute('SELECT * FROM medium_term_memory WHERE chat_id=? AND status="active" ORDER BY last_referenced_at DESC LIMIT 20', (chat_id,)).fetchall()
                row = None
                for candidate in rows:
                    ids = json.loads(candidate['participants_json'] or '[]')
                    if participant_id is None or participant_id in ids:
                        row = candidate; break
                if not row:
                    return False
                sources = list(dict.fromkeys(json.loads(row['source_message_ids_json'] or '[]') + ([source_message_id] if source_message_id else [])))[-50:]
                conn.execute('UPDATE medium_term_memory SET summary=?,last_referenced_at=?,source_message_ids_json=?,revision=revision+1 WHERE event_id=?', (summary[:1200], int(time.time()), json.dumps(sources), row['event_id']))
                conn.commit()
        await self.memory_audit_event('MEMORY_MEDIUM_UPDATED', 'medium', chat_id, object_id=row['event_id'], details={'source_message_id':source_message_id})
        logger.info('MEMORY_MEDIUM_UPDATED chat_id=%s event_id=%s', chat_id, row['event_id'])
        await self.refresh_rag_index(chat_id)
        return True

    async def find_active_long_memory(self, chat_id: int, category: str, subject_user_id: int | None = None) -> dict[str, Any] | None:
        async with self._lock:
            with self._conn() as conn:
                if subject_user_id is None:
                    row = conn.execute('SELECT * FROM long_term_memory WHERE chat_id=? AND category=? AND status="active" ORDER BY updated_at DESC LIMIT 1', (chat_id, category)).fetchone()
                else:
                    row = conn.execute('SELECT * FROM long_term_memory WHERE chat_id=? AND category=? AND subject_user_id=? AND status="active" ORDER BY updated_at DESC LIMIT 1', (chat_id, category, subject_user_id)).fetchone()
                return dict(row) if row else None

    async def correct_long_memory(self, memory_id: str, content: str, *, actor_user_id: int, trace_id: str, reason: str = 'user_correction') -> bool:
        from .memory import is_sensitive_memory_text
        if is_sensitive_memory_text(content):
            raise ValueError('sensitive memory is not persistable')
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute('SELECT * FROM long_term_memory WHERE memory_id=? AND status="active"', (memory_id,)).fetchone()
                if not row:
                    return False
                conn.execute('INSERT INTO memory_revisions(revision_id,layer,object_id,chat_id,before_json,after_json,actor_user_id,reason,source,trace_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)', (uuid.uuid4().hex,'long',memory_id,row['chat_id'],json.dumps(dict(row),ensure_ascii=False),json.dumps({'content':content[:1600]},ensure_ascii=False),actor_user_id,reason,'trusted_control',trace_id,now))
                conn.execute('UPDATE long_term_memory SET content=?,updated_at=?,last_confirmed_at=?,revision=revision+1 WHERE memory_id=?', (content[:1600],now,now,memory_id))
                conn.commit()
        await self.memory_audit_event('MEMORY_CORRECTED','long',int(row['chat_id']),object_id=memory_id,actor_user_id=actor_user_id,trace_id=trace_id)
        await self.refresh_rag_index(int(row['chat_id']))
        return True

    async def restore_memory_revision(self, chat_id: int, revision_id: str, *, actor_user_id: int, trace_id: str) -> bool:
        async with self._lock:
            with self._conn() as conn:
                rev = conn.execute('SELECT * FROM memory_revisions WHERE revision_id=? AND chat_id=?', (revision_id, chat_id)).fetchone()
                if not rev or not rev['before_json']:
                    return False
                data = json.loads(rev['before_json']); layer = rev['layer']
                if layer == 'long':
                    data['status'] = 'active'; cols = [k for k in data if k != 'memory_id']; vals = [data[k] for k in cols]
                    conn.execute(f'INSERT OR REPLACE INTO long_term_memory(memory_id,{",".join(cols)}) VALUES (?,{",".join("?" for _ in cols)})', [data['memory_id'], *vals])
                elif layer == 'medium':
                    data['status'] = 'active'; cols = [k for k in data if k != 'event_id']; vals = [data[k] for k in cols]
                    conn.execute(f'INSERT OR REPLACE INTO medium_term_memory(event_id,{",".join(cols)}) VALUES (?,{",".join("?" for _ in cols)})', [data['event_id'], *vals])
                elif layer == 'short':
                    cols = [k for k in data if k != 'chat_id']; vals = [data[k] for k in cols]
                    conn.execute(f'INSERT OR REPLACE INTO short_term_context(chat_id,{",".join(cols)}) VALUES (?,{",".join("?" for _ in cols)})', [data['chat_id'], *vals])
                else:
                    return False
                conn.commit()
        await self.memory_audit_event('MEMORY_RESTORED', rev['layer'], chat_id, object_id=rev['object_id'], actor_user_id=actor_user_id, trace_id=trace_id, details={'revision_id':revision_id})
        if rev['layer'] in {'medium', 'long'}:
            await self.refresh_rag_index(chat_id)
        return True
    async def list_memory_revisions(self, chat_id: int, layer: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        async with self._lock:
            with self._conn() as conn:
                if layer: rows = conn.execute('SELECT * FROM memory_revisions WHERE chat_id=? AND layer=? ORDER BY created_at DESC LIMIT ?', (chat_id, layer, limit)).fetchall()
                else: rows = conn.execute('SELECT * FROM memory_revisions WHERE chat_id=? ORDER BY created_at DESC LIMIT ?', (chat_id, limit)).fetchall()
                return [dict(r) for r in rows]

    async def add_memory_item(self, kind: str, value: str, score: int = 0) -> None:
        async with self._lock:
            with self._conn() as conn:
                conn.execute('INSERT INTO memory_items(kind, value, score, created_at) VALUES (?, ?, ?, ?)', (kind, value, score, int(time.time())))
                conn.commit()

    async def get_memory_items(self, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        async with self._lock:
            with self._conn() as conn:
                if kind:
                    rows = conn.execute('SELECT kind, value, score, created_at FROM memory_items WHERE kind=? ORDER BY score DESC, id DESC LIMIT ?', (kind, limit)).fetchall()
                else:
                    rows = conn.execute('SELECT kind, value, score, created_at FROM memory_items ORDER BY score DESC, id DESC LIMIT ?', (limit,)).fetchall()
                return [dict(r) for r in rows]

    async def add_rate_event(self, sender_id: int, kind: str, chat_id: int | None = None) -> None:
        table = 'rate_events_scoped' if chat_id is not None else 'rate_events'
        values = (int(chat_id), sender_id, kind, int(time.time())) if chat_id is not None else (sender_id, kind, int(time.time()))
        columns = 'chat_id, sender_id, kind, created_at' if chat_id is not None else 'sender_id, kind, created_at'
        async with self._lock:
            with self._conn() as conn:
                conn.execute(f'INSERT INTO {table}({columns}) VALUES ({",".join("?" for _ in values)})', values)
                conn.commit()

    async def count_rate_events(self, sender_id: int, kind: str, since_seconds: int, chat_id: int | None = None) -> int:
        cutoff = int(time.time()) - since_seconds
        table = 'rate_events_scoped' if chat_id is not None else 'rate_events'
        where = 'chat_id=? AND sender_id=? AND kind=? AND created_at>=?' if chat_id is not None else 'sender_id=? AND kind=? AND created_at>=?'
        args = (int(chat_id), sender_id, kind, cutoff) if chat_id is not None else (sender_id, kind, cutoff)
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute(f'SELECT COUNT(*) AS c FROM {table} WHERE {where}', args).fetchone()
                return int(row['c'])

    async def try_reserve_rate_event(self, sender_id: int, kind: str, since_seconds: int, limit: int, *, chat_id: int | None = None, vision: bool = False) -> tuple[bool, int]:
        cutoff = int(time.time()) - int(since_seconds)
        async with self._lock:
            with self._conn() as conn:
                if vision:
                    table, where, args = 'vision_rate_events', 'sender_id=? AND kind=? AND created_at>=?', (sender_id, kind, cutoff)
                    columns, values = 'sender_id,kind,created_at', (sender_id, kind, int(time.time()))
                elif chat_id is not None:
                    table, where, args = 'rate_events_scoped', 'chat_id=? AND sender_id=? AND kind=? AND created_at>=?', (int(chat_id), sender_id, kind, cutoff)
                    columns, values = 'chat_id,sender_id,kind,created_at', (int(chat_id), sender_id, kind, int(time.time()))
                else:
                    table, where, args = 'rate_events', 'sender_id=? AND kind=? AND created_at>=?', (sender_id, kind, cutoff)
                    columns, values = 'sender_id,kind,created_at', (sender_id, kind, int(time.time()))
                used = int(conn.execute(f'SELECT COUNT(*) FROM {table} WHERE {where}', args).fetchone()[0])
                if used >= int(limit):
                    return False, used
                conn.execute(f'INSERT INTO {table}({columns}) VALUES ({",".join("?" for _ in values)})', values)
                conn.commit()
                return True, used + 1

    async def add_vision_rate_event(self, sender_id: int, kind: str) -> None:
        """Record a vision rate event (image/gif) persistently."""
        async with self._lock:
            with self._conn() as conn:
                conn.execute('INSERT INTO vision_rate_events(sender_id, kind, created_at) VALUES (?, ?, ?)', (sender_id, kind, int(time.time())))
                conn.commit()

    async def count_vision_rate_events(self, sender_id: int, kind: str, since_seconds: int) -> int:
        """Count vision rate events for a user within a time window."""
        cutoff = int(time.time()) - since_seconds
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute('SELECT COUNT(*) AS c FROM vision_rate_events WHERE sender_id=? AND kind=? AND created_at>=?', (sender_id, kind, cutoff)).fetchone()
                return int(row['c'])

    async def incr_daily_stats(self, day: str, **deltas: int | float) -> None:
        async with self._lock:
            with self._conn() as conn:
                conn.execute('INSERT OR IGNORE INTO stats(day) VALUES (?)', (day,))
                for key, value in deltas.items():
                    conn.execute(f'UPDATE stats SET {key} = COALESCE({key}, 0) + ? WHERE day=?', (value, day))
                conn.commit()

    async def get_today_stats(self, day: str) -> dict[str, Any]:
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute('SELECT * FROM stats WHERE day=?', (day,)).fetchone()
                return dict(row) if row else {}

    # ============ SOCIAL AWARENESS ============

    async def get_social_group_state(self, chat_id: int) -> dict[str, Any]:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute('INSERT OR IGNORE INTO social_group_state(chat_id, updated_at) VALUES (?, ?)', (chat_id, now))
                row = conn.execute('SELECT * FROM social_group_state WHERE chat_id=?', (chat_id,)).fetchone()
                conn.commit()
                return dict(row)

    async def adjust_social_group_state(self, chat_id: int, *, reputation_delta: int = 0, positive_delta: int = 0,
                                        negative_delta: int = 0, accepted_delta: int = 0, ignored_delta: int = 0) -> dict[str, Any]:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute('INSERT OR IGNORE INTO social_group_state(chat_id, updated_at) VALUES (?, ?)', (chat_id, now))
                conn.execute(
                    '''UPDATE social_group_state SET social_reputation=social_reputation + ?,
                       positive_feedback_count=positive_feedback_count + ?, negative_feedback_count=negative_feedback_count + ?,
                       reply_acceptance_count=reply_acceptance_count + ?, ignored_reply_count=ignored_reply_count + ?, updated_at=?
                       WHERE chat_id=?''',
                    (reputation_delta, positive_delta, negative_delta, accepted_delta, ignored_delta, now, chat_id),
                )
                row = conn.execute('SELECT * FROM social_group_state WHERE chat_id=?', (chat_id,)).fetchone()
                reputation = int(row['social_reputation'])
                confidence = max(0.45, min(1.0, 1.0 + reputation / 20.0))
                conn.execute('UPDATE social_group_state SET social_confidence=? WHERE chat_id=?', (confidence, chat_id))
                row = conn.execute('SELECT * FROM social_group_state WHERE chat_id=?', (chat_id,)).fetchone()
                conn.commit()
                return dict(row)

    async def add_social_feedback_event(self, chat_id: int, sender_id: int, kind: str, *, now: int | None = None) -> None:
        now = int(now or time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute('INSERT INTO social_feedback_events(chat_id, sender_id, kind, created_at) VALUES (?, ?, ?, ?)', (chat_id, sender_id, kind, now))
                conn.commit()

    async def count_social_feedback_users(self, chat_id: int, kind: str, since_seconds: int) -> int:
        cutoff = int(time.time()) - since_seconds
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute('SELECT COUNT(DISTINCT sender_id) AS c FROM social_feedback_events WHERE chat_id=? AND kind=? AND created_at>=?', (chat_id, kind, cutoff)).fetchone()
                return int(row['c'])

    async def record_social_action_message(self, chat_id: int, message_id: int, action: str) -> None:
        async with self._lock:
            with self._conn() as conn:
                conn.execute('INSERT OR REPLACE INTO social_action_messages(chat_id, message_id, action, created_at) VALUES (?, ?, ?, ?)', (chat_id, message_id, action, int(time.time())))
                conn.commit()

    async def social_action_for_message(self, chat_id: int, message_id: int) -> str | None:
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute('SELECT action FROM social_action_messages WHERE chat_id=? AND message_id=?', (chat_id, message_id)).fetchone()
                return str(row['action']) if row else None

    async def observe_social_plus(self, chat_id: int, user_id: int, text: str, *, label: str = '', media_type: str = '', topics: list[str] | None = None, emojis: list[str] | None = None, now: int | None = None) -> dict[str, Any]:
        now = int(now or time.time()); day = datetime.fromtimestamp(now).strftime('%Y-%m-%d')
        topics = list(dict.fromkeys((topics or [])[:8])); emojis = list(dict.fromkeys((emojis or [])[:8]))
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute('SELECT * FROM social_stats WHERE chat_id=? AND day=?', (chat_id, day)).fetchone()
                stats = dict(row) if row else {'message_count': 0, 'sticker_count': 0, 'gif_count': 0, 'image_count': 0, 'user_counts_json': '{}', 'topic_counts_json': '{}', 'emoji_counts_json': '{}'}
                users, topic_counts, emoji_counts = (json.loads(stats[k] or '{}') for k in ('user_counts_json', 'topic_counts_json', 'emoji_counts_json'))
                users[str(user_id)] = int(users.get(str(user_id), 0)) + 1
                for topic in topics: topic_counts[topic] = int(topic_counts.get(topic, 0)) + 1
                for emoji in emojis: emoji_counts[emoji] = int(emoji_counts.get(emoji, 0)) + 1
                media_key = {'sticker': 'sticker_count', 'gif': 'gif_count', 'image': 'image_count'}.get(media_type, '')
                conn.execute('INSERT INTO social_stats(chat_id,day,message_count,sticker_count,gif_count,image_count,user_counts_json,topic_counts_json,emoji_counts_json) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(chat_id,day) DO UPDATE SET message_count=social_stats.message_count+1, sticker_count=social_stats.sticker_count+?, gif_count=social_stats.gif_count+?, image_count=social_stats.image_count+?, user_counts_json=excluded.user_counts_json, topic_counts_json=excluded.topic_counts_json, emoji_counts_json=excluded.emoji_counts_json', (chat_id, day, 1, int(media_key == 'sticker'), int(media_key == 'gif'), int(media_key == 'image'), json.dumps(users), json.dumps(topic_counts, ensure_ascii=False), json.dumps(emoji_counts, ensure_ascii=False), int(media_key == 'sticker'), int(media_key == 'gif'), int(media_key == 'image')))
                profile = conn.execute('SELECT * FROM social_profiles WHERE chat_id=? AND user_id=?', (chat_id, user_id)).fetchone()
                p = dict(profile) if profile else {'favorite_topics_json': '[]', 'activity_hours_json': '{}', 'humor_score': 0, 'sticker_usage': 0, 'reaction_usage': 0, 'confidence': 0.1}
                favorite = list(dict.fromkeys(json.loads(p['favorite_topics_json'] or '[]') + topics))[-8:]
                hours = json.loads(p['activity_hours_json'] or '{}'); hour = str(datetime.fromtimestamp(now).hour); hours[hour] = int(hours.get(hour, 0)) + 1
                humor = min(1.0, float(p['humor_score']) * 0.95 + (0.1 if any(x in (text or '') for x in ('😂','🤣','خخ','lol')) else 0))
                language = 'fa' if re.search(r'[آ-ی]', text or '') else 'en' if re.search(r'[A-Za-z]', text or '') else 'unknown'
                style = 'question' if '?' in (text or '') or '؟' in (text or '') else 'technical' if any(x in (text or '').lower() for x in ('python','api','کد','ارور')) else 'casual'
                conn.execute('INSERT INTO social_profiles(chat_id,user_id,nickname,favorite_topics_json,activity_hours_json,humor_score,sticker_usage,reaction_usage,preferred_language,interaction_style,confidence,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET nickname=CASE WHEN excluded.nickname != \'\' THEN excluded.nickname ELSE social_profiles.nickname END, favorite_topics_json=excluded.favorite_topics_json, activity_hours_json=excluded.activity_hours_json, humor_score=excluded.humor_score, sticker_usage=social_profiles.sticker_usage+excluded.sticker_usage, confidence=MIN(1.0,social_profiles.confidence+0.03), preferred_language=excluded.preferred_language, interaction_style=excluded.interaction_style, updated_at=excluded.updated_at', (chat_id, user_id, label[:80], json.dumps(favorite, ensure_ascii=False), json.dumps(hours), humor, int(media_type == 'sticker'), 0, language, style, min(1.0, float(p['confidence']) + 0.03), now))
                conn.commit()
        return {'day': day, 'topics': topics, 'media_type': media_type}

    async def get_social_plus_stats(self, chat_id: int, *, days: int = 1) -> dict[str, Any]:
        cutoff = (datetime.now() - __import__('datetime').timedelta(days=max(1, days) - 1)).strftime('%Y-%m-%d')
        async with self._lock:
            with self._conn() as conn:
                rows = [dict(r) for r in conn.execute('SELECT * FROM social_stats WHERE chat_id=? AND day>=? ORDER BY day DESC', (chat_id, cutoff)).fetchall()]
        totals = {key: sum(int(r.get(key, 0)) for r in rows) for key in ('message_count','sticker_count','gif_count','image_count')}
        users: dict[str, int] = {}; topics: dict[str, int] = {}
        for row in rows:
            for key, target in (('user_counts_json', users), ('topic_counts_json', topics)):
                for item, count in json.loads(row.get(key) or '{}').items(): target[item] = target.get(item, 0) + int(count)
        totals.update({'days': len(rows), 'active_users': sorted(users.items(), key=lambda x: x[1], reverse=True)[:10], 'topics': sorted(topics.items(), key=lambda x: x[1], reverse=True)[:10]})
        logger.info('GROUP_STATS_UPDATED chat_id=%s days=%s message_count=%s', chat_id, days, totals['message_count'])
        return totals

    async def get_social_plus_context(self, chat_id: int, query: str = '') -> list[dict[str, Any]]:
        async with self._lock:
            with self._conn() as conn:
                rows = conn.execute('SELECT topic,summary,participants_json,confidence FROM social_threads WHERE chat_id=? ORDER BY last_activity DESC LIMIT 3', (chat_id,)).fetchall()
                jokes = conn.execute('SELECT phrase,confidence,users_json,days_json FROM inside_jokes WHERE chat_id=? ORDER BY confidence DESC,last_seen DESC LIMIT 20', (chat_id,)).fetchall()
        qualified = [dict(r) for r in jokes if len(json.loads(r['users_json'] or '[]')) >= 3 and len(json.loads(r['days_json'] or '[]')) >= 2 and float(r['confidence']) >= 0.65]
        return [{'kind': 'thread', **dict(r)} for r in rows] + [{'kind': 'inside_joke', 'phrase': r['phrase'], 'confidence': r['confidence']} for r in qualified[:3]]

    async def get_social_quotes(self, chat_id: int, *, today: bool = False, limit: int = 5) -> list[dict[str, Any]]:
        where = ' AND last_seen>=?' if today else ''; args: list[Any] = [chat_id]
        if today: args.append(int(datetime.combine(datetime.now().date(), datetime.min.time()).timestamp()))
        args.append(limit)
        async with self._lock:
            with self._conn() as conn:
                return [dict(r) for r in conn.execute(f'SELECT quote,occurrences FROM social_quotes WHERE chat_id=?{where} ORDER BY last_seen DESC LIMIT ?', args).fetchall()]


    async def get_limit_challenge_progress(self, user_id: int, chat_id: int) -> dict[str, Any] | None:
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    'SELECT * FROM limit_challenge_progress WHERE user_id=? AND chat_id=?', (user_id, chat_id),
                ).fetchone()
                return dict(row) if row else None

    async def upsert_limit_challenge_progress(self, user_id: int, chat_id: int, *, current_stage: int | None = None,
                                              completed_stages: list[int] | None = None, reward_step: int | None = None,
                                              bonus_quota: int | None = None, last_challenge_at: int | None = None,
                                              daily_completed_count: int | None = None, day_key: str | None = None) -> dict[str, Any]:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute('SELECT * FROM limit_challenge_progress WHERE user_id=? AND chat_id=?', (user_id, chat_id)).fetchone()
                current = dict(row) if row else {}
                values = {
                    'current_stage': int(current_stage if current_stage is not None else current.get('current_stage', 1)),
                    'completed_stages_json': json.dumps(completed_stages if completed_stages is not None else json.loads(current.get('completed_stages_json', '[]')), ensure_ascii=False),
                    'reward_step': int(reward_step if reward_step is not None else current.get('reward_step', 0)),
                    'bonus_quota': max(0, int(bonus_quota if bonus_quota is not None else current.get('bonus_quota', 0))),
                    'last_challenge_at': last_challenge_at if last_challenge_at is not None else current.get('last_challenge_at'),
                    'daily_completed_count': max(0, int(daily_completed_count if daily_completed_count is not None else current.get('daily_completed_count', 0))),
                    'day_key': str(day_key if day_key is not None else current.get('day_key', '')),
                }
                conn.execute(
                    '''INSERT INTO limit_challenge_progress(user_id, chat_id, current_stage, completed_stages_json, reward_step, bonus_quota, last_challenge_at, daily_completed_count, day_key, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(user_id, chat_id) DO UPDATE SET current_stage=excluded.current_stage, completed_stages_json=excluded.completed_stages_json,
                         reward_step=excluded.reward_step, bonus_quota=excluded.bonus_quota, last_challenge_at=excluded.last_challenge_at,
                         daily_completed_count=excluded.daily_completed_count, day_key=excluded.day_key, updated_at=excluded.updated_at''',
                    (user_id, chat_id, values['current_stage'], values['completed_stages_json'], values['reward_step'], values['bonus_quota'],
                     values['last_challenge_at'], values['daily_completed_count'], values['day_key'], now),
                )
                conn.commit()
                values.update({'user_id': user_id, 'chat_id': chat_id, 'updated_at': now})
                return values

    async def get_limit_challenge_active(self, user_id: int, chat_id: int) -> dict[str, Any] | None:
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT * FROM limit_challenge_active WHERE user_id=? AND chat_id=? AND status='active'", (user_id, chat_id),
                ).fetchone()
                return dict(row) if row else None

    async def create_limit_challenge_active(self, user_id: int, chat_id: int, *, stage: int, challenge_id: str,
                                            question: str, answer: str, answer_hash: str, expires_at: int) -> None:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    '''INSERT INTO limit_challenge_active(user_id, chat_id, stage, challenge_id, question, answer, answer_hash, attempts, created_at, expires_at, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 'active')
                       ON CONFLICT(user_id, chat_id) DO UPDATE SET stage=excluded.stage, challenge_id=excluded.challenge_id, question=excluded.question,
                         answer=excluded.answer, answer_hash=excluded.answer_hash, attempts=0, created_at=excluded.created_at, expires_at=excluded.expires_at, status='active' ''',
                    (user_id, chat_id, stage, challenge_id, question, answer, answer_hash, now, expires_at),
                )
                conn.commit()

    async def record_limit_challenge_wrong_answer(self, user_id: int, chat_id: int, *, max_attempts: int = 2) -> tuple[int, bool]:
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute("SELECT attempts FROM limit_challenge_active WHERE user_id=? AND chat_id=? AND status='active'", (user_id, chat_id)).fetchone()
                if not row:
                    return 0, True
                attempts = int(row['attempts']) + 1
                failed = attempts >= max_attempts
                conn.execute('UPDATE limit_challenge_active SET attempts=?, status=? WHERE user_id=? AND chat_id=?',
                             (attempts, 'failed' if failed else 'active', user_id, chat_id))
                conn.commit()
                return attempts, failed

    async def close_limit_challenge_active(self, user_id: int, chat_id: int, status: str = 'completed') -> None:
        async with self._lock:
            with self._conn() as conn:
                conn.execute('UPDATE limit_challenge_active SET status=? WHERE user_id=? AND chat_id=?', (status, user_id, chat_id))
                conn.commit()

    async def expire_limit_challenge_active(self, user_id: int, chat_id: int, *, now: int | None = None) -> bool:
        now = int(now or time.time())
        async with self._lock:
            with self._conn() as conn:
                changed = conn.execute("UPDATE limit_challenge_active SET status='expired' WHERE user_id=? AND chat_id=? AND status='active' AND expires_at<=?", (user_id, chat_id, now)).rowcount
                conn.commit()
                return bool(changed)

    async def count_limit_challenge_active(self, user_id: int, chat_id: int) -> int:
        async with self._lock:
            with self._conn() as conn:
                return int(conn.execute("SELECT COUNT(*) AS c FROM limit_challenge_active WHERE user_id=? AND chat_id=? AND status='active'", (user_id, chat_id)).fetchone()['c'])

    async def ensure_limit_challenge_template(self, stage: int, template_id: str, template_type: str, template: Any) -> bool:
        async with self._lock:
            with self._conn() as conn:
                created = conn.execute(
                    'INSERT OR IGNORE INTO limit_challenge_templates(stage, template_id, template_type, template_json, created_at) VALUES (?, ?, ?, ?, ?)',
                    (stage, template_id, template_type, json.dumps(template, ensure_ascii=False), int(time.time())),
                ).rowcount
                conn.commit()
                return bool(created)

    async def list_limit_challenge_templates(self, stage: int | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            with self._conn() as conn:
                if stage is None:
                    rows = conn.execute('SELECT * FROM limit_challenge_templates ORDER BY stage, template_id').fetchall()
                else:
                    rows = conn.execute('SELECT * FROM limit_challenge_templates WHERE stage=? ORDER BY template_id', (stage,)).fetchall()
                return [dict(row) for row in rows]

    async def mark_limit_challenge_template_used(self, stage: int, template_id: str) -> None:
        async with self._lock:
            with self._conn() as conn:
                conn.execute('UPDATE limit_challenge_templates SET usage_count=usage_count+1, last_used_at=? WHERE stage=? AND template_id=?', (int(time.time()), stage, template_id))
                conn.commit()

    async def list_limit_challenge_progress(self, user_id: int) -> list[dict[str, Any]]:
        async with self._lock:
            with self._conn() as conn:
                rows = conn.execute('SELECT * FROM limit_challenge_progress WHERE user_id=? ORDER BY chat_id', (user_id,)).fetchall()
                return [dict(row) for row in rows]

    async def clear_limit_challenge_active(self, user_id: int, chat_id: int | None = None) -> int:
        async with self._lock:
            with self._conn() as conn:
                if chat_id is None:
                    changed = conn.execute("UPDATE limit_challenge_active SET status='cleared' WHERE user_id=? AND status='active'", (user_id,)).rowcount
                else:
                    changed = conn.execute("UPDATE limit_challenge_active SET status='cleared' WHERE user_id=? AND chat_id=? AND status='active'", (user_id, chat_id)).rowcount
                conn.commit()
                return int(changed)

    async def reset_limit_challenge(self, user_id: int, chat_id: int | None = None) -> None:
        async with self._lock:
            with self._conn() as conn:
                if chat_id is None:
                    conn.execute('DELETE FROM limit_challenge_progress WHERE user_id=?', (user_id,))
                    conn.execute('DELETE FROM limit_challenge_active WHERE user_id=?', (user_id,))
                else:
                    conn.execute('DELETE FROM limit_challenge_progress WHERE user_id=? AND chat_id=?', (user_id, chat_id))
                    conn.execute('DELETE FROM limit_challenge_active WHERE user_id=? AND chat_id=?', (user_id, chat_id))
                conn.commit()

    # ============ SOCIAL GROUP STATE ============

    async def touch_group_user(self, user_id: int, chat_id: int, label: str, *, now: int | None = None, joined: bool = False) -> None:
        now = int(now or time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    '''INSERT INTO group_user_state(user_id, chat_id, label, first_seen, last_seen, joined_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(user_id, chat_id) DO UPDATE SET
                         label=CASE WHEN excluded.label != '' THEN excluded.label ELSE group_user_state.label END,
                         last_seen=MAX(group_user_state.last_seen, excluded.last_seen),
                         joined_at=COALESCE(group_user_state.joined_at, excluded.joined_at),
                         left_at=CASE WHEN excluded.joined_at IS NOT NULL THEN NULL ELSE group_user_state.left_at END''',
                    (user_id, chat_id, label or '', now, now, now if joined else None),
                )
                conn.commit()

    async def record_group_user_message(self, user_id: int, chat_id: int, label: str, *, now: int | None = None) -> None:
        now = int(now or time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    '''INSERT INTO group_user_state(user_id, chat_id, label, first_seen, last_seen, message_count)
                       VALUES (?, ?, ?, ?, ?, 1)
                       ON CONFLICT(user_id, chat_id) DO UPDATE SET
                         label=CASE WHEN excluded.label != '' THEN excluded.label ELSE group_user_state.label END,
                         last_seen=excluded.last_seen, message_count=group_user_state.message_count + 1,
                         left_at=NULL''',
                    (user_id, chat_id, label or '', now, now),
                )
                conn.commit()

    async def claim_group_welcome(self, user_id: int, chat_id: int, *, now: int | None = None) -> bool:
        now = int(now or time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    '''INSERT INTO group_user_state(user_id, chat_id, label, first_seen, last_seen, joined_at)
                       VALUES (?, ?, '', ?, ?, ?)
                       ON CONFLICT(user_id, chat_id) DO UPDATE SET
                         joined_at=COALESCE(group_user_state.joined_at, excluded.joined_at),
                         left_at=NULL''',
                    (user_id, chat_id, now, now, now),
                )
                changed = conn.execute(
                    'UPDATE group_user_state SET welcome_sent=1 WHERE user_id=? AND chat_id=? AND welcome_sent=0',
                    (user_id, chat_id),
                ).rowcount
                conn.commit()
                return bool(changed)

    async def set_group_social_opt_out(self, user_id: int, chat_id: int, enabled: bool = True) -> None:
        await self.touch_group_user(user_id, chat_id, '')
        async with self._lock:
            with self._conn() as conn:
                conn.execute('UPDATE group_user_state SET social_opt_out=? WHERE user_id=? AND chat_id=?', (int(enabled), user_id, chat_id))
                conn.commit()

    async def group_user_social_opt_out(self, user_id: int, chat_id: int) -> bool:
        state = await self.get_group_user_state(user_id, chat_id)
        return bool(state and state['social_opt_out'])

    async def mark_inactive_ping(self, user_id: int, chat_id: int, *, now: int | None = None) -> None:
        now = int(now or time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute('UPDATE group_user_state SET last_inactive_ping=? WHERE user_id=? AND chat_id=?', (now, user_id, chat_id))
                conn.commit()

    async def list_inactive_group_users(self, chat_id: int, *, inactive_before: int, min_messages: int, last_ping_before: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
        query = '''SELECT user_id, label, first_seen, last_seen, message_count, last_inactive_ping
                   FROM group_user_state
                   WHERE chat_id=? AND left_at IS NULL AND social_opt_out=0
                     AND message_count>=? AND first_seen<=? AND last_seen<?'''
        params: list[Any] = [chat_id, min_messages, inactive_before, inactive_before]
        if last_ping_before is not None:
            query += ' AND (last_inactive_ping IS NULL OR last_inactive_ping<?)'
            params.append(last_ping_before)
        query += ' ORDER BY last_seen ASC LIMIT ?'
        params.append(limit)
        async with self._lock:
            with self._conn() as conn:
                return [dict(row) for row in conn.execute(query, params).fetchall()]

    async def mark_group_user_left(self, user_id: int, chat_id: int, *, now: int | None = None) -> None:
        now = int(now or time.time())
        await self.touch_group_user(user_id, chat_id, '', now=now)
        async with self._lock:
            with self._conn() as conn:
                conn.execute('UPDATE group_user_state SET left_at=? WHERE user_id=? AND chat_id=?', (now, user_id, chat_id))
                conn.commit()

    async def mark_user_dm_allowed(self, user_id: int, *, now: int | None = None) -> None:
        now = int(now or time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    '''INSERT INTO group_user_state(user_id, chat_id, label, first_seen, last_seen, dm_allowed)
                       VALUES (?, 0, '', ?, ?, 1)
                       ON CONFLICT(user_id, chat_id) DO UPDATE SET dm_allowed=1, last_seen=excluded.last_seen''',
                    (user_id, now, now),
                )
                conn.commit()

    async def user_dm_allowed_for_group(self, user_id: int, chat_id: int) -> bool:
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    'SELECT 1 FROM group_user_state WHERE user_id=? AND chat_id IN (?, 0) AND dm_allowed=1 LIMIT 1',
                    (user_id, chat_id),
                ).fetchone()
                return row is not None

    async def get_group_user_state(self, user_id: int, chat_id: int) -> dict[str, Any] | None:
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute('SELECT * FROM group_user_state WHERE user_id=? AND chat_id=?', (user_id, chat_id)).fetchone()
                return dict(row) if row else None

    # ============ STICKER METHODS ============

    async def add_sticker(self, sticker: Sticker) -> None:
        """Insert or update a sticker in the database."""
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    '''INSERT INTO stickers (
                        doc_id, access_hash, file_reference, mime_type, emoji,
                        stickerset_id, stickerset_access_hash, stickerset_short_name,
                        is_animated, is_video, vision_summary, vision_tags,
                        nsfw_score, mood_tags, quality_score, spam_score,
                        usage_count, first_seen, last_seen, first_sender_id,
                        saved_to_account, saved_at, recent_saved, last_message_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(doc_id) DO UPDATE SET
                        access_hash=excluded.access_hash,
                        file_reference=excluded.file_reference,
                        usage_count=excluded.usage_count,
                        last_seen=excluded.last_seen,
                        vision_summary=excluded.vision_summary,
                        vision_tags=excluded.vision_tags,
                        nsfw_score=excluded.nsfw_score,
                        mood_tags=excluded.mood_tags,
                        quality_score=excluded.quality_score,
                        spam_score=excluded.spam_score,
                        saved_to_account=excluded.saved_to_account,
                        saved_at=excluded.saved_at,
                        recent_saved=excluded.recent_saved,
                        last_message_id=excluded.last_message_id''',
                    sticker.to_row()
                )
                conn.commit()

    async def get_sticker(self, doc_id: int) -> Sticker | None:
        """Get a sticker by doc_id."""
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute('SELECT * FROM stickers WHERE doc_id=?', (doc_id,)).fetchone()
                if not row:
                    return None
                return self._row_to_sticker(row)

    async def get_stickers(
        self,
        *,
        min_quality: float = 0.0,
        mood_filter: str | None = None,
        saved_only: bool = False,
        limit: int = 50,
        min_usage: int = 0
    ) -> list[Sticker]:
        """Search stickers with filters."""
        query = 'SELECT * FROM stickers WHERE quality_score >= ? AND usage_count >= ?'
        params: list = [min_quality, min_usage]
        
        if mood_filter:
            query += ' AND mood_tags LIKE ?'
            params.append(f'%{mood_filter}%')
        if saved_only:
            query += ' AND saved_to_account = 1'
        
        query += ' ORDER BY quality_score DESC, usage_count DESC LIMIT ?'
        params.append(limit)
        
        async with self._lock:
            with self._conn() as conn:
                rows = conn.execute(query, params).fetchall()
                return [self._row_to_sticker(row) for row in rows]

    async def get_sticker_by_mood(self, mood: str, limit: int = 10, min_quality: float = 0.6) -> list[Sticker]:
        """Get stickers matching a mood tag."""
        return await self.get_stickers(
            min_quality=min_quality,
            mood_filter=mood,
            limit=limit
        )

    async def increment_sticker_usage(self, doc_id: int, sender_id: int) -> None:
        """Increment usage count and update last_seen."""
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    '''UPDATE stickers SET 
                        usage_count = usage_count + 1,
                        last_seen = ?,
                        first_sender_id = COALESCE(first_sender_id, ?)
                    WHERE doc_id = ?''',
                    (now, sender_id, doc_id)
                )
                conn.commit()

    async def record_sticker_observation(self, doc_id: int, chat_id: int, sender_id: int, message_id: int) -> None:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    'UPDATE stickers SET chat_id=?, sender_id=?, message_id=?, last_seen=?, is_available=1, sticker_type=CASE WHEN is_video=1 THEN \'video\' WHEN is_animated=1 THEN \'animated\' ELSE \'static\' END WHERE doc_id=?',
                    (chat_id, sender_id, message_id, now, doc_id),
                )
                conn.commit()

    async def record_sticker_send(self, doc_id: int, chat_id: int, message_id: int | None = None) -> None:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    'UPDATE stickers SET send_count=send_count+1, last_sent_at=?, message_id=COALESCE(?, message_id), is_available=1 WHERE doc_id=?',
                    (now, message_id, doc_id),
                )
                conn.execute('INSERT INTO sticker_send_history(chat_id, doc_id, sent_at) VALUES (?, ?, ?)', (chat_id, doc_id, now))
                conn.commit()

    async def record_sticker_send_failure(self, doc_id: int) -> None:
        async with self._lock:
            with self._conn() as conn:
                conn.execute('UPDATE stickers SET failure_count=failure_count+1 WHERE doc_id=?', (doc_id,))
                conn.commit()

    async def get_recent_sticker_doc_ids(self, chat_id: int, limit: int = 10) -> list[int]:
        async with self._lock:
            with self._conn() as conn:
                rows = conn.execute('SELECT doc_id FROM sticker_send_history WHERE chat_id=? ORDER BY sent_at DESC LIMIT ?', (chat_id, limit)).fetchall()
                return [int(row['doc_id']) for row in rows]

    async def get_sticker_send_policy(self, chat_id: int) -> dict[str, int]:
        """Return chat-scoped sticker send counters without using labels."""
        now = int(time.time()); window_cutoff = now - 120
        async with self._lock:
            with self._conn() as conn:
                count = int(conn.execute('SELECT COUNT(*) AS c FROM sticker_send_history WHERE chat_id=? AND sent_at>=?', (chat_id, window_cutoff)).fetchone()['c'])
                last_row = conn.execute('SELECT sent_at FROM sticker_send_history WHERE chat_id=? ORDER BY sent_at DESC LIMIT 1', (chat_id,)).fetchone()
                last = int(last_row['sent_at']) if last_row else 0
                messages = int(conn.execute("SELECT COUNT(*) AS c FROM recent_messages WHERE chat_id=? AND role='user' AND created_at>=?", (chat_id, last)).fetchone()['c']) if last else 999999
                return {'sent_last_hour': count, 'last_sent_at': last, 'messages_since_last': messages}

    async def mark_sticker_saved(self, doc_id: int) -> None:
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    'UPDATE stickers SET saved_to_account = 1, saved_at = ? WHERE doc_id = ?',
                    (now, doc_id)
                )
                conn.commit()

    async def mark_sticker_recent_saved(self, doc_id: int) -> None:
        """Mark sticker as recently saved."""
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    'UPDATE stickers SET recent_saved = 1 WHERE doc_id = ?',
                    (doc_id,)
                )
                conn.commit()

    async def update_sticker_vision(self, doc_id: int, summary: str, tags: str, nsfw_score: float) -> None:
        """Update vision analysis results."""
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    'UPDATE stickers SET vision_summary = ?, vision_tags = ?, nsfw_score = ? WHERE doc_id = ?',
                    (summary, tags, nsfw_score, doc_id)
                )
                conn.commit()

    async def update_sticker_classification(self, doc_id: int, mood_tags: str, quality_score: float, spam_score: float) -> None:
        """Update mood and quality classification."""
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    'UPDATE stickers SET mood_tags = ?, quality_score = ?, spam_score = ? WHERE doc_id = ?',
                    (mood_tags, quality_score, spam_score, doc_id)
                )
                conn.commit()

    async def update_sticker_last_message(self, doc_id: int, message_id: int) -> None:
        """Update the last message ID for a sticker."""
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    'UPDATE stickers SET last_message_id = ? WHERE doc_id = ?',
                    (message_id, doc_id)
                )
                conn.commit()

    async def adjust_sticker_reaction_score_by_message(self, message_id: int, delta: int) -> bool:
        """Apply aggregate feedback only when exactly one sticker matches a message.

        No reactor identities or individual reaction records are stored.  The
        uniqueness check prevents legacy cross-chat message-ID collisions from
        affecting the wrong sticker.
        """
        async with self._lock:
            with self._conn() as conn:
                rows = conn.execute(
                    'SELECT doc_id FROM stickers WHERE last_message_id = ?', (message_id,)
                ).fetchall()
                if len(rows) != 1:
                    return False
                conn.execute(
                    'UPDATE stickers SET reaction_score = reaction_score + ? WHERE doc_id = ?',
                    (int(delta), rows[0]['doc_id']),
                )
                conn.commit()
                return True

    async def update_sticker_file_reference(self, doc_id: int, file_reference: bytes, access_hash: int | None = None) -> None:
        """Update expiring file reference and, when supplied, access hash."""
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    'UPDATE stickers SET file_reference = ?, access_hash = COALESCE(?, access_hash) WHERE doc_id = ?',
                    (file_reference, access_hash, doc_id)
                )
                conn.commit()

    async def update_sticker_saved(self, doc_id: int, saved: bool) -> None:
        """Update saved_to_account flag for a sticker."""
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    'UPDATE stickers SET saved_to_account = ?, saved_at = ? WHERE doc_id = ?',
                    (saved, now, doc_id)
                )
                conn.commit()

    async def get_sticker_stats(self) -> dict:
        """Get sticker library statistics."""
        async with self._lock:
            with self._conn() as conn:
                total = conn.execute('SELECT COUNT(*) AS c FROM stickers').fetchone()['c']
                saved = conn.execute('SELECT COUNT(*) AS c FROM stickers WHERE saved_to_account = 1').fetchone()['c']
                recent = conn.execute('SELECT COUNT(*) AS c FROM stickers WHERE recent_saved = 1').fetchone()['c']
                animated = conn.execute('SELECT COUNT(*) AS c FROM stickers WHERE is_animated = 1').fetchone()['c']
                video = conn.execute('SELECT COUNT(*) AS c FROM stickers WHERE is_video = 1').fetchone()['c']
                avg_quality = conn.execute('SELECT AVG(quality_score) AS q FROM stickers').fetchone()['q'] or 0.0
                total_usage = conn.execute('SELECT SUM(usage_count) AS s FROM stickers').fetchone()['s'] or 0
                return {
                    'total': total,
                    'saved_to_account': saved,
                    'recent_saved': recent,
                    'animated': animated,
                    'video': video,
                    'avg_quality': round(avg_quality, 3),
                    'total_usage': total_usage
                }

    async def get_saved_stickers(self, limit: int = 50) -> list[Sticker]:
        """Get stickers saved to account."""
        return await self.get_stickers(saved_only=True, limit=limit)

    async def get_recent_stickers(self, limit: int = 20) -> list[Sticker]:
        """Get recently saved stickers."""
        async with self._lock:
            with self._conn() as conn:
                rows = conn.execute(
                    'SELECT * FROM stickers WHERE recent_saved = 1 ORDER BY last_seen DESC LIMIT ?',
                    (limit,)
                ).fetchall()
                return [self._row_to_sticker(row) for row in rows]

    def _row_to_sticker(self, row: sqlite3.Row) -> Sticker:
        """Convert database row to Sticker dataclass."""
        return Sticker(
            doc_id=row['doc_id'],
            access_hash=row['access_hash'],
            file_reference=row['file_reference'],
            mime_type=row['mime_type'],
            emoji=row['emoji'],
            stickerset_id=row['stickerset_id'],
            stickerset_access_hash=row['stickerset_access_hash'],
            stickerset_short_name=row['stickerset_short_name'],
            is_animated=bool(row['is_animated']),
            is_video=bool(row['is_video']),
            vision_summary=row['vision_summary'],
            vision_tags=row['vision_tags'],
            nsfw_score=row['nsfw_score'] or 0.0,
            mood_tags=row['mood_tags'],
            quality_score=row['quality_score'] or 0.5,
            spam_score=row['spam_score'] or 0.0,
            reaction_score=row['reaction_score'] or 0,
            usage_count=row['usage_count'] or 0,
            first_seen=row['first_seen'],
            last_seen=row['last_seen'],
            first_sender_id=row['first_sender_id'],
            saved_to_account=bool(row['saved_to_account']),
            saved_at=row['saved_at'],
            recent_saved=bool(row['recent_saved']),
            last_message_id=row['last_message_id']
        )

    # ============ STICKER SET METHODS ============

    async def add_sticker_set(self, sticker_set: StickerSet) -> None:
        """Insert or update a sticker set."""
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    '''INSERT INTO sticker_sets (
                        set_id, access_hash, short_name, title, count,
                        is_animated, is_video, is_official, installed, installed_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(set_id) DO UPDATE SET
                        access_hash=excluded.access_hash,
                        short_name=excluded.short_name,
                        title=excluded.title,
                        count=excluded.count,
                        is_animated=excluded.is_animated,
                        is_video=excluded.is_video,
                        is_official=excluded.is_official,
                        installed=excluded.installed,
                        installed_at=excluded.installed_at,
                        updated_at=excluded.updated_at''',
                    sticker_set.to_row()
                )
                conn.commit()

    async def get_sticker_set(self, set_id: int) -> StickerSet | None:
        """Get a sticker set by ID."""
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute('SELECT * FROM sticker_sets WHERE set_id=?', (set_id,)).fetchone()
                if not row:
                    return None
                return StickerSet(
                    set_id=row['set_id'],
                    access_hash=row['access_hash'],
                    short_name=row['short_name'],
                    title=row['title'],
                    count=row['count'] or 0,
                    is_animated=bool(row['is_animated']),
                    is_video=bool(row['is_video']),
                    is_official=bool(row['is_official']),
                    installed=bool(row['installed']),
                    installed_at=row['installed_at'],
                    updated_at=row['updated_at'] or 0
                )

    async def get_sticker_set_by_short_name(self, short_name: str) -> StickerSet | None:
        """Get a sticker set by short name."""
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute('SELECT * FROM sticker_sets WHERE short_name=?', (short_name,)).fetchone()
                if not row:
                    return None
                return StickerSet(
                    set_id=row['set_id'],
                    access_hash=row['access_hash'],
                    short_name=row['short_name'],
                    title=row['title'],
                    count=row['count'] or 0,
                    is_animated=bool(row['is_animated']),
                    is_video=bool(row['is_video']),
                    is_official=bool(row['is_official']),
                    installed=bool(row['installed']),
                    installed_at=row['installed_at'],
                    updated_at=row['updated_at'] or 0
                )

    async def mark_sticker_set_installed(self, set_id: int) -> None:
        """Mark a sticker set as installed."""
        now = int(time.time())
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    'UPDATE sticker_sets SET installed = 1, installed_at = ?, updated_at = ? WHERE set_id = ?',
                    (now, now, set_id)
                )
                conn.commit()

    async def get_installed_sticker_sets(self) -> list[StickerSet]:
        """Get all installed sticker sets."""
        async with self._lock:
            with self._conn() as conn:
                rows = conn.execute(
                    'SELECT * FROM sticker_sets WHERE installed = 1 ORDER BY updated_at DESC'
                ).fetchall()
                return [
                    StickerSet(
                        set_id=row['set_id'],
                        access_hash=row['access_hash'],
                        short_name=row['short_name'],
                        title=row['title'],
                        count=row['count'] or 0,
                        is_animated=bool(row['is_animated']),
                        is_video=bool(row['is_video']),
                        is_official=bool(row['is_official']),
                        installed=bool(row['installed']),
                        installed_at=row['installed_at'],
                        updated_at=row['updated_at'] or 0
                    )
                    for row in rows
                ]
