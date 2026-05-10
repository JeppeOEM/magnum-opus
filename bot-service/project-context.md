---
project_name: magnum-opus — Bot Service
user_name: mrqdt
date: '2026-05-09'
service: bot-service
optimized_for_llm: true
---

# Project Context: magnum-opus Bot Service

_Critical implementation rules for AI agents. Each rule captures a non-obvious constraint — things that cause silent failures, position loss, or wrong behavior if missed. Read before writing any code for the bot-service._

---

## What This Service Does

The Bot Service is a Python 3.11+ FastAPI daemon that runs trading strategies against live Redis candle and tick streams produced by the Candle Service. It maintains per-strategy asyncio event loops in dedicated threads, executes orders directly via KuCoin and Bybit REST APIs, stores the complete order lifecycle in QuestDB, and exposes Prometheus metrics.

It does NOT connect to exchange WebSocket market data feeds — all price/OB/candle data arrives via Redis Streams from the aggregator and candle-service.

It does NOT manage positions at a portfolio level — each strategy is independently isolated.

It does NOT serve a frontend — Grafana is the observability layer, QuestDB is the trade store.

---

## Technology Stack & Versions

- **Language:** Python 3.11+
- **Web framework:** FastAPI — for `/health`, `/version`, `/metrics` only; not for serving data
- **Async HTTP:** `httpx` (async) — all exchange REST calls; **never ccxt**
- **Redis client:** `redis-py` async (`redis.asyncio`) — read streams, write alerts, persist rolling windows
- **QuestDB client:** `questdb` package v2+ — ILP sender over TCP port **9009** (`from questdb.ingress import Sender, Protocol`); **NOT** the REST API over port 9000
- **Type checking:** `mypy --strict` (configured in `pyproject.toml`) — run in CI; type errors are build failures
- **Backtesting:** `backtrader` — `QuestDBFeed` subclasses `bt.feeds.PandasData`
- **Prometheus:** `prometheus-client` — all metrics via a single registry; never use default collectors for per-strategy gauges
- **Logging:** `structlog` — structured JSON output; bind `strategy`, `exchange`, `symbol` as context vars; credential redaction via a `structlog` processor that filters known patterns before any sink
- **Config:** `pydantic-settings` (`BaseSettings`) — all values from env vars; **no file outside `config.py` may access `os.environ` directly**
- **Directory:** `bot-service/` at the repo root — separate from aggregator and candle-service
- **Redis image:** `redis:7-alpine` (shared with other services)
- **QuestDB image:** `questdb/questdb:8.2.1` — never `:latest` or `:8.x`

---

## Package Layout

```
bot-service/
  main.py                      # FastAPI app, startup, SIGTERM handler, watchdog
  config.py                    # Config dataclass: all env vars, no os.environ in other files
  bus/
    event_types.py             # BarClose, Tick, OBSnapshot, FundingRate, AISignal,
                               #   GapMarker, OrderFilled, OrderRejected, MultiBarClose
    event_bus.py               # BusManager thread: reads Redis streams, routes to strategy queues
    barrier.py                 # MultiBarClose barrier: timeout + floor-ts matching
  strategy/
    base.py                    # BaseStrategy ABC: subscribe(), risk gate, NaN guard,
                               #   GapMarker handler, heartbeat, open_orders
    registry.py                # file watcher: strategies/active/ → spawn, inactive/ → stop
    signals/                   # pure signal functions — no framework dependency
      __init__.py
      ma_cross.py
      ofi_signal.py
      funding_rate_arb.py
  backtest/
    feed.py                    # QuestDBFeed: bt.feeds.PandasData + 67 microstructure lines
    commission.py              # KuCoin/Bybit fee model for bt.CommissionInfo
    runner.py                  # walk-forward harness, stress test runner (LUNA, FTX windows)
    monte_carlo.py             # shuffle TradeAnalyzer results, recompute P&L distribution
  exchange/
    kucoin.py                  # REST (httpx) + private WebSocket + HMAC auth
    bybit.py                   # REST (httpx) + private WebSocket + API key signing
    paper.py                   # PaperExchangeClient: latency sim + tick-based fill
  persistence/
    questdb.py                 # ILP writer: order_events + order_alerts
    reconciliation.py          # startup: exchange REST → QuestDB cross-reference
  metrics/
    prometheus.py              # all Prometheus gauges, counters, histograms + reset helpers
  strategies/
    active/                    # .py files watched by registry — spawned as strategy threads
    inactive/                  # .py files moved here → strategy thread stopped
  DESIGN.md                    # cross-strategy position concentration decision (required)
  .env.example                 # all env vars with defaults and descriptions
  Dockerfile
  requirements.txt
```

