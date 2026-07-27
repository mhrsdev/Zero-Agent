# Zero Memory V2 — Data Model

## Tables

`memory_v2_items`

| column | purpose |
|---|---|
| `id` | UUID primary key |
| `item_type` | `profile`, `fact`, `episode`, `project`, `group`, `procedure`, `session_summary` |
| `scope`, `chat_id`, `user_id`, `session_id`, `project_key` | hard ownership filters |
| `subject`, `predicate`, `value_json` | atomic fact key/value; nullable for episode |
| `summary`, `entities_json`, `topics_json` | bounded render/search material |
| `importance`, `confidence` | 0..1 validated |
| `event_type`, `occurred_at`, `valid_from`, `valid_until`, `expires_at` | time semantics |
| `status` | active/superseded/disputed/deleted/expired |
| `supersedes`, `contradicted_by` | explicit lineage |
| `source_json`, `content_hash` | provenance and dedup |
| `access_count`, `last_accessed_at` | telemetry, not primary relevance |
| `created_at`, `updated_at` | audit |

`memory_v2_fts` is FTS5 over `summary`, `subject`, `predicate`, `value_text`, and `topics`; it joins back to `memory_v2_items` and never bypasses scope/status filtering.

`memory_v2_sessions` stores one structured `state_json` per `(chat_id,user_id,session_id)` with fields: `active_topic`, `user_goal`, `confirmed_facts`, `unresolved_questions`, `decisions`, `constraints`, `completed_actions`, `pending_actions`, `referenced_entities`, `files_or_resources`, `last_updated_turn`.

`memory_v2_audit` has event type, item id, scope identifiers, trace id, reason code and redacted metadata. `memory_v2_metrics` stores numeric per-turn counters only.

## Conflict rules

Natural uniqueness key is `(scope, chat_id, user_id, project_key, item_type, subject, predicate)`. Exact normalized payload refreshes provenance/access; trusted changed payload creates a new row and marks the previous active row `superseded`. Low-confidence incompatible material becomes `disputed`; no duplicate active fact is injected.

Source priority: direct user statement > explicit owner correction > verified test/result > deterministic inference > summary. Sensitive values are rejected before persistence.