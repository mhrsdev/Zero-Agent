# Site-by-Site Manual Audit Report — 2026-08-22

Every site below was verified by reading the real code, grepping call sites,
and running targeted tests — never by file name alone.
Method: `search_files` over gate/call-site patterns + targeted pytest runs +
faulthandler for hangs.

## Findings summary

| # | Site | Status | Action |
|---|------|--------|--------|
| 1 | `zero/sqlite_tx.py` + 184 call sites | healthy | full semantic audit in phase V-4; 3 defects fixed, 9 contract tests |
| 2 | `zero/automation.py` (kill/observe) | healthy | fail-closed; 21-case precedence matrix in tests/test_automation_precedence.py |
| 3 | `zero/reactions.py` (12 sites) | healthy | gate inside maybe_react, rate-limit, dedup, self-protection |
| 4 | `scripts/run_listener.py` maybe_react wiring | DEFECT | FIXED: reaction ran before the reply decision |
| 5 | `zero/triggers.py` decide_reply | healthy | spam_blocked > triggered > interject > no_need; pure & testable |
| 6 | `zero/social_awareness.py` SocialDecision | healthy | silence-first: conversation_in_progress / emotion / not_addressed -> ignore |
| 7 | `zero/brain.py` _should_interject | healthy | automation_disabled gate before every interjection |
| 8 | `zero/proactive_followups.py` | healthy | kill gate on create+send, observe_only postpone, rollout gate |
| 9 | `tests/test_automation_precedence.py::hammer` | DEFECT | FIXED: self-deadlock; explicit yield added |
| 10 | `scripts/doctor.py` (new) | new | health check with exit-code contract, warning-level credentials |
| 11 | `install.sh` / `install.ps1` (new) | new | idempotent one-line installers, no secret leakage |
| 12 | `tests/test_install_scripts.py` (new) | new | simulated cycle: doctor healthy/fail/json + both installer parsers |
| 13 | `zero/storage.py` rate-event methods | healthy | add_rate_event / get_rate_count with time partitioning |
| 14 | `evaluation/hard_eval.py`, `reaction_eval.py` | healthy | 107 labelled scenarios, accuracy=1.0, false-interjection=0 |
| 15 | `zero/config.py` ReactionsConfig | healthy | chance_percent/mode validation, observe-safe defaults |

## Defect details

### Defect A — reaction/reply ordering in the listener (site 4)
Evidence: `run_listener.py` previously called
`await reactions.maybe_react(event, incoming)` BEFORE
`brain.maybe_reply_with_media`. Therefore the opt-in react+reply mode never saw
`reply_pending=True` at runtime (dead feature at the only production call site).
Fix: moved the call after the reply decision with
`reply_pending=bool(decision.should_reply and answer != '__NO_REPLY__')`.
Positive side effect: if the reply pipeline crashes, no decorative emoji is sent
either (silence over noise).
Tests: tests/test_reactions.py + test_automation_switch.py +
test_automation_precedence.py -> 51 passed.

### Defect B — concurrency-test self-deadlock (site 9)
Evidence: faulthandler dump showed the thread stuck inside `hammer` (line 150).
`automation_disabled` is a coroutine with no internal await points, so the
`while not stop.is_set()` loop blocked the event loop and the flipper coroutine
was never scheduled.
Fix: explicit `await asyncio.sleep(0)` in hammer plus an explanatory comment.
Tests: same file -> 26 passed in <3s (previously hung until timeout).

## Execution evidence
- `pytest tests/test_reactions.py tests/test_automation_switch.py tests/test_automation_precedence.py -q` -> **51 passed in 2.92s**
- `pytest tests/test_install_scripts.py -q` -> **6 passed in 4.01s**
- `pytest ... -o faulthandler_timeout=45` -> exact stack trace of defect B (documented above)