---

## Concurrency Model

**Central Bus Manager thread** (one, daemon): reads all Redis streams (`candles:close:*`, `ob_features:*`, `ticks:*`, `funding:*`), deserializes into typed dataclasses, routes events to per-strategy `asyncio.Queue` via `loop.run_coroutine_threadsafe(queue.put(event), loop)`. If the Bus Manager crashes, the process must exit (not silently die) — Bus Manager death must propagate to a controlled process exit so Docker restarts and reconciliation runs cleanly. **Transient Redis read errors:** Bus Manager retries with exponential backoff (initial=1s, multiplier=2, max=60s) before escalating to process exit; error count and last-error logged at each retry.

**Per-strategy thread** (one per loaded strategy): each runs its own `asyncio.new_event_loop()`. A crash in one thread cannot affect others. The strategy's event loop processes events from its queue, computes signals, and posts `OrderRequest` objects to the order queue.

**Loop reference handshake:** after a strategy thread creates its event loop (`loop = asyncio.new_event_loop()`), it stores the loop in a `StrategyHandle` dataclass (fields: `name`, `thread`, `loop`, `queue`) and registers it in the registry *before* calling `loop.run_forever()`. Bus Manager reads handles from the registry to obtain each strategy's `loop` reference for `run_coroutine_threadsafe`. Bus Manager must not route events to a strategy until its handle is registered — the registry signals readiness via a `threading.Event`.

**Per-strategy asyncio order queue + worker coroutine**: strategy posts `OrderRequest` and immediately continues processing events. The order worker coroutine executes the REST call and persists the result. Never block the event loop on a REST call:
```python
async with httpx.AsyncClient(timeout=10.0) as client:
    response = await client.post(url, json=payload, headers=headers)
```

**Heartbeat thread** (one per strategy, separate OS thread — NOT an asyncio task): posts a `threading.Event` into the strategy's asyncio loop via `run_coroutine_threadsafe()` every 5 seconds; expects the event to be set within 10 seconds. If not set (loop frozen), calls `os.kill(os.getpid(), signal.SIGTERM)` to force process exit. An asyncio task cannot detect a frozen event loop — do not use asyncio tasks for heartbeat.

**Watchdog** (in `main.py`): uses `thread.is_alive()` to detect **crashes** (thread is dead). This is distinct from the heartbeat thread which detects **freezes** (thread alive but loop unresponsive). Both mechanisms are required — they cover different failure modes. On crash detection: reset Prometheus gauges to sentinel values synchronously, then exponential backoff restart (5s→10s→30s→60s, max 60s). Backoff state keyed by strategy class name, not thread object — survives thread replacement.

---

## Python Code Standards

**Full type annotation is mandatory throughout the codebase — no exceptions.**

### Annotation Rules

- Every function and method must annotate all parameters and the return type, including `-> None` for void functions
- Every class attribute must be annotated at the class body level (not only in `__init__`)
- `from __future__ import annotations` at the top of every file — enables PEP 563 deferred evaluation and allows forward references without quotes
- Use `X | None` (union syntax, Python 3.10+) over `Optional[X]`
- Use `X | Y` over `Union[X, Y]`
- Use `collections.abc.Callable`, `collections.abc.Iterator`, `collections.abc.Sequence` — not `typing.Callable` etc.
- `Any` is forbidden unless the type is genuinely unknowable at the call boundary (e.g. raw Redis stream dict before parsing). Every `Any` use requires an inline comment explaining why.
- `TypeAlias` for complex repeated types (e.g. `StrategyName: TypeAlias = str`)
- `TypedDict` for structured dict shapes (e.g. Redis stream entries before deserialization)
- `Protocol` for structural interfaces instead of ABCs where duck typing is sufficient

