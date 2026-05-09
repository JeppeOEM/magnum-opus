---
stepsCompleted: ['step-01-validate-prerequisites', 'step-02-design-epics']
status: in-progress
service: bot-service
inputDocuments:
  - '_bmad-output/planning-artifacts/architecture.md'
  - '_bmad-output/brainstorming/brainstorming-session-2026-05-09-1116.md'
requirementsRef: '_bmad-output/planning-artifacts/epics-requirements.md'
---

# magnum-opus — Bot Service Epic Breakdown

> Requirements inventory (BS-FR, BS-NFR definitions) → [epics-requirements.md](epics-requirements.md)

_Added 2026-05-09. Epics 11–16 covering the Python FastAPI Bot Service (strategy execution, trade persistence, backtesting, observability)._

## Bot Service Epic List

### Epic 11: Bot Service Foundation — Signal Computation

mrqdt can write a strategy subclass that subscribes to candle events from Redis, builds multi-timeframe DataFrames cold-started from QuestDB history, and computes signals with NaN protection, gap invalidation, lookback gating, and risk gate enforcement. The service scaffold (FastAPI `/health`, Bus Manager thread, per-strategy thread model, QuestDB DDL) is in place. No trading yet — signal computation pipeline only.

**FRs covered:** BS-FR1, BS-FR2, BS-FR3, BS-FR4, BS-FR5, BS-FR6, BS-FR7, BS-FR8, BS-FR9, BS-FR10, BS-FR11, BS-FR12, BS-FR13, BS-FR36 (basic /health), BS-FR37, BS-FR38
**NFRs addressed:** BS-NFR1, BS-NFR2, BS-NFR3, BS-NFR4, BS-NFR6, BS-NFR8 (service foundation)

### Epic 12: Trade Execution — Paper Trading

mrqdt can run a strategy end-to-end in paper mode — signals produce OrderRequests that flow through the order queue worker, are simulated by PaperExchangeClient (latency + tick-based fill), and all trade events (placed, filled, rejected, cancelled, failed) are persisted to QuestDB `order_events` with `paper_trading=true`. Real exchange client infrastructure (httpx + private WebSocket) is also built in this epic so the same strategy runs live when `paper_trading=False`.

**FRs covered:** BS-FR14, BS-FR15, BS-FR16, BS-FR17, BS-FR18, BS-FR19, BS-FR20, BS-FR26, BS-FR27, BS-FR28
**NFRs addressed:** BS-NFR5 (credential security for exchange auth), BS-NFR7 (ILP fire-and-forget)

### Epic 13: Strategy Lifecycle & Crash Safety

mrqdt can deploy strategies without manual process management: the file watcher auto-loads `.py` files from `strategies/active/`, the watchdog restarts crashed threads with exponential backoff, and after any crash (including mid-order-lifecycle crashes) the service reconciles open positions and non-terminal orders against exchange REST on startup so no position is silently lost. Heartbeat emergency close is available for microstructure strategies.

**FRs covered:** BS-FR21, BS-FR22, BS-FR23, BS-FR24, BS-FR25, BS-FR34
**NFRs addressed:** BS-NFR8 (position recovery after crash)

### Epic 14: Backtrader Backtesting — Validate Before Deploying

mrqdt can validate any strategy on QuestDB historical data including all 67 microstructure features, with exact KuCoin/Bybit fee models, before risking real money. Walk-forward validation, stress-window testing (LUNA, FTX collapses), and Monte Carlo result shuffling are available. Signal logic lives in pure functions called identically by live and backtest paths — written once, tested once. Backtest results land in the same QuestDB `order_events` table with `backtest=True`, queryable in Grafana alongside paper results.

**FRs covered:** BS-FR29, BS-FR30, BS-FR31, BS-FR32, BS-FR33

### Epic 15: First Strategy Implementations

