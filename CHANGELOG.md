# Changelog

## Full-project audit — 2026-09-02

Packaging and installation

- `requirements.lock` is regenerated as a multi-platform (`--universal`)
  resolution. It was a Linux-only resolution installed on every platform, and
  because it carries hashes pip switches to `--require-hashes` mode, where every
  transitive requirement must be pinned. `colorama` (a win32-only pytest
  dependency) was absent, so the documented Windows one-line install aborted.
- `requirements.txt` no longer carries pytest or pytest-asyncio. They were
  shipped into the Docker image and into every end-user install, and they were
  the reason the lock needed a platform-conditional dependency at all.
  Development installs need `-r requirements-dev.txt`; the docs now say so.
- `pyproject.toml` dependencies match `requirements.txt` again. The drift
  installed aiohttp 3.10.11 on the `pip install .` path — below the PYSEC floor
  `requirements.txt` documents — and omitted `tzdata`, so `zoneinfo` timezone
  validation raised `ZoneInfoNotFoundError` on Windows.
- `install.ps1` resolves `$Req` before branching and enables `Set-StrictMode`.
  It was assigned inside one install branch and interpolated into the closing
  instructions, printing `pip install -r` with no argument.
- `.dockerignore` excludes `config/zero.yaml` and repeats every state and
  credential pattern in recursive `**/` form. `config/` is COPYied into the
  image, so an operator who filled in credentials there baked them into a layer.
- `docker-compose.yml` disables the inherited image HEALTHCHECK for
  `zero-listener`, which serves no HTTP endpoint and so reported unhealthy
  forever.
- pytest configuration is consolidated in `pytest.ini`. The duplicate
  `[tool.pytest.ini_options]` block in `pyproject.toml` was silently ignored.

Runtime correctness

- Both web-search branches are time-bounded. Only the deep branch had a
  `wait_for`, so a normal search could run for the sum of every provider budget
  while holding the listener's global message lock.
- `HybridWeb.search` (sync, `asyncio.run`) is replaced by `search_hits`
  (awaited). Its only caller reached it through `asyncio.to_thread`, which bound
  the transport's shared per-host semaphore to a throwaway loop and then failed
  every later request with `RuntimeError: bound to a different event loop`,
  silently emptying news and knowledge digests for the process lifetime.
- A failing template job now advances `next_run_at` and records a failure
  metric. Previously only `cron_runs` was updated, so the 30s coordinator loop
  re-selected the same job on every tick — re-delivering the same digest to the
  group whenever the failure happened after the send succeeded.
- Listener background loops keep their task handles, log unexpected exits
  (`BACKGROUND_LOOP_CRASHED`), and are cancelled on shutdown. Both entrypoints
  now tear down: the listener disconnects Telethon, the panel releases its
  listening socket (`PanelAPI.stop`) and closes the bot session.
- The panel reads a bounded tail of each log file instead of the whole file on
  every SSE poll, and its SSE handlers no longer swallow `asyncio.CancelledError`.
- `scrypt` password work moved off the panel event loop.
- Every `with sqlite3.connect(...)` site now uses `zero.sqlite_tx.sqlite_txn`.
  `Connection.__exit__` commits but never closes; the V1→V3 migration leaked one
  handle per row, and on Windows an open handle locks the database file against
  the rollback path.
- SSRF DNS resolution in the extractor runs off the event loop, as do image
  base64 encoding and the SearXNG discovery fallback.
- Bounded previously unbounded state: connection-pool idle sockets (LRU by
  host), panel sessions and rate-limit windows, Telegram-search conversation
  state, the vision cooldown map, and the market price cache.

Release gates

- `scripts/scan_secrets.py` and `scripts/verify_public_artifact.py` detect
  virtualenvs structurally (`pyvenv.cfg`) and prune vendored and generated
  trees. Matching only the literal name `.venv` meant a differently named
  environment was scanned as project source, so both documented commands failed
  on an ordinary developer checkout.
- New regression suites: `tests/test_packaging_contract.py` and
  `tests/test_runtime_hardening.py`.

## Memory Sprint — 2026-07-10

- Memory stage frozen after final Web Follow-up E2E.
- Final query reconstruction verified: `نرخ طلا امروز site:milli.gold`.
- SearXNG provider health and execution verified.
- Known limitation: the provider returned `result_count=0` for the `milli.gold` query during E2E. The system returned a truthful no-results response and did not invent a price or market explanation.
- No Telegram Search or Sticker Memory work started in this stage.
- Further non-critical improvements are deferred to VNext. Only critical security, data-loss, corruption, crash, or regression fixes are in scope after freeze.