### Type Checking

Run `mypy --strict` (or `pyright --strict`) in CI as part of `make test-l1`. Type errors are build failures — not warnings. Add `mypy` (or `pyright`) to `requirements-dev.txt` and configure in `pyproject.toml`:

```toml
[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_ignores = true
```

### `dataclass` and `BaseModel` patterns

- Use `@dataclass(frozen=True)` for immutable value objects (e.g. `OrderRequest`, `StrategyHandle`, event types)
- Use `pydantic.BaseModel` only for external data validation at system boundaries (e.g. parsing Redis stream entries, exchange REST responses)
- Use `BaseSettings` (from `pydantic-settings`) only in `config.py`
- Never use untyped dicts as internal data structures — define a `TypedDict` or `dataclass` instead

---

## Critical Behavioral Rules

### Config and Credential Isolation

All env vars are loaded once at startup into `config.py` via `pydantic-settings` `BaseSettings`. No file outside `config.py` may access `os.environ` directly. Credentials must not appear in logs, error messages, tracebacks, or Prometheus label values.

Implement a `structlog` processor (added to the processor chain before any sink) that redacts the **exact string values** of these fields from all log output: `KUCOIN_API_KEY`, `KUCOIN_API_SECRET`, `KUCOIN_API_PASSPHRASE`, `BYBIT_API_KEY`, `BYBIT_API_SECRET`, and any Redis URL that contains a password component (match `redis://:.*@`). The processor must redact values that appear embedded in longer strings (e.g. in exception tracebacks), not only exact-match top-level log fields.

### QuestDB ILP — Thread Safety

`questdb.ingress.Sender` is **NOT thread-safe**. Each thread that writes to QuestDB must own its own `Sender` instance. The strategy threads own their own `Sender`; `main.py` / reconciliation owns a separate one. Flush policy: call `sender.flush()` after every `order_events` write (per-write flush) — write frequency is low (order lifecycle events) so overhead is acceptable. Never call `sender.flush()` from a thread that does not own the sender.

SIGTERM handler must NOT call `sender.flush()` from the main thread. Instead, signal each strategy's asyncio loop via `run_coroutine_threadsafe()` to flush its own sender from within that loop.

### Strategy Queue — Bounded, GapMarker on Drop

Per-strategy `asyncio.Queue` has a bounded size (`BOT_QUEUE_MAX_DEPTH`, default 1000). When full, Bus Manager drops the **oldest** event for that strategy (not the new one), increments `bot_queue_drop_total{strategy}`, logs WARN, and immediately posts a synthetic `GapMarker` to that strategy's queue (drop-oldest again if still full). A queue drop is a silent bar gap — treat identically to a real GapMarker from the Redis stream.

### NaN Guard — Inputs, Not Outputs

The NaN guard in `BaseStrategy` must validate DataFrame **inputs** before calling any signal function — not only the signal return value. Strategies must not call `fillna()` inside signal functions to mask NaN; this is an architectural prohibition enforced by `BaseStrategy`. A DataFrame with leading NaN rows from a cold-start join must produce zero `OrderRequest` objects.

### Gap Invalidation — Full Lookback Window

When a `GapMarker` is received by `BaseStrategy`:
1. Set `_signal_invalid[symbol] = True` to block new signals.
2. Mark the affected bar row in the rolling DataFrame with `has_gap=True`.

`_signal_invalid[symbol]` is only cleared after the post-gap clean-bar count spans the **full indicator lookback window** — not just N=1 clean bar.

### Open Order State — Three Layers