mrqdt has 2-3 concrete strategy implementations running in paper mode, each passing the fee impact gate (minimum required edge > 2 x (fee + expected slippage)) before proceeding. Target strategies: (1) OFI-signal microstructure bot using 1s bars, (2) funding rate arb continuous monitor, (3) MA-cross baseline for comparison. All signal logic lands in `strategy/signals/` pure functions shared with the Backtrader path. No new infrastructure — this epic exercises Epics 11-14 end-to-end with real strategy code.

**FRs covered:** (no new BS-FRs — exercises the full stack built in Epics 11-14)

### Epic 16: Production Observability & Operations

mrqdt can monitor all running strategies from Grafana — per-strategy P&L, open position sizes, drawdown, fill rate, consumer lag behind Redis streams, and order execution latency — via a Prometheus `/metrics` endpoint. The service runs in Docker Compose alongside the aggregator and candle service. A runbook entry covers deploy, rollback, credential rotation, and alert playbooks for strategy failures.

**FRs covered:** BS-FR35, BS-FR36 (production-hardened /metrics + /health)
**NFRs addressed:** BS-NFR5 (credentials in Docker Compose env_file pattern)

---

## Epic 11: Bot Service Foundation — Signal Computation

mrqdt can write a strategy subclass that subscribes to candle events from Redis, builds multi-timeframe DataFrames cold-started from QuestDB history, and computes signals with NaN protection, gap invalidation, lookback gating, and risk gate enforcement. The service scaffold (FastAPI `/health`, Bus Manager thread, per-strategy thread model, QuestDB DDL) is in place. No trading yet — signal computation pipeline only.

**FRs covered:** BS-FR1, BS-FR2, BS-FR3, BS-FR4, BS-FR5, BS-FR6, BS-FR7, BS-FR8, BS-FR9, BS-FR10, BS-FR11, BS-FR12, BS-FR13, BS-FR36 (basic /health), BS-FR37, BS-FR38
**NFRs addressed:** BS-NFR1, BS-NFR2, BS-NFR3, BS-NFR4, BS-NFR6, BS-NFR8 (service foundation)

