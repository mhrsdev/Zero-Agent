# Zero Memory V2 — Benchmark and Acceptance

## Corpus

Use anonymized fixtures from V1 overload regressions: casual greeting, direct recall, project decision, multi-user group target, reply chain, old superseded fact, forwarded/bot/tool payload, restart and concurrent write.

## Measurements per turn

`candidate_count`, `retrieved_count`, `injected_count`, `injected_tokens`, scope/threshold/superseded rejections, retrieval latency, write candidates/commits/dedups/conflicts, total context tokens, memory ratio.

Precision is manually labelled: selected item is relevant only if it changes or verifies the correct response for that fixture. Report precision@k and cross-scope leak count; result count alone is not quality.

## Acceptance gates

- median injected-memory tokens at least 50% below V1 baseline
- zero cross-user leak, zero same-turn duplicate injection
- zero active superseded contradiction returned
- casual fixtures inject <=2 items (normally 0)
- project facts remain retrievable
`python3 scripts/benchmark_memory_v2.py --fixtures tests/fixtures/memory_v2_cases.json` emits JSON report. A benchmark must include the exact fixture count, V1/V2 medians, precision labels, and failures.

## Labelled regression run (2026-07-17)

Command: `python3 scripts/benchmark_memory_v2_labelled.py`

- Corpus: 50 anonymized synthetic structural regression cases; **0 direct production-log records** (raw logs were not copied to fixtures).
- Result: micro/macro precision `1.00`, recall `1.00`, F1 `1.00`; forbidden retrieval rate `0`; median/p95 selected tokens `8/10`; median/p95 retrieval latency `0.65/0.83 ms`.
- Limitation: this validates only controlled isolated fixtures. Shadow validation on anonymized real conversation structure remains required before readiness.

## Preliminary real-anonymized comparison (2026-07-17)

- Builder created a local, uncommitted review queue from SQLite conversation storage; hard detector matches were redacted before review.
- Accepted/exported fixture: 15 reviewed **no-memory** cases only. It passed a final privacy scan but is preliminary and has no positive-recall labels.
- V1/V2 command: `python3 scripts/benchmark_memory_v1_v2.py`.
- Real-corpus result: both no-memory accuracy `1.00`, forbidden rate `0`; V1 median/p95 prompt contribution `98/98` tokens; V2 `0/0` tokens. V2 median/p95 retrieval latency `8.88/10.17 ms`; V1 `104.43/110.91 ms` in this isolated harness.
- This corpus is insufficient for readiness: it has fewer than 30 cases and lacks reviewed positive retrieval labels.

## Positive-retrieval debug cycle

The expanded corpus now has 30 local real-anonymized cases (20 provenance-labelled positives, 8 no-memory). Its deterministic split is 20 development / 10 holdout. V2 failed 9 provenance-required cases in the initial trace: 6 had **no FTS candidate** (the source message and stored summary had no lexical overlap), and 3 had an FTS candidate but were rejected by the relevance threshold. None were rejected by scope, token packing, ranking, expiry, or supersession. This is a corpus-label/representation mismatch: direct source provenance proves how a memory was created, not that a later response needs the summary. V1 itself recalls only 0.69 development / 0.71 holdout on these labels. No broad threshold reduction was committed because it did not improve the development split sufficiently and would weaken the precision-first retrieval contract.