1. **In-memory `self.open_orders` dict** — checked before every new `OrderRequest`. Key: `(symbol, side, order_role)` where `order_role in {entry, exit, stop}`.
2. **QuestDB write** — only after exchange confirmation; every outcome persisted (placed, open, partially_filled, filled, cancelled, rejected, failed).
3. **Startup reconciliation** — query exchange REST for non-terminal orders, cross-reference against QuestDB `order_events`, populate `self.open_orders` for ALL strategies before any event loop starts.

**Crash-during-fill race:** if the process crashes after QuestDB writes `status=placed` but before in-memory `self.open_orders` is updated, reconciliation recovers the fill. `self.open_orders` must be populated from the reconciliation result *before* the first event is delivered to any strategy.

### Order Queue Deduplication — Entry Only

At most one pending `entry` OrderRequest per `(symbol, side, order_role)` in the queue. Deduplication applies **only to `entry` orders** — exit and stop orders must NEVER be deduplicated. The dedup key `(symbol, side)` alone is too broad: it would block a stop-loss sell from following a buy entry. If a duplicate `entry` arrives, discard it and increment `bot_order_queue_dedup_total{strategy,symbol}`.

### Fill Deduplication

A fill event for the same `order_id` can arrive simultaneously from the private WebSocket and from the REST poll fallback. Maintain `_seen_fill_ids: set[str]` in the order worker — discard fills whose `order_id` is already in the set. For REST poll fallback: sort fills by `ts_exchange` ascending before applying — REST responses may be out of order.

### Private WebSocket Health — Timestamp Not State

WebSocket health is determined by **last-message-received timestamp**, not library-reported connection state. A WebSocket that has sent no fill events in >10s (live) or >30s (paper) is treated as dead regardless of what the library reports. REST poll fallback activates on this timer.

### Reconciliation — Hard Timeout + Graceful Degradation

Reconciliation has a hard timeout of 120 seconds (`BOT_RECONCILIATION_TIMEOUT_S`). If it cannot complete exchange REST queries within this window (rate-limited, network partition), degrade gracefully: use QuestDB-only state for `self.open_orders` and position tracking, log CRITICAL, mark all positions as `unconfirmed`, continue a background retry every 60 seconds. Strategies start with QuestDB-derived state — `_managed_positions` force-subscribe still fires.

**Reconciliation sequence is mandatory and synchronous before any strategy thread starts:**
1. Query exchange REST for open positions and non-terminal orders
2. Cross-reference against QuestDB `order_events`
3. Populate `self.open_orders` and position state for ALL strategies
4. THEN start the file watcher and spawn strategy threads

### File Watcher — Class Name Uniqueness

The file watcher validates strategy class name uniqueness before spawning any thread. If two files define a class with the same name, both are rejected (log ERROR for each, increment `bot_strategy_load_failure_total`). Uniqueness is checked across ALL files in `strategies/active/` at each scan cycle — not just new files.

Each file is imported in isolation via `importlib`. An `ImportError` or `SyntaxError` in one file logs ERROR and skips that file — must not prevent other strategy files from loading.

### Hot-Reload Sequence

When a file is updated in-place (same filename, new content):
1. Send stop signal to the existing strategy thread; wait up to `BOT_SHUTDOWN_TIMEOUT_S`
2. Call `importlib.invalidate_caches()` and remove the module from `sys.modules` to clear the module cache
3. Import the new module
4. Spawn a new thread

Omitting step 2 leaves the old class definition bound in `sys.modules` — the new thread silently runs the old code.

### Prometheus — Reset on Strategy Death

Per-strategy gauges (`bot_position_size`, `bot_drawdown`, `bot_unrealized_pnl`, etc.) must be reset to sentinel values (0 for sizes, NaN for P&L) **synchronously in the watchdog's death-detection path, before the backoff sleep**. Stale non-zero `position_size` on a dead strategy appears in Grafana as an active position and triggers false alerts.

### Cross-Strategy Position Concentration