**Implementation notes (pre-mortem + failure mode hardening):**
- NaN guard must validate DataFrame *inputs* before calling any signal function — not only the signal return value. Strategies must not call `fillna()` inside signal functions to mask NaN; this is an architectural prohibition enforced by BaseStrategy. Test: DataFrame with leading NaN rows from a cold-start join must produce zero OrderRequests. [Gap C applied: guard enforced at signal computation site, not only at handler boundary]
- GapMarker handler must do two things: (1) set `_signal_invalid[symbol] = True` to block new signals, AND (2) mark contaminated bars in the rolling DataFrame (set `has_gap=True` on the affected row). Post-gap clean-bar requirement must span the full indicator lookback window — not just N=1 clean bar — before `_signal_invalid` is cleared.
- Bus Manager crash must cause process exit (not silent thread death) so Docker restarts the whole service and reconciliation runs cleanly on startup. Bus Manager must be a daemon thread or supervised such that its death propagates to a controlled process exit. Emergency close (heartbeat timeout) must issue market-sell orders via direct exchange REST — not via the Redis-backed order queue — so it remains resilient when Redis is unavailable. [Gap A: Bus Manager uses exponential backoff (same policy as other services — initial=1s, multiplier=2, max=60s) for transient Redis read errors before escalating to process exit; error count and last-error logged at each retry]
- Per-strategy asyncio.Queue has a bounded size (default: `BOT_QUEUE_MAX_DEPTH=1000`). When full, Bus Manager drops the oldest event for that strategy, increments `bot_queue_drop_total{strategy}` Prometheus counter, and logs WARN. In addition, the Bus Manager immediately posts a synthetic `GapMarker` event to that strategy's queue (drop-oldest again if still full) — a queue drop is a silent bar gap and must be treated identically to a GapMarker received from the Redis stream. A strategy whose queue is consistently dropping events is a signal that it's processing too slowly — this metric should be alerted. [Gap B, Z3]
- Per-strategy asyncio heartbeat must be a **separate OS thread** (not an asyncio task) per strategy thread. A `threading.Event` is posted into the strategy's asyncio loop via `run_coroutine_threadsafe()` every 5 seconds; the heartbeat OS thread expects the event to be set within 10 seconds. If the event is not set (loop is frozen), the heartbeat thread calls `os.kill(os.getpid(), signal.SIGTERM)` to force process exit. An asyncio task cannot detect a frozen event loop because it is blocked by the same freeze — this design is therefore fundamentally broken and must not be used. `thread.is_alive()` is not sufficient for liveness. [Gap C, Z1]
- `subscribe()` must complete within 30 seconds (`BOT_SUBSCRIBE_TIMEOUT_S`, configurable). If it exceeds this, the strategy is not loaded: log ERROR with strategy name and exception, skip the file, continue loading other strategies. A strategy that fails subscribe() is NOT retried immediately — it will be picked up on the next file watcher scan (default: every 60 seconds). [Gap D]
- `get_history()` cold-start failure when QuestDB is unavailable must not block the strategy thread — return an empty DataFrame immediately, log WARN, and schedule a background retry every 30 seconds until QuestDB responds. Strategies start with an empty DataFrame and the lookback gate prevents signals until the window fills naturally (either via retry or rolling accumulation). The background retry calls `get_history()` only once successfully and then stops. [Gap E]
- Cold-start DataFrame continuity validation: after loading history from QuestDB, check for missing bar timestamps (gaps in the time series). If gap fraction exceeds 5% of the lookback window, log WARN with the gap details. Do not reject the DataFrame — allow natural warm-up — but increment `bot_coldstart_gap_fraction{strategy,symbol,tf}` metric for monitoring. [Gap F]
- Barrier late-event handling: if a BarClose arrives for a timestamp T after the barrier for T has already fired (timed out and delivered), discard the late event, increment `bot_barrier_late_event_total{strategy,symbol}`, and log WARN. This makes late-event frequency visible in Grafana — chronic lateness from one exchange indicates a barrier timeout misconfiguration. [Gap O]
- Barrier timeout is configurable per timeframe via env vars (`BOT_BARRIER_TIMEOUT_MS_1S=250`, `BOT_BARRIER_TIMEOUT_MS_1M=500`, `BOT_BARRIER_TIMEOUT_MS_5M=1000`, etc.). For 1s bar strategies the default 500ms consumes half the processing budget — 250ms is a safer default. For 1h+ strategies any value under 60,000ms is fine. [Gap P]
- QuestDB ILP Python client: use `questdb` package v2+ which supports the ILP sender API over TCP (port 9009) — NOT the REST API over port 9000. Specifically: `from questdb.ingress import Sender, Protocol`. The ILP `Sender` is NOT thread-safe; each thread that writes to QuestDB must have its own `Sender` instance. Flush policy: call `sender.flush()` after every `order_events` write (per-write flush) to minimize data loss on crash — write frequency is low (order lifecycle events) so per-write overhead is acceptable. The strategy threads may share a single dedicated QuestDB writer coroutine per strategy (within its own asyncio loop) which serializes writes and owns one `Sender`. [Gaps S, T]
- SIGTERM handler: `main.py` registers a `signal.signal(signal.SIGTERM, _shutdown_handler)`. On receipt: (1) set a global shutdown event, (2) signal all strategy threads to stop accepting new BarClose/Tick events (flush the asyncio queue), (3) allow in-flight order workers up to 30 seconds to settle (`BOT_SHUTDOWN_TIMEOUT_S`, configurable), (4) signal each strategy's asyncio loop (via `run_coroutine_threadsafe()`) to flush its own QuestDB ILP `Sender` from within that loop — the SIGTERM handler must NOT call `sender.flush()` directly from the main thread because ILP Sender is not thread-safe and each Sender is owned exclusively by its strategy loop, (5) exit 0. Orders still pending after 30 seconds are logged at WARN as "shutdown-interrupted" — they will be recovered by reconciliation on next startup. SIGKILL is not graceful; document this explicitly in the runbook. [Gap V, Z2]
- Configuration story required as the first story of Epic 11: write `bot-service/.env.example` documenting all env vars with defaults and descriptions. Minimum set: `KUCOIN_API_KEY`, `KUCOIN_API_SECRET`, `KUCOIN_API_PASSPHRASE`, `BYBIT_API_KEY`, `BYBIT_API_SECRET`, `REDIS_URL`, `QUESTDB_ILP_ADDR`, `BOT_QUEUE_MAX_DEPTH=1000`, `BOT_SUBSCRIBE_TIMEOUT_S=30`, `BOT_RECONCILIATION_TIMEOUT_S=120`, `BOT_WS_FALLBACK_TIMEOUT_LIVE=10`, `BOT_WS_FALLBACK_TIMEOUT_PAPER=30`, `BOT_BARRIER_TIMEOUT_MS_1S=250`, `BOT_BARRIER_TIMEOUT_MS_1M=500`, `BOT_SHUTDOWN_TIMEOUT_S=30`, `LOG_LEVEL=info`. No strategy may read `os.environ` directly — all config flows through a `Config` dataclass loaded at startup (same pattern as aggregator and candle service). [Gap W]

