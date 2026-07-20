# Zero Memory V2 — Retrieval

## Pipeline

1. Deterministic intent classification: casual / recall / project / task / operational / social.
2. Scope allowlist: current user, current group, resolved target user, project only when project intent; never arbitrary peer profile.
3. Entity/topic extraction from current message and reply chain.
4. FTS5 candidate search (bounded overfetch).
5. Hard status/expiry/scope filter.
6. Multiplicative scoring:

```text
score = relevance × scope_match × confidence × freshness × importance
```

`scope_match` is 0 for an unauthorized scope. `relevance` must exceed threshold before score ranking. Recency cannot rescue irrelevant records.

7. Deduplicate equivalent fact keys and diversify same topic/entity.
8. Token pack by priority while preserving whole items.
9. Final relevance gate: practical answer value required.

## Defaults

- casual: 0–2 item, 240 token budget
- normal recall: 3 item, 500 token budget
- project/operational: 6 item, 900 token budget
- hard global ceiling: min(900, 15% usable model context)
- candidate limit 30; FTS overfetch 3x; relevance threshold .25; final score threshold .20

All defaults live in `MemoryV2Config`, not scattered literals.

## Selection reasons

Debug mode returns only redacted `id`, item type, scope, score components, token estimate, and reason (`selected`, `scope`, `expired`, `superseded`, `threshold`, `duplicate`, `budget`). No raw sensitive text is logged.

## Working context

Working Memory is independent of durable retrieval: current message, reply chain depth <=8, and at most 8 nearby records with duplicate quoted body removed. Old tool results are omitted unless the current turn explicitly requires a compact trusted result.