Cross-strategy position concentration is an **intentional design decision, not a gap**. If two strategies both hold BTCUSDT long at 10% max each, combined exposure is 20% with no system-level enforcement. This is documented in `bot-service/DESIGN.md`. Do not add cross-strategy position caps — they require a shared state bus that breaks thread isolation.

### PaperExchangeClient — Never Silently Reject

If the live tick stream for a symbol has no ticks during the simulated latency window (Redis stream empty), fill the order at the limit price and log WARN `paper_fill_no_ticks {strategy} {symbol}`. Never silently reject a paper fill — a rejected paper order that would have filled in reality creates misleading backtest divergence.

### Backtest — Filter Gap-Flagged Bars

`QuestDBFeed` must filter rows where `has_gap=True` before feeding bars to Backtrader. For each filtered gap bar, insert a NaT/NaN placeholder row so Backtrader's index remains contiguous. Strategy `next()` must guard against NaN rows using the same NaN guard used in live `BaseStrategy` (via the shared `strategy/signals/` pure-function layer).

### subscribe() Timeout

`subscribe()` must complete within 30 seconds (`BOT_SUBSCRIBE_TIMEOUT_S`). If it exceeds this, do not load the strategy: log ERROR, skip the file, continue loading others. A strategy that fails `subscribe()` is NOT retried immediately — picked up on the next file watcher scan (default: every 60 seconds).

### get_history() Cold-Start Failure

`get_history()` cold-start failure when QuestDB is unavailable must not block the strategy thread. Return an empty DataFrame immediately, log WARN, and schedule a background retry every 30 seconds until QuestDB responds. The lookback gate prevents signals until the window fills naturally.

### Funding Rate Arb — Staleness Guard

`FundingRate` events older than 2× the funding interval (typically 16h for KuCoin/Bybit 8h funding) must be rejected as stale. Compare `next_funding_ts` in the event against `time.time()`. A stale event logs WARN and increments `bot_funding_rate_stale_total{strategy}`.

### Funding Rate Arb — Atomic Unwind Intent

Unwind sequence: close spot leg → if spot close fails, abort and alert; if spot closes → close perp leg → if perp close fails, log CRITICAL and alert immediately. Retry perp close every 5 seconds until filled or operator intervenes. Never leave a half-unwound arb silently.

### ReplayEngine — Out of Scope for Epics 11–16

`replay/engine.py` mentioned in the architecture doc is **explicitly out of scope for Epics 11–16**. Do not stub, scaffold, or partially implement `replay/` in any Epic 11–16 story.

---

## SIGTERM Shutdown Sequence

`main.py` registers `signal.signal(signal.SIGTERM, _shutdown_handler)`. On receipt:
1. Set a global shutdown event
2. Signal all strategy threads to stop accepting new events (flush asyncio queue)
3. Allow in-flight order workers up to 30 seconds to settle (`BOT_SHUTDOWN_TIMEOUT_S`)
4. Signal each strategy's asyncio loop (via `run_coroutine_threadsafe()`) to flush its own QuestDB ILP `Sender`
5. Exit 0

Orders still pending after 30 seconds are logged at WARN as "shutdown-interrupted" — recovered by reconciliation on next startup. SIGKILL is not graceful; document this in the runbook.

---

## Naming Conventions

- **Python:** `snake_case` for everything except class names (`PascalCase`), constants (`UPPER_SNAKE_CASE`)
- **Prometheus metrics prefix:** `bot_`
  - Required counters: `bot_queue_drop_total{strategy}`, `bot_order_queue_dedup_total{strategy,symbol}`, `bot_strategy_load_failure_total`, `bot_barrier_late_event_total{strategy,symbol}`, `bot_funding_rate_stale_total{strategy}`, `bot_flush_alert_failure_total`
  - Required gauges: `bot_position_size{strategy,symbol}`, `bot_unrealized_pnl{strategy}`, `bot_drawdown{strategy}`, `bot_consumer_lag{strategy}`, `bot_coldstart_gap_fraction{strategy,symbol,tf}` (ratio, 0.0–1.0 — gauge not counter; decreases on re-warm after restart)
  - Required histograms: `bot_order_execution_latency_ms{strategy,exchange}`, `bot_fill_latency_ms{strategy,exchange}`