---

## Epic 12: Trade Execution — Paper Trading

mrqdt can run a strategy end-to-end in paper mode — signals produce OrderRequests that flow through the order queue worker, are simulated by PaperExchangeClient (latency + tick-based fill), and all trade events (placed, filled, rejected, cancelled, failed) are persisted to QuestDB `order_events` with `paper_trading=true`. Real exchange client infrastructure (httpx + private WebSocket) is also built in this epic so the same strategy runs live when `paper_trading=False`.

**FRs covered:** BS-FR14, BS-FR15, BS-FR16, BS-FR17, BS-FR18, BS-FR19, BS-FR20, BS-FR26, BS-FR27, BS-FR28
**NFRs addressed:** BS-NFR5 (credential security for exchange auth), BS-NFR7 (ILP fire-and-forget)

**Implementation notes (pre-mortem + failure mode hardening):**
- Three-layer open order state (BS-FR18) must explicitly address the crash-during-fill race: if the process crashes after QuestDB writes `status=placed` but before in-memory `self.open_orders` is updated, Epic 13's reconciliation will recover the fill — but this requires `self.open_orders` to be populated from the reconciliation result *before* the event loop starts (see Epic 13). The Epic 12 order queue story must include an L2 test: place order -> crash -> restart -> verify reconciliation populates `open_orders` correctly and no duplicate order is placed.
- Private WebSocket health must be determined by last-message-received timestamp, not connection state. A WebSocket that has sent no fill events in >10s (live) or >30s (paper) is treated as dead regardless of what the library reports as connection state. REST poll fallback activates on this timer. [Gap I]
- Order queue deduplication: at most one pending `entry` OrderRequest per `(symbol, side, order_role)` in the queue at any time, where `order_role in {entry, exit, stop}`. Deduplication applies **only to `entry` orders** — exit and stop orders must never be deduplicated or a stop-loss that follows an entry can be silently dropped. The dedup key `(symbol, side)` alone is too broad: it would block a stop-loss sell from following a buy entry. If a duplicate `entry` OrderRequest arrives, the new request is discarded and `bot_order_queue_dedup_total{strategy,symbol}` is incremented. [Gap G, Z4]
- PaperExchangeClient fill fallback: if the live tick stream for a symbol has no ticks during the simulated latency window (e.g., Redis stream empty), fill the order at the limit price and log WARN `paper_fill_no_ticks {strategy} {symbol}`. Never silently reject a paper fill — a rejected paper order that would have filled in reality creates misleading backtest divergence. [Gap H]
- Fill deduplication by `order_id`: a fill event for the same `order_id` can arrive simultaneously from the private WebSocket and from the REST poll fallback. The order worker must maintain a `_seen_fill_ids: set[str]` and discard any fill event whose `order_id` is already in the set. For REST poll fallback: sort returned fills by `ts_exchange` ascending before applying them — REST endpoints may return fills out of order, and applying them out of order can produce incorrect partial-fill accounting. [Z5]

