# Changelog

## Memory Sprint — 2026-07-10

- Memory stage frozen after final Web Follow-up E2E.
- Final query reconstruction verified: `نرخ طلا امروز site:milli.gold`.
- SearXNG provider health and execution verified.
- Known limitation: the provider returned `result_count=0` for the `milli.gold` query during E2E. The system returned a truthful no-results response and did not invent a price or market explanation.
- No Telegram Search or Sticker Memory work started in this stage.
- Further non-critical improvements are deferred to VNext. Only critical security, data-loss, corruption, crash, or regression fixes are in scope after freeze.