- **Env vars** (`UPPER_SNAKE_CASE`):
  - `KUCOIN_API_KEY`, `KUCOIN_API_SECRET`, `KUCOIN_API_PASSPHRASE`
  - `BYBIT_API_KEY`, `BYBIT_API_SECRET`
  - `REDIS_URL`, `QUESTDB_ILP_ADDR`
  - `BOT_CONSUMER_GROUP=bot-service` — Redis consumer group name; must match across restarts
  - `BOT_QUEUE_MAX_DEPTH=1000`
  - `BOT_SUBSCRIBE_TIMEOUT_S=30`
  - `BOT_RECONCILIATION_TIMEOUT_S=120`
  - `BOT_WS_FALLBACK_TIMEOUT_LIVE=10`
  - `BOT_WS_FALLBACK_TIMEOUT_PAPER=30`
  - `BOT_BARRIER_TIMEOUT_MS_1S=250`
  - `BOT_BARRIER_TIMEOUT_MS_1M=500`
  - `BOT_BARRIER_TIMEOUT_MS_5M=1000`
  - `BOT_BARRIER_TIMEOUT_MS_15M=2000`
  - `BOT_BARRIER_TIMEOUT_MS_1H=5000`
  - `BOT_BARRIER_TIMEOUT_MS_4H=10000`
  - `BOT_BARRIER_TIMEOUT_MS_1D=30000`
  - `BOT_BARRIER_TIMEOUT_MS_1W=60000`
  - `BOT_SHUTDOWN_TIMEOUT_S=30`
  - `LOG_LEVEL=info`
- **slog-equivalent:** Python `logging` fields in `snake_case`; use `error` not `err`

---

## QuestDB Tables

### `order_events` (append-only)
```sql
CREATE TABLE order_events (
    ts                  TIMESTAMP,
    order_id            SYMBOL,
    client_order_id     SYMBOL,
    strategy            SYMBOL,
    exchange            SYMBOL,
    symbol              SYMBOL,
    market_type         SYMBOL,      -- spot / perp
    side                SYMBOL,      -- buy / sell
    order_type          SYMBOL,      -- limit / market
    status              SYMBOL,      -- open/partially_filled/filled/cancelled/rejected/failed/orphaned
    limit_price         DOUBLE,
    stop_price          DOUBLE,
    take_profit_price   DOUBLE,
    requested_size      DOUBLE,
    filled_size         DOUBLE,
    remaining_size      DOUBLE,
    avg_fill_price      DOUBLE,
    fee                 DOUBLE,
    fee_currency        SYMBOL,
    realized_pnl        DOUBLE,
    slippage            DOUBLE,
    position_size_after DOUBLE,
    signal_type         SYMBOL,
    paper_trading       BOOLEAN,
    backtest            BOOLEAN,
    ts_placed           TIMESTAMP,
    ts_exchange         TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY WAL;
```

### `order_alerts` (operator attention)
```sql
CREATE TABLE order_alerts (
    ts           TIMESTAMP,
    order_id     SYMBOL,
    strategy     SYMBOL,
    alert_type   SYMBOL,   -- orphaned / reconciliation_mismatch / fill_without_order
    detail       STRING,
    resolved     BOOLEAN
) TIMESTAMP(ts) PARTITION BY DAY WAL;
```

DDL approach: execute both `CREATE TABLE IF NOT EXISTS` statements via the QuestDB REST API (`POST /exec`) on startup, before the Bus Manager starts consuming. This is idempotent — safe to re-run on every restart. Schema evolution uses `ALTER TABLE ... ADD COLUMN` statements appended to the startup sequence in version order; `DROP COLUMN` and `RENAME COLUMN` are not supported by QuestDB and must never appear. ILP `Sender` is per-write flushed for `order_events` and `order_alerts`.