---

## Epic 13: Strategy Lifecycle & Crash Safety

mrqdt can deploy strategies without manual process management: the file watcher auto-loads `.py` files from `strategies/active/`, the watchdog restarts crashed threads with exponential backoff, and after any crash (including mid-order-lifecycle crashes) the service reconciles open positions and non-terminal orders against exchange REST on startup so no position is silently lost. Heartbeat emergency close is available for microstructure strategies.

**FRs covered:** BS-FR21, BS-FR22, BS-FR23, BS-FR24, BS-FR25, BS-FR34
**NFRs addressed:** BS-NFR8 (position recovery after crash)

**Implementation notes (pre-mortem + failure mode hardening):**
- Reconciliation has a hard timeout of 120 seconds (`BOT_RECONCILIATION_TIMEOUT_S`). If it cannot complete exchange REST queries within this window (e.g., rate-limited, network partition), it degrades gracefully: use QuestDB-only state for `self.open_orders` and position tracking, log CRITICAL, mark all positions as `unconfirmed`, and continue an async background retry every 60 seconds until exchange confirmation is received. Strategies start with QuestDB-derived state and the `_managed_positions` force-subscribe still fires — positions are managed with the best available information. [Gap M]
- Reconciliation sequence is mandatory and synchronous (up to timeout): (1) query exchange REST for open positions and non-terminal orders, (2) cross-reference against QuestDB `order_events`, (3) populate `self.open_orders` and position state for ALL strategies, (4) THEN start the file watcher and spawn strategy threads. No strategy event loop may start before step 3 completes or the timeout is reached.
- Force-subscribe mechanism (BS-FR22) uses a BaseStrategy `_managed_positions: set[str]` attribute populated by reconciliation before `subscribe()` runs. Events for symbols in `_managed_positions` are routed to the strategy regardless of what `subscribe()` selected. Strategies cannot filter out `_managed_positions` symbols. L2 test: strategy subscribes to ETH only, open BTC position exists -> verify BTC BarClose events still routed.
- `self.open_orders` must be populated from reconciliation results before the first event is delivered to any strategy. Test: crash after `status=placed` write, before in-memory update -> restart -> verify no duplicate order placed.
- File watcher validates strategy class name uniqueness before spawning any thread. If two files define a class with the same name, both are rejected: log ERROR for each, neither is loaded, `bot_strategy_load_failure_total` incremented. Uniqueness is checked across all files in `strategies/active/` at each scan cycle, not just on new files. [Gap J]
- File watcher imports each strategy file in isolation via `importlib`. An ImportError or SyntaxError in one file logs ERROR and skips that file — it must not prevent other strategy files from loading. Each file import is wrapped in its own try/except. [Gap K]
- Watchdog backoff state is keyed by strategy *name* (the class name string), not by thread object. When a thread is replaced on restart, the new thread inherits the existing backoff state from the registry. Restart count and `last_restart_ts` persist in the watchdog registry for the lifetime of the process. [Gap L]
- Hot-reload on in-place file update (same filename, new content) follows a strict sequence: (1) send stop signal to the existing strategy thread and wait for exit up to `BOT_SHUTDOWN_TIMEOUT_S`, (2) call `importlib.invalidate_caches()` and remove the module from `sys.modules` to clear the module cache, (3) import the new module, (4) spawn a new thread. Hot-reload is NOT a live code swap — omitting step 2 leaves the old class definition bound in `sys.modules` so the new thread silently runs the old code. A strategy whose thread fails to stop within the shutdown timeout is force-killed (via `thread._stop()` as last resort) and logged CRITICAL before proceeding with the reload. [Z8]
- Cross-strategy position concentration is an **intentional design decision, not an oversight**: if two strategies both hold BTCUSDT long at 10% max each, the combined exposure is 20% with no system-level enforcement. mrqdt accepts this concentration risk. The rationale: strategies are independent signals; portfolio-level position caps require a shared state bus that introduces coupling and coordination overhead inconsistent with the thread-isolation model. This decision must be documented in `bot-service/DESIGN.md` so future implementers don't add cross-strategy caps thinking they're filling a gap. [Gap U]

