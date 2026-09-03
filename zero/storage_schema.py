"""SQLite DDL for ZeroStore. Kept byte-stable so migrations stay in one place."""
from __future__ import annotations

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
  sent_at INTEGER NOT NULL,
  trigger_type TEXT NOT NULL DEFAULT 'auto'
);
CREATE INDEX IF NOT EXISTS idx_sticker_send_history_chat ON sticker_send_history(chat_id, sent_at DESC);
CREATE TABLE IF NOT EXISTS gif_send_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL,
  doc_id INTEGER NOT NULL,
  sent_at INTEGER NOT NULL,
  trigger_type TEXT NOT NULL DEFAULT 'auto'
);
CREATE INDEX IF NOT EXISTS idx_gif_send_history_chat ON gif_send_history(chat_id, sent_at DESC);

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