---

## Redis Stream Contracts

### Consuming
- `candles:close:{exchange}:{symbol}:{tf}` — closed bar events (`is_complete: "true"` only)
- `candles:{exchange}:{symbol}:{tf}` — partial + close bar events
- `ob_features:{exchange}:{symbol}` — OB feature snapshots on each 1s bar close
- `ticks:{exchange}:{symbol}` — raw tick stream (Tick + GapMarker events)
- `funding:{exchange}:{symbol}` — funding rate events (once aggregator perp epic is complete)

Consumer group: Bus Manager uses `XREADGROUP`. On unknown message type: log WARN, XACK, continue — never stall.

### Publishing
- `alerts:flush_failure` — flush failure alerts (if bot-service extends the cold-storage flush path in future)

---

## Test Architecture

| Layer | Scope | Runs in CI |
|---|---|---|
| L1 | Signal pure functions with known DataFrames; NaN propagation; GapMarker invalidation; barrier logic; order dedup | Yes |
| L2 | Event bus routing; per-strategy thread isolation; order queue; position reconciliation; heartbeat timeout — using mocked exchange clients and Redis | Yes |
| L3 | Kill Redis mid-session; kill QuestDB mid-write; inject gap markers; partition private WebSocket — verify all recovery paths | Local |
| L4 | Exchange testnet: place orders, crash at each lifecycle stage, verify reconciliation — real API calls against testnet endpoints | Manual pre-deploy |

**No live exchange L5** — paper trading (30+ days) is the production validation gate, not an automated test layer.

### Test Layer Conventions (Python)

Tests are separated using **pytest marks**, registered in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "l1: pure-function unit tests, no IO",
    "l2: integration tests with mocked Redis and exchange clients",
    "l3: chaos tests requiring live Redis and QuestDB containers",
    "l4: exchange testnet tests (manual pre-deploy only)",
]
```

Tag every test function: `@pytest.mark.l1`, `@pytest.mark.l2`, etc. Unmarked tests are treated as L1.

**Makefile targets:**
```makefile
typecheck: mypy --strict bot-service/
test-l1:  pytest -m "l1 or not l2 and not l3 and not l4" -x
test-l2:  pytest -m "l2" -x
test-l3:  pytest -m "l3" -x
test-all: mypy --strict bot-service/ && pytest -m "l1 or l2" --tb=short   # CI gate
```

`typecheck` runs before tests in CI — a type error blocks the test run. L1 + L2 run in CI on every push/PR. L3 runs locally. L4 is manual.

### Signal Pure Function Tests (L1)
First story of each strategy: write the signal function in `strategy/signals/` with `@pytest.mark.l1` unit tests against known DataFrames — written before any live/paper wiring. This enforces "signals written once, tested once."

### Backtest Go/No-Go Thresholds
Walk-forward pass requires ALL of: Sharpe >= 1.0, max drawdown <= 15%, out-of-sample P&L >= 70% of in-sample P&L, Monte Carlo 5th-percentile P&L > 0. These are minimums — mrqdt may tighten per strategy. Output as machine-readable JSON artifact.

---

## docker-compose Integration

```yaml
bot-service:
  image: ghcr.io/mrqdt/magnum-opus/bot-service:${VERSION}
  env_file: .env
  mem_limit: 2g
  cpus: 2.0
  stop_grace_period: 45s   # BOT_SHUTDOWN_TIMEOUT_S (30s) + 15s for per-strategy ILP Sender flushes
  depends_on:
    questdb:
      condition: service_healthy   # requires QuestDB healthcheck in compose file
    redis:
      condition: service_started
```

QuestDB healthcheck (required in compose file):
```yaml
questdb:
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:9000/health"]
    interval: 10s
    retries: 3
    start_period: 30s