---

## Epic 14: Backtrader Backtesting — Validate Before Deploying

mrqdt can validate any strategy on QuestDB historical data including all 67 microstructure features, with exact KuCoin/Bybit fee models, before risking real money. Walk-forward validation, stress-window testing (LUNA, FTX collapses), and Monte Carlo result shuffling are available. Signal logic lives in pure functions called identically by live and backtest paths — written once, tested once. Backtest results land in the same QuestDB `order_events` table with `backtest=True`, queryable in Grafana alongside paper results.

**FRs covered:** BS-FR29, BS-FR30, BS-FR31, BS-FR32, BS-FR33

**Implementation notes (pre-mortem + failure mode hardening):**
- Walk-forward and stress-test harnesses must produce a structured go/no-go report with explicit thresholds. A strategy passes only if ALL of the following hold on the out-of-sample window: Sharpe ratio >= 1.0, max drawdown <= 15%, out-of-sample P&L >= 70% of in-sample P&L (degradation <= 30%), and Monte Carlo 5th-percentile P&L > 0. These thresholds are defaults — mrqdt may tighten but not loosen them per strategy. The go/no-go report must be a machine-readable artifact (JSON) so Epic 15's first story can reference it as a prerequisite gate.
- Fee impact analysis is the *first* gate (cheapest to run), not a final gate. Run it before any backtesting to avoid wasting compute on strategies with insufficient edge.
- `QuestDBFeed` must filter rows where `has_gap=true` before feeding bars to Backtrader. Gap-flagged bars contain contaminated microstructure features (partial accumulation, snapshot reinit) and must not be presented as valid data. For each filtered gap bar, insert a NaT/NaN placeholder row (all fields NaN, timestamp NaT) so Backtrader's index remains contiguous and strategy code can detect the discontinuity. Strategy `next()` must guard against NaN rows using the same NaN guard used in live BaseStrategy — shared via the `strategy/signals/` pure-function layer. Without this filtering, backtests silently incorporate corrupted bars and the resulting performance metrics are biased. [Z7]
- `ReplayEngine` (`replay/engine.py` in architecture doc) is **out of scope for Epic 14**. Backtrader via `QuestDBFeed` is the sole backtesting mechanism in Epics 11-16. ReplayEngine (feeding QuestDB history through the live event bus in accelerated time) is useful for integration testing and debugging but adds significant complexity; it is deferred to a future epic or implemented informally as a developer tool if needed. Do not stub, scaffold, or partially implement `replay/` in any Epic 11-16 story. [Gap Y]

---

## Epic 15: First Strategy Implementations

mrqdt has 2-3 concrete strategy implementations running in paper mode, each passing the fee impact gate (minimum required edge > 2 x (fee + expected slippage)) before proceeding. Target strategies: (1) OFI-signal microstructure bot using 1s bars, (2) funding rate arb continuous monitor, (3) MA-cross baseline for comparison. All signal logic lands in `strategy/signals/` pure functions shared with the Backtrader path. No new infrastructure — this epic exercises Epics 11-14 end-to-end with real strategy code.

**FRs covered:** (no new BS-FRs — exercises the full stack built in Epics 11-14)