```

---

## Forbidden Anti-Patterns

- `os.environ` access outside `config.py` — all env var reads go through `BaseSettings`
- Unannotated functions, methods, or class attributes — `mypy --strict` enforces this in CI
- `Optional[X]` or `Union[X, Y]` — use `X | None` and `X | Y` (Python 3.10+ syntax)
- Untyped dicts as internal data structures — use `TypedDict` or `@dataclass` instead
- `Any` without an inline comment explaining why — every `Any` must be justified
- `ccxt` — use direct `httpx` REST calls with `async with httpx.AsyncClient() as client:` pattern only
- `asyncio.Queue` without a bounded `maxsize` (use `BOT_QUEUE_MAX_DEPTH`)
- Heartbeat implemented as an asyncio task — must be a separate OS thread
- Calling `sender.flush()` from a thread that does not own the `Sender`
- `fillna()` inside signal functions — architectural prohibition
- Using heartbeat as an asyncio task — it cannot detect a frozen event loop; must be a separate OS thread
- Using `thread.is_alive()` as a freeze detector — it detects crashes only; heartbeat detects frozen loops
- Starting strategy threads before reconciliation completes
- Deduplicating exit or stop orders — only `entry` orders are deduplicated
- Silently rejecting a paper fill — always fill at limit price if no ticks available
- ReplayEngine (`replay/engine.py`) — out of scope for Epics 11–16; do not stub or scaffold
- Stubbing, scaffolding, or partially implementing `replay/` in any Epic 11–16 story
- Auto-cancelling orphaned orders — write to `order_alerts`, alert operator

---

## Implementation Sequence

1. **Config + .env.example** (Epic 11) — `BaseSettings`, all env vars documented, `structlog` credential-redaction processor wired
2. **QuestDB startup DDL** (Epic 11) — `order_events` + `order_alerts` `CREATE TABLE IF NOT EXISTS` executed via REST `/exec` before Bus Manager starts
3. **Event types + Bus Manager** (Epic 11) — typed dataclasses, Redis `XREADGROUP`, per-strategy queue routing via `StrategyHandle`
4. **MultiBarClose barrier** (Epic 11) — floor-ts matching, per-timeframe timeout, partial delivery + synthetic GapMarker
5. **BaseStrategy + NaN guard + GapMarker handler** (Epic 11) — ABC, lookback gate, risk gate, signal invalidation, heartbeat OS thread
6. **Strategy registry + file watcher** (Epic 13) — class name uniqueness, `importlib` isolation, hot-reload sequence
7. **ExchangeClient** (Epic 12) — KuCoin + Bybit REST (`httpx`) + private WebSocket + auth; `async with httpx.AsyncClient()` pattern
8. **Order queue worker** (Epic 12) — per-strategy asyncio queue, `entry`-only dedup, three-layer open order state, QuestDB ILP persistence
9. **PaperExchangeClient** (Epic 12) — latency sim, tick-based fill, never-reject guarantee
10. **Position reconciliation** (Epic 13) — startup sequence, 120s hard timeout, graceful degradation to QuestDB-only state
11. **Watchdog + crash safety** (Epic 13) — exponential backoff, Prometheus gauge sentinel reset on death
12. **QuestDBFeed + Backtrader commission** (Epic 14) — 67 feature lines, gap bar `has_gap=True` filtering, NaN placeholder rows
13. **Backtest harness** (Epic 14) — walk-forward, LUNA/FTX stress windows, Monte Carlo, JSON go/no-go report
14. **Prometheus /metrics + production /health** (Epic 16) — per-strategy metrics, sentinel reset helpers, per-strategy status in `/health`
15. **docker-compose + runbook** (Epic 16) — bot-service profile, `service_healthy` depends_on, deploy/rollback/rotation docs

---

## Usage Guidelines

**For AI agents:** Read this file before writing any code for the bot-service. Rules in the aggregator or candle-service project-context.md (Go-specific patterns, ILP sender goroutine safety model, WebSocket transport) do not apply here. When in doubt about thread safety or async patterns, default to explicit serialization and per-thread resource ownership. Flag any rule that conflicts with a library default.

_Last updated: 2026-05-09_