**Implementation notes (pre-mortem + failure mode hardening):**
- Gate sequence before paper deployment for each strategy: (1) fee impact analysis passes, (2) Epic 14 walk-forward go/no-go JSON report shows all thresholds met (Sharpe >= 1.0, drawdown <= 15%, degradation <= 30%, Monte Carlo 5th-pct > 0). A strategy that fails either gate is moved to `strategies/inactive/` — not deployed to paper. Both gates must be documented in the story acceptance criteria as explicit prerequisites.
- First story of each strategy must be the signal function in `strategy/signals/` with unit tests against known DataFrames — written before any live/paper wiring. This enforces the "signals written once, tested once" invariant from Epic 14.
- **Funding rate arb strategy (Story 3 of Epic 15) is BLOCKED on an unimplemented aggregator dependency.** Aggregator FR37-42 (perpetual futures feed: funding rate, open interest, perp mark price, basis) are listed in the architecture as "planned, not yet implemented." The funding rate arb strategy requires `funding:*` Redis stream events that the aggregator does not yet produce. Story 3 must not be started until the aggregator perp feed epic is complete and `funding:*` streams are present in Redis. In the meantime, Story 3 acceptance criteria must include an explicit prerequisite gate: "aggregator perp epic is done and `funding:{exchange}:{symbol}` stream entries are verifiable in Redis." Stories 1 (OFI bot) and 2 (MA-cross baseline) are unblocked. [Z6]
- Funding rate arb: FundingRate events older than 2x the funding interval (typically 2x 8h = 16h for KuCoin/Bybit) must be rejected as stale — do not act on them. Staleness is determined by comparing `next_funding_ts` in the event against `time.time()`. A stale event logs WARN and increments `bot_funding_rate_stale_total{strategy}`. [Gap Q]
- Funding rate arb unwind must be atomic in intent: close spot leg -> if spot close fails, abort and alert; if spot closes successfully -> close perp leg -> if perp close fails, log CRITICAL and alert immediately (half-unwound position now has unintended directional exposure). The alert must include both the open spot and perp positions and current PnL. Retry perp close every 5 seconds until filled or operator intervenes — never leave a half-unwound arb silently. [Gap R]

---

## Epic 16: Production Observability & Operations

mrqdt can monitor all running strategies from Grafana — per-strategy P&L, open position sizes, drawdown, fill rate, consumer lag behind Redis streams, and order execution latency — via a Prometheus `/metrics` endpoint. The service runs in Docker Compose alongside the aggregator and candle service. A runbook entry covers deploy, rollback, credential rotation, and alert playbooks for strategy failures.

**FRs covered:** BS-FR35, BS-FR36 (production-hardened /metrics + /health)
**NFRs addressed:** BS-NFR5 (credentials in Docker Compose env_file pattern)

**Implementation notes (failure mode hardening):**
- Prometheus gauges for per-strategy metrics (`bot_position_size`, `bot_drawdown`, `bot_unrealized_pnl`, etc.) must be reset to sentinel values (0 for sizes, NaN or a reserved value for P&L) when a strategy thread dies — before the watchdog attempts restart. Stale non-zero position_size on a dead strategy will appear in Grafana as an active position and could trigger false alerts. Reset happens synchronously in the watchdog's death-detection path, before the backoff sleep. [Gap N]
- `/health` response must include per-strategy status: `{"strategies": {"OFIBot": "running", "FundingRateArb": "restarting", ...}}` so that Grafana and the runbook can identify which strategies are live vs recovering at a glance.
- Docker Compose `mem_limit: 2g` for the bot service (5 strategies x 200 bars x 67 features x 8 bytes ~= 5MB data; Python/pandas process overhead + GC spikes ~= 500MB-1.5GB; 2g provides headroom). `depends_on: questdb: condition: service_healthy` — requires a QuestDB healthcheck defined in the compose file (e.g., `curl -f http://questdb:9000/health` every 10s, 3 retries, 30s start period) so bot service doesn't start until QuestDB is serving queries. [Gap X]
