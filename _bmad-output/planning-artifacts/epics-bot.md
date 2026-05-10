---
stepsCompleted: ['step-01-validate-prerequisites', 'step-02-design-epics', 'step-03-create-stories', 'step-04-final-validation']
status: complete
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

## Epic 11 Stories

---

### Story 11.1: Project Scaffold, Typed Configuration & Credential Redaction

**As** mrqdt,
**I want** a fully typed project scaffold with strict mypy, credential redaction in logs, and a documented env file,
**so that** all subsequent bot service stories start from a consistent, safe foundation.

**Acceptance Criteria:**

- **Given** the `bot-service/` directory,
  **When** `pyproject.toml` is present,
  **Then** it includes `[tool.mypy]` with `python_version = "3.11"`, `strict = true`, `warn_return_any = true`, `warn_unused_ignores = true`; and `[tool.pytest.ini_options]` with `markers` registering `l1`, `l2`, `l3`, `l4`

- **Given** `bot-service/bot_service/config.py`,
  **When** loaded at startup,
  **Then** it is a `pydantic_settings.BaseSettings` subclass with fields `kucoin_api_key: SecretStr`, `kucoin_api_secret: SecretStr`, `kucoin_api_passphrase: SecretStr`, `bybit_api_key: SecretStr`, `bybit_api_secret: SecretStr`, and all other env vars listed in the Epic 11 implementation notes; all fields sourced from env only (no `os.getenv` calls outside `config.py`)

- **Given** any log message or exception traceback produced by the bot service,
  **When** it is emitted via structlog,
  **Then** all occurrences of values matching `KUCOIN_API_KEY`, `KUCOIN_API_SECRET`, `KUCOIN_API_PASSPHRASE`, `BYBIT_API_KEY`, `BYBIT_API_SECRET`, and the Redis URL pattern `redis://:.*@` are replaced with `[REDACTED]`; the redaction processor is a structlog processor applied globally, not per call-site

- **Given** `bot-service/.env.example`,
  **When** read,
  **Then** it documents every env var with its default and a one-line description, including at minimum: the 5 exchange credentials, `REDIS_URL`, `QUESTDB_ILP_ADDR`, `BOT_QUEUE_MAX_DEPTH=1000`, `BOT_SUBSCRIBE_TIMEOUT_S=30`, `BOT_RECONCILIATION_TIMEOUT_S=120`, `BOT_WS_FALLBACK_TIMEOUT_LIVE=10`, `BOT_WS_FALLBACK_TIMEOUT_PAPER=30`, all 8 `BOT_BARRIER_TIMEOUT_MS_*` vars, `BOT_SHUTDOWN_TIMEOUT_S=30`, `LOG_LEVEL=info`

- **Given** `bot-service/Makefile`,
  **When** targets are invoked,
  **Then** `make typecheck` runs `mypy --strict bot_service/`; `make test-l1` runs `pytest -m "l1" -x`; `make test-all` runs `mypy --strict bot_service/ && pytest -m "l1 or l2" --tb=short`

- **Given** `bot-service/Dockerfile`,
  **When** built,
  **Then** it is a multi-stage build: a `builder` stage installs deps, a final stage copies only the installed packages and source; runs as a non-root user

- **Given** any `.py` file in `bot_service/`,
  **When** `mypy --strict` is run,
  **Then** it passes with zero errors; every file contains `from __future__ import annotations` as the first non-comment line

**Test coverage (l1):** Unit test for the credential redaction processor — feed a structlog event dict containing a raw secret value and assert the output is `[REDACTED]`; verify the Redis URL regex replaces the password component

---

### Story 11.2: QuestDB Schema — order_events & order_alerts

**As** mrqdt,
**I want** both QuestDB tables created idempotently at service startup via REST,
**so that** the schema exists before any write path runs and re-deployments are safe.

**Acceptance Criteria:**

- **Given** `bot_service/db/schema.py` (or equivalent),
  **When** `apply_schema(questdb_http_addr: str) -> None` is called,
  **Then** it issues `POST /exec` to QuestDB with `CREATE TABLE IF NOT EXISTS order_events` containing exactly: `order_id SYMBOL`, `strategy SYMBOL`, `symbol SYMBOL`, `exchange SYMBOL`, `side SYMBOL`, `order_role SYMBOL`, `order_type SYMBOL`, `status SYMBOL`, `qty DOUBLE`, `limit_price DOUBLE`, `fill_price DOUBLE`, `fill_qty DOUBLE`, `fee DOUBLE`, `paper_trading BOOLEAN`, `backtest BOOLEAN`, `ts_signal TIMESTAMP`, `ts_placed TIMESTAMP`, `ts_filled TIMESTAMP`, `ts_second TIMESTAMP`; designated timestamp `ts_second`; `PARTITION BY DAY WAL`

- **Given** the same call,
  **When** `apply_schema` runs,
  **Then** it also creates `order_alerts` with exactly: `strategy SYMBOL`, `symbol SYMBOL`, `alert_type SYMBOL`, `message STRING`, `ts TIMESTAMP`; designated timestamp `ts`; `PARTITION BY DAY WAL`

- **Given** QuestDB is reachable and both tables already exist,
  **When** `apply_schema` is called again,
  **Then** it succeeds without error (idempotent; `IF NOT EXISTS` prevents failure)

- **Given** QuestDB is unreachable (connection refused or HTTP 5xx),
  **When** `apply_schema` is called,
  **Then** it logs `ERROR` with the failure reason and raises an exception that causes the service entry point to exit with a non-zero code; the service does NOT start the Bus Manager or load any strategies

**Test coverage (l2):** Integration test using a real QuestDB container — call `apply_schema` twice, assert both tables exist with correct column types; call with unreachable QuestDB, assert exception raised

---

### Story 11.3: Event Type Definitions & Stream Entry Parser

**As** mrqdt,
**I want** typed, immutable event types and a single parser function that converts raw Redis stream entries to typed events,
**so that** all downstream code is fully type-checked with no `dict[str, str]` leaking past the bus boundary.

**Acceptance Criteria:**

- **Given** `bot_service/events.py`,
  **When** read by mypy --strict,
  **Then** it defines exactly these `@dataclass(frozen=True)` types: `BarClose`, `MultiBarClose`, `GapMarker`, `OBSnapshot`, `Tick`, `FundingRate`, `OrderFilled`, `OrderRejected`; and the type alias `BusEvent: TypeAlias = BarClose | GapMarker | OBSnapshot | Tick | FundingRate | OrderFilled | OrderRejected` (note: `MultiBarClose` is produced by the Barrier, not the parser)

- **Given** `parse_stream_entry(stream_key: str, entry: dict[str, str]) -> BusEvent | None`,
  **When** called with a valid entry for each event type,
  **Then** it returns the correctly typed frozen dataclass with all fields populated from the entry dict

- **Given** `parse_stream_entry` called with an unknown `stream_key` format,
  **When** executed,
  **Then** it returns `None` without raising an exception; it does NOT log at WARN or ERROR (unknown keys are silently dropped — high-volume path)

- **Given** `parse_stream_entry` called with a valid stream key but a missing required field in `entry`,
  **When** executed,
  **Then** it returns `None` and logs `WARN` with the stream key and missing field name (missing field is unexpected and warrants visibility)

- **Given** mypy --strict run on `events.py` and all files that import it,
  **When** executed,
  **Then** zero type errors; `BusEvent` union is exhaustive — match statements on it in other files will produce a mypy error if a new event type is added without updating the match

**Test coverage (l1):** One test per event type verifying round-trip from `dict[str, str]` to typed dataclass; one test for unknown stream_key → None; one test for missing field → None + WARN logged

---

### Story 11.4: Bus Manager — Redis Consumer & Event Router

**As** mrqdt,
**I want** a Bus Manager that reads from Redis XREADGROUP, routes events to per-strategy queues, and handles queue overflow and transient Redis errors safely,
**so that** events are delivered reliably and queue overflow never silently drops data.

**Acceptance Criteria:**

- **Given** `bot_service/bus_manager.py` with `StrategyHandle` and `BusManager`,
  **When** `StrategyHandle` is inspected,
  **Then** it is `@dataclass(frozen=True)` with fields `name: str`, `thread: threading.Thread`, `loop: asyncio.AbstractEventLoop`, `queue: asyncio.Queue[BusEvent]`

- **Given** a strategy registered with `BusManager.register(handle: StrategyHandle)` before `BusManager.start()`,
  **When** `BusManager.start()` is called,
  **Then** it waits on the strategy's `threading.Event` readiness signal before issuing the first `XREADGROUP` call; strategies that have not signalled ready within `BOT_SUBSCRIBE_TIMEOUT_S` are logged WARN and skipped

- **Given** a stream entry arriving for a strategy whose queue has `BOT_QUEUE_MAX_DEPTH` events already queued,
  **When** the Bus Manager attempts to deliver the event,
  **Then** it drops the OLDEST item from the queue (not the incoming event), increments `bot_queue_drop_total{strategy}`, logs WARN, and then immediately enqueues a synthetic `GapMarker(gap_cause="queue_overflow")` into that strategy's queue (applying the same drop-oldest policy if still full)

- **Given** a transient Redis error (connection reset, timeout) during `XREADGROUP`,
  **When** the error occurs,
  **Then** the Bus Manager retries with exponential backoff starting at 1 second, doubling each retry, capped at 60 seconds; each retry logs WARN with attempt count and last error

- **Given** a Redis error that persists across all retries until the backoff cap is reached 3 consecutive times,
  **When** the Bus Manager concludes the error is persistent,
  **Then** it calls `os.kill(os.getpid(), signal.SIGTERM)` to initiate controlled process exit; logs CRITICAL before the kill with the failure reason

- **Given** events delivered to a strategy queue,
  **When** delivered via `loop.run_coroutine_threadsafe(queue.put(event), loop)`,
  **Then** delivery uses the strategy's own asyncio loop reference from `StrategyHandle`; no event is delivered synchronously from the Bus Manager thread into the strategy loop

- **Given** `BOT_CONSUMER_GROUP` env var,
  **When** `XREADGROUP` is issued,
  **Then** the group name matches `BOT_CONSUMER_GROUP` exactly; consumer name is `{hostname}-{pid}` to prevent consumer ID collisions on multi-replica deployments

- **Given** a strategy whose `register()` call has completed and `BusManager.start()` has been called,
  **When** any code attempts to call `register()` again for the same or a different strategy,
  **Then** `BusManager` raises `RuntimeError` ("cannot register after start"); no mid-run re-subscription path exists; the routing table is immutable for the process lifetime (BS-FR5)

**Test coverage (l2):** Integration test with real Redis — register 2 strategies, publish 10 events, assert all 10 delivered; flood a queue to max depth, assert oldest dropped and GapMarker appended; simulate Redis timeout, assert backoff wait observed

---

### Story 11.5: MultiBarClose Barrier

**As** mrqdt,
**I want** a Barrier that collects per-symbol BarClose events for a given timeframe and delivers a single MultiBarClose when all expected symbols arrive (or times out),
**so that** strategies receive consistent multi-symbol snapshots and missing symbols are explicitly marked as gaps.

**Acceptance Criteria:**

- **Given** `bot_service/barrier.py` with `Barrier(symbols: frozenset[str], tf: str, timeout_ms: int)`,
  **When** a BarClose for timestamp T arrives for the last expected symbol,
  **Then** the Barrier emits a `MultiBarClose` with all symbols' closes for that timestamp immediately, without waiting for timeout

- **Given** a Barrier waiting for timestamp T,
  **When** the per-timeframe timeout elapses before all symbols arrive,
  **Then** the Barrier emits a `MultiBarClose` containing only the symbols that did arrive, plus a `GapMarker` for each missing symbol; `bot_barrier_timeout_total{tf}` is incremented

- **Given** a BarClose for timestamp T arriving AFTER the Barrier for T has already fired,
  **When** the late event is received,
  **Then** the Barrier returns `None` for that event, increments `bot_barrier_late_event_total{strategy, symbol}`, and logs WARN; the late event is NOT included in any future MultiBarClose

- **Given** timestamp alignment,
  **When** the Barrier computes which bucket a BarClose belongs to,
  **Then** it uses floor-ts alignment: `ts // tf_ms * tf_ms`; two BarClose events with the same floored timestamp are treated as the same bucket regardless of sub-timeframe jitter

- **Given** `BOT_BARRIER_TIMEOUT_MS_{TF}` env vars (1S=250, 1M=500, 5M=1000, 15M=2000, 1H=5000, 4H=10000, 1D=30000, 1W=60000),
  **When** the Barrier is constructed for timeframe `tf`,
  **Then** it reads the corresponding env var for its timeout; missing env var causes startup error (not silent default)

**Test coverage (l1):** Unit tests — all symbols arrive before timeout → MultiBarClose immediately; one symbol arrives late → partial MultiBarClose + GapMarker after timeout; late event after barrier fired → None + counter incremented

---

### Story 11.6: BaseStrategy — Signal Computation Foundation

**As** mrqdt,
**I want** an abstract BaseStrategy that enforces NaN guards, gap invalidation, lookback gating, and heartbeat liveness,
**so that** concrete strategy subclasses cannot accidentally bypass safety mechanisms.

**Acceptance Criteria:**

- **Given** `bot_service/strategy/base.py` with `BaseStrategy(ABC)`,
  **When** inspected,
  **Then** it declares abstract class attributes: `min_lookback: int`, `max_position_pct: float`, `stop_loss_pct: float`, `paper_trading: bool`; `max_position_pct` and `stop_loss_pct` are stored on the instance and accessible to the order queue worker in Epic 12 — enforcement (blocking OrderRequests that exceed the limit) is implemented there, not here; concrete subclasses must declare all four attributes or mypy --strict raises an error (BS-FR11)

- **Given** a concrete strategy subclass with fewer than `min_lookback` rows in its rolling DataFrame for symbol S,
  **When** a signal handler for S is called,
  **Then** the handler returns immediately without producing any signal or `OrderRequest`; no log message is emitted (high-frequency path); the gate applies from first bar receipt through cold-start warm-up until the DataFrame has accumulated at least `min_lookback` rows (BS-FR8)

- **Given** a concrete strategy subclass that registers a signal handler,
  **When** the handler is invoked with a DataFrame containing NaN values in any input column,
  **Then** the NaN guard (applied as a decorator on handler registration, not overridable by subclasses) intercepts before the signal function is called; no `OrderRequest` is produced; `bot_nan_guard_total{strategy, symbol}` is incremented; calling `fillna()` inside the signal function is prohibited and caught in code review (not enforced at runtime)

- **Given** a `GapMarker` event for symbol S delivered to a strategy,
  **When** handled,
  **Then** `_signal_invalid[S]` is set to `True` AND the corresponding bar row in the rolling DataFrame has `has_gap=True` set; signal production for S is suppressed until `min_lookback` consecutive clean (non-gap) bars have accumulated for S

- **Given** `_signal_invalid[S] = True` and N clean bars subsequently received for S,
  **When** the N-th bar is processed,
  **Then** if N < `min_lookback`, `_signal_invalid[S]` remains `True`; only when N >= `min_lookback` is `_signal_invalid[S]` cleared; the count resets to zero on any new GapMarker for S

- **Given** `subscribe()` called on a strategy,
  **When** it does not complete within `BOT_SUBSCRIBE_TIMEOUT_S` seconds,
  **Then** a `SubscribeTimeoutError` is raised; the strategy is not loaded; other strategies continue loading unaffected

- **Given** `get_history()` called when QuestDB is unreachable,
  **When** the call fails,
  **Then** an empty DataFrame (correct schema, zero rows) is returned immediately; WARN is logged; a background retry runs every 30 seconds until QuestDB responds and history is loaded; the background retry stops after first success

- **Given** history loaded from QuestDB with gap fraction > 5% of `min_lookback`,
  **When** the cold-start DataFrame is validated,
  **Then** WARN is logged with gap details; `bot_coldstart_gap_fraction{strategy, symbol, tf}` gauge is set to the gap fraction (0.0–1.0); the DataFrame is NOT rejected

- **Given** a strategy's asyncio event loop,
  **When** the loop freezes (not crashed — thread still alive but loop not processing),
  **Then** the heartbeat OS thread (separate from the strategy thread; posts `threading.Event` every 5s; expects it set within 10s) detects the freeze and calls `os.kill(os.getpid(), signal.SIGTERM)`; this mechanism is separate from `thread.is_alive()` crash detection

**Test coverage (l1/l2):**
- l1: NaN guard — DataFrame with leading NaN rows → zero signal calls
- l1: GapMarker → invalid; N-1 clean bars → still invalid; N-th clean bar → valid
- l2: Heartbeat thread — mock frozen loop → verify SIGTERM issued within 15s of freeze
- l2: get_history failure → empty DataFrame returned; background retry eventually populates

---

### Story 11.7: FastAPI Service Entry Point & Startup Sequence

**As** mrqdt,
**I want** a FastAPI entry point that initialises the service in the correct order, exposes `/health` and `/version`, and shuts down cleanly on SIGTERM,
**so that** the service starts deterministically and Docker Compose can health-check and gracefully stop it.

**Acceptance Criteria:**

- **Given** `bot_service/main.py`,
  **When** the service starts,
  **Then** the startup sequence is strictly: (1) configure structlog with credential redaction processor, (2) load `Settings` via pydantic-settings (exit non-zero on validation error), (3) call `apply_schema()` (exit non-zero if QuestDB unreachable), (4) start `BusManager`, (5) create FastAPI app and start uvicorn; Bus Manager must not start before schema is applied

- **Given** `GET /health`,
  **When** called while the service is running normally,
  **Then** it returns `200 OK` within 100ms; response body is `{"status": "ok", "bus_manager": "running"}`; the check is in-memory only (no Redis or QuestDB calls)

- **Given** `GET /health`,
  **When** the Bus Manager thread is dead (not alive),
  **Then** it returns `200 OK` with body `{"status": "degraded", "bus_manager": "dead"}`; HTTP 200 is intentional — the health endpoint reports state, not service viability (liveness probe handles restarts)

- **Given** `GET /version`,
  **When** called,
  **Then** it returns `{"version": <BUILD_VERSION env var>, "commit": <GIT_COMMIT env var>}`; if env vars are absent, values are `"unknown"`

- **Given** SIGTERM received by the process,
  **When** the shutdown handler fires,
  **Then** it: (1) sets the global shutdown event, (2) signals Bus Manager to stop accepting new events, (3) signals all strategy asyncio loops (via `run_coroutine_threadsafe`) to flush their own QuestDB ILP Senders from within their own loops, (4) waits up to `BOT_SHUTDOWN_TIMEOUT_S` for in-flight order workers to settle, (5) exits 0; orders still pending after timeout are logged WARN as "shutdown-interrupted"

- **Given** SIGTERM shutdown and a strategy's QuestDB ILP Sender flush,
  **When** the flush is requested,
  **Then** the main SIGTERM handler calls the flush via `run_coroutine_threadsafe` into the strategy's loop — NOT directly from the main thread (ILP Sender is not thread-safe; each Sender is owned exclusively by its strategy loop)

- **Given** `docker-compose.yml` for the bot service,
  **When** inspected,
  **Then** `stop_grace_period: 45s` is set (BOT_SHUTDOWN_TIMEOUT_S 30s + 15s for ILP Sender flushes); `depends_on: questdb: condition: service_healthy`

**Test coverage (l1/l2):**
- l1: `/health` returns 200 + correct body when Bus Manager alive; returns degraded body when Bus Manager thread dead
- l1: `/version` returns env var values; returns "unknown" when vars absent
- l2: Integration — start service, send SIGTERM, verify exit 0 within 50s; verify in-flight order worker log message appears

---

## Epic 12 Stories

---

### Story 12.1: Exchange Auth & httpx REST Client

**As** mrqdt,
**I want** typed, signed REST clients for KuCoin and Bybit that never expose credentials in logs or errors,
**so that** order placement and REST poll fallback have a safe, testable HTTP layer.

**Acceptance Criteria:**

- **Given** `bot_service/exchange/kucoin/rest.py` with `KuCoinRESTClient`,
  **When** a signed request is made,
  **Then** the `KC-API-SIGN` header is HMAC-SHA256 of `{timestamp}{method}{path}{body}` using `kucoin_api_secret`; `KC-API-KEY`, `KC-API-TIMESTAMP`, `KC-API-PASSPHRASE`, and `KC-API-KEY-VERSION: 2` headers are set; the raw secret value is obtained via `SecretStr.get_secret_value()` at sign-time only and never stored in a variable that outlives the function call

- **Given** `bot_service/exchange/bybit/rest.py` with `BybitRESTClient`,
  **When** a signed request is made,
  **Then** the signature is HMAC-SHA256 of `{timestamp}{api_key}{recv_window}{querystring_or_body}`; `X-BAPI-API-KEY`, `X-BAPI-TIMESTAMP`, `X-BAPI-RECV-WINDOW`, and `X-BAPI-SIGN` headers are set

- **Given** any REST response with HTTP 5xx status,
  **When** the call fails,
  **Then** the client retries up to 3 times with exponential backoff (1s, 2s, 4s); after 3 failures raises `ExchangeRESTError` with status code and truncated body (no credentials); 4xx responses raise `ExchangeRESTError` immediately without retry (client errors are not transient)

- **Given** an `ExchangeRESTError` raised or logged at any level,
  **When** the message is inspected,
  **Then** it contains no substring matching `KUCOIN_API_KEY`, `KUCOIN_API_SECRET`, `KUCOIN_API_PASSPHRASE`, `BYBIT_API_KEY`, `BYBIT_API_SECRET`, or any value of those variables

- **Given** `async with httpx.AsyncClient() as client:` inside the request method,
  **When** a request is issued,
  **Then** the client is created fresh per request (not shared across calls) to avoid session state leakage; timeout is 10s connect + 30s read, configurable via `BOT_EXCHANGE_REQUEST_TIMEOUT_S`

**Test coverage (l1):** Unit tests — KuCoin signature computed correctly against known test vector; Bybit signature computed correctly; 5xx retries 3 times then raises; 4xx raises immediately; no secret in error message

---

### Story 12.2: Private WebSocket Fill Feed & REST Poll Fallback

**As** mrqdt,
**I want** per-exchange private WebSocket feeds that deliver fill events with automatic REST fallback when the feed goes silent,
**so that** fill acknowledgement is near-real-time in normal operation and resilient to WebSocket silent drops.

**Acceptance Criteria:**

- **Given** `bot_service/exchange/{exchange}/ws_private.py`,
  **When** connected,
  **Then** it subscribes to the exchange's private fill/order channel using the auth mechanism from Story 12.1; reconnects automatically on disconnection with exponential backoff (1s/×2/max 30s)

- **Given** the private WebSocket feed,
  **When** liveness is assessed,
  **Then** liveness is determined by `time.monotonic() - last_message_received_ts`, NOT by the library's connection state; a connected socket that has sent no messages is treated as dead after `BOT_WS_FALLBACK_TIMEOUT_LIVE` seconds (live mode) or `BOT_WS_FALLBACK_TIMEOUT_PAPER` seconds (paper mode)

- **Given** the silence timeout elapsed,
  **When** REST poll fallback activates,
  **Then** `bot_ws_fallback_active{exchange}` gauge is set to 1; REST open-orders endpoint is polled every 2 seconds; on first WebSocket message received, fallback deactivates and gauge returns to 0

- **Given** a fill event arriving from both WebSocket and REST poll for the same `order_id`,
  **When** the second event is processed,
  **Then** it is discarded via `_seen_fill_ids: set[str]`; `bot_fill_dedup_total{exchange}` is incremented; no duplicate QuestDB write occurs

- **Given** REST poll returning fills out of chronological order,
  **When** applying them,
  **Then** fills are sorted by `ts_exchange` ascending before being applied; partial-fill accounting is always applied in time order

**Test coverage (l1/l2):**
- l1: Last-message-received silence detection — assert fallback activates after timeout with no messages
- l1: Fill deduplication — same order_id from WS and REST → only one processed
- l2: Integration with real exchange mock — WS goes silent, REST poll activates, WS resumes, poll stops

---

### Story 12.3: Order Queue Worker & Three-Layer State

**As** mrqdt,
**I want** a per-strategy asyncio order queue and worker that enforces deduplication, persists every outcome to QuestDB, and supports crash recovery,
**so that** no order is silently lost and no duplicate order is placed after a restart.

**Acceptance Criteria:**

- **Given** `bot_service/strategy/order_worker.py` with `OrderQueueWorker`,
  **When** an `OrderRequest` is posted to the queue,
  **Then** the worker pops it, checks `self.open_orders` for an existing entry order with the same `(symbol, side, order_role='entry')`, and if one exists discards the new request and increments `bot_order_queue_dedup_total{strategy, symbol}`; exit and stop orders are NEVER deduplicated regardless of existing state

- **Given** an `OrderRequest` that passes deduplication,
  **When** the worker calls the exchange REST client,
  **Then** on success: (1) updates `self.open_orders` in-memory, (2) writes `order_events` row with `status='placed'` via QuestDB ILP; on rejection/failure: writes `order_events` row with `status='rejected'` or `status='failed'`; every outcome is persisted — no silent discard

- **Given** the risk gate from Story 11.6 (`max_position_pct`, `stop_loss_pct`),
  **When** an `OrderRequest` would result in a position exceeding `max_position_pct` of portfolio value,
  **Then** the worker discards the request, logs WARN with strategy name and computed position size, increments `bot_risk_gate_block_total{strategy, symbol}`; this is the enforcement point for BS-FR11

- **Given** a fill event arriving from the private WebSocket or REST poll,
  **When** it matches an `order_id` in `self.open_orders`,
  **Then** the worker updates `self.open_orders`, updates in-memory position state, writes `order_events` row with `status='filled'` or `status='partially_filled'`; QuestDB ILP write is fire-and-forget (failure logged but does not halt processing)

- **Given** the process crashes after writing `status='placed'` to QuestDB but before `self.open_orders` is updated,
  **When** the service restarts and reconciliation runs (Story 13.1),
  **Then** the reconciled state correctly populates `self.open_orders` from QuestDB non-terminal orders; no duplicate order is placed for the same `order_id`

**Test coverage (l1/l2):**
- l1: Dedup — two entry OrderRequests for same (symbol, side, order_role) → one placed, one discarded + counter
- l1: Risk gate — OrderRequest exceeding max_position_pct → discarded + counter
- l1: All outcomes write QuestDB row (mock ILP sender)
- l2: Crash-recovery — place order → crash → restart → reconciliation → verify no duplicate placed

---

### Story 12.4: Orphaned Order Detection & order_alerts

**As** mrqdt,
**I want** orphaned exchange orders (present on exchange, absent from QuestDB) detected and written to order_alerts without auto-cancellation,
**so that** no exchange position is silently ignored and the operator is always notified.

**Acceptance Criteria:**

- **Given** startup reconciliation (Story 13.1) cross-referencing exchange open orders against QuestDB `order_events`,
  **When** an order is found on the exchange with no matching `order_id` in QuestDB,
  **Then** it is written to the `order_alerts` table with `alert_type='orphaned_order'` and a message containing the exchange, symbol, order_id, and current quantity; `bot_orphaned_order_total{exchange}` is incremented; the order is NEVER auto-cancelled

- **Given** an orphaned order written to `order_alerts`,
  **When** the operator queries Grafana,
  **Then** the alert is visible in a query against `order_alerts` filtered by `alert_type='orphaned_order'`; the runbook entry for orphaned orders explains the manual resolution steps

- **Given** the same orphaned order appearing across multiple restarts (not yet resolved by operator),
  **When** reconciliation runs again,
  **Then** a new `order_alerts` row is written each time; deduplication is an operator concern, not a service concern — the service must not silently suppress repeated alerts for the same order

**Test coverage (l1):** Unit test — mock reconciliation finding an order on exchange absent from QuestDB → order_alerts row written, counter incremented, no cancel REST call made

---

### Story 12.5: PaperExchangeClient — Simulated Fill Engine

**As** mrqdt,
**I want** a paper trading client that simulates fills using live tick data and writes outcomes to the same order_events table as live trading,
**so that** paper and live results are queryable together in Grafana with a single filter.

**Acceptance Criteria:**

- **Given** a strategy class with `paper_trading: bool = True`,
  **When** the order queue worker is instantiated for that strategy,
  **Then** it uses `PaperExchangeClient` instead of the live `KuCoinRESTClient`/`BybitRESTClient`; the swap is transparent — `OrderQueueWorker` accepts an `ExchangeClient` protocol, not a concrete class

- **Given** a limit order submitted to `PaperExchangeClient`,
  **When** simulating a fill,
  **Then** the client waits a random duration drawn from a uniform distribution between `BOT_PAPER_LATENCY_MIN_MS` and `BOT_PAPER_LATENCY_MAX_MS` (defaults: 50ms, 250ms); during this window it checks the live tick stream for the symbol to determine whether the limit price was crossed; if crossed, the order fills at the limit price

- **Given** a limit order whose simulated latency window elapses with no ticks available for the symbol (empty Redis stream),
  **When** the fill is resolved,
  **Then** the order fills at the limit price; WARN is logged with `paper_fill_no_ticks {strategy} {symbol}`; the order is NEVER rejected due to missing tick data

- **Given** a market order submitted to `PaperExchangeClient`,
  **When** simulating a fill,
  **Then** the order fills at mid-price + configured slippage (`BOT_PAPER_SLIPPAGE_BPS`, default 5); if no mid-price is available, fills at the last-known price and logs WARN

- **Given** any order processed by `PaperExchangeClient`,
  **When** the `order_events` QuestDB row is written,
  **Then** `paper_trading=true` is set; `backtest=false`; the row is identical in schema to a live-mode row and filterable with `WHERE paper_trading = true` in Grafana

**Test coverage (l1/l2):**
- l1: Limit order fills when tick crosses price; does not fill when tick does not cross
- l1: No ticks available → fills at limit price + WARN logged
- l1: Market order fills at mid-price + slippage bps
- l2: paper_trading=true row written to real QuestDB; queryable with paper_trading filter

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

## Epic 13 Stories

---

### Story 13.1: Startup Reconciliation — Exchange REST vs QuestDB

**As** mrqdt,
**I want** the service to reconcile open positions and non-terminal orders against exchange REST before any strategy starts,
**so that** no position is silently lost after a crash and strategies always start from accurate state.

**Acceptance Criteria:**

- **Given** the service starting up,
  **When** reconciliation runs,
  **Then** the sequence is strictly: (1) query exchange REST for all open positions and non-terminal orders for all configured symbols, (2) cross-reference against QuestDB `order_events` WHERE status NOT IN ('filled','cancelled','rejected','failed'), (3) populate `self.open_orders` and position state for all strategies, (4) THEN start the file watcher and spawn strategy threads; no strategy event loop may receive events before step 3 completes or the timeout is reached

- **Given** positions found in reconciliation for symbols not in the current strategy's `subscribe()` selections,
  **When** force-subscription runs,
  **Then** those symbols are added to `_managed_positions` and events for them are routed to the strategy regardless of the `subscribe()` selection; strategies cannot filter out `_managed_positions` symbols (BS-FR22)

- **Given** reconciliation does not complete within `BOT_RECONCILIATION_TIMEOUT_S` (120s),
  **When** the timeout elapses,
  **Then** the service logs CRITICAL with the failure reason; uses QuestDB-derived state only for `self.open_orders` and position tracking; marks all recovered positions as `unconfirmed`; continues starting strategies with best-available state; starts a background retry every 60 seconds until exchange confirmation is received

- **Given** a process crash that occurred after `status='placed'` was written to QuestDB but before `self.open_orders` was updated in-memory,
  **When** reconciliation runs on next startup,
  **Then** the `placed` order is found in QuestDB non-terminal orders, cross-referenced against exchange, and `self.open_orders` is populated correctly; no duplicate order is placed

- **Given** cross-strategy position tracking (BS-FR23),
  **When** two independent strategies both hold BTCUSDT long positions,
  **Then** each strategy's `self.open_orders` tracks only its own orders; there is no shared position ledger across strategies; combined exposure is accepted as an architectural constraint and documented but not enforced

**Test coverage (l1/l2):**
- l1: Reconciliation populates open_orders from QuestDB non-terminal rows when exchange REST is mocked
- l1: Force-subscribe — strategy subscribed to ETH only, BTC open position exists → BTC events routed
- l2: Integration — crash after placed write → restart → reconciliation → no duplicate order

---

### Story 13.2: File Watcher & Strategy Auto-Load

**As** mrqdt,
**I want** strategies to be auto-loaded from `strategies/active/` and stopped when moved out, without manual process management,
**so that** deploying a new strategy requires only dropping a `.py` file into the directory.

**Acceptance Criteria:**

- **Given** a `.py` file placed in `strategies/active/`,
  **When** the file watcher detects it (scan interval: `BOT_FILEWATCHER_INTERVAL_S`, default 60s),
  **Then** it imports the file via `importlib` in an isolated try/except; if import succeeds and the class name is unique across all loaded strategies, spawns a strategy thread; if import raises `ImportError` or `SyntaxError`, logs ERROR with filename and exception, skips the file, continues loading other files

- **Given** two `.py` files in `strategies/active/` that define a class with the same name,
  **When** the file watcher scans,
  **Then** BOTH files are rejected; `bot_strategy_load_failure_total` is incremented twice; neither class is loaded; uniqueness is checked across all files at each scan cycle, not only on newly added files

- **Given** a `.py` file moved out of `strategies/active/` (or deleted),
  **When** the file watcher detects the removal,
  **Then** it sends a stop signal to the corresponding strategy thread and waits up to `BOT_SHUTDOWN_TIMEOUT_S` for clean exit; logs INFO with strategy name and stop reason

- **Given** a `.py` file in `strategies/active/` that is updated in-place (same filename, new content),
  **When** the file watcher detects the modification,
  **Then** the hot-reload sequence runs strictly: (1) send stop signal to existing thread and wait up to `BOT_SHUTDOWN_TIMEOUT_S`, (2) call `importlib.invalidate_caches()` and remove the module from `sys.modules`, (3) import the new module, (4) spawn a new thread; if the existing thread does not stop within the timeout, it is force-stopped via `thread._stop()` (last resort) and CRITICAL is logged before proceeding

- **Given** each strategy file import,
  **When** imported via `importlib`,
  **Then** it is wrapped in its own try/except; an error in one file does NOT prevent other files from loading in the same scan cycle

**Test coverage (l1/l2):**
- l1: Valid file → class loaded and thread spawned; invalid syntax → ERROR logged, no thread; duplicate class name → both rejected
- l2: Hot-reload — update file in-place → old thread stopped, new class loaded with updated logic

---

### Story 13.3: Watchdog & Exponential Backoff Restart

**As** mrqdt,
**I want** a watchdog that detects crashed strategy threads and restarts them with exponential backoff,
**so that** a single strategy crash is self-healing without operator intervention.

**Acceptance Criteria:**

- **Given** the watchdog monitoring all registered strategy threads,
  **When** `thread.is_alive()` returns `False` for a strategy thread,
  **Then** the watchdog detects the crash (crash detection is separate from freeze detection — see heartbeat in Story 11.6); logs ERROR with strategy name, restart count, and last exception if captured; resets Prometheus gauges for that strategy to sentinel values (0 for sizes, 0 for P&L) synchronously before the backoff sleep to prevent stale non-zero metrics appearing as active positions

- **Given** a crashed strategy thread,
  **When** the watchdog restarts it,
  **Then** the restart backoff is keyed by strategy NAME (the class name string), not by thread object; backoff sequence: 5s → 10s → 30s → 60s → 60s (cap); restart count and `last_restart_ts` persist in the watchdog registry for the process lifetime; a new thread inherits the existing backoff state

- **Given** a strategy that restarts after a crash,
  **When** the new thread starts,
  **Then** reconciliation (Story 13.1) runs for that strategy's positions before the new thread's event loop starts accepting events; `self.open_orders` is repopulated from exchange REST before any signal handler can run

- **Given** `bot_strategy_restart_total{strategy}` counter and `bot_strategy_backoff_seconds{strategy}` gauge,
  **When** a restart occurs,
  **Then** the counter is incremented and the gauge is set to the current backoff duration before sleeping

**Test coverage (l1):** Unit tests — thread.is_alive() returns False → ERROR logged, backoff state updated, restart scheduled; backoff sequence 5→10→30→60→60 after consecutive crashes; gauge reset before backoff sleep

---

### Story 13.4: Heartbeat Emergency Close

**As** mrqdt,
**I want** a per-strategy configurable heartbeat that can emergency-close all open positions if the event bus goes silent,
**so that** microstructure strategies are never left holding positions when their signal feed dies.

**Acceptance Criteria:**

- **Given** a strategy class with `bus_timeout_seconds: int` and `close_on_bus_timeout: bool` class attributes,
  **When** no BarClose or relevant event is received for `bus_timeout_seconds` seconds,
  **Then** the heartbeat fires: always logs WARN with strategy name, position summary, and seconds since last event; always increments `bot_heartbeat_timeout_total{strategy}`; if `close_on_bus_timeout=True`, issues market-sell orders for all open positions via direct exchange REST (NOT via the asyncio order queue); if `close_on_bus_timeout=False`, logs and increments only

- **Given** `close_on_bus_timeout=True` and the emergency close path,
  **When** market-sell orders are issued,
  **Then** they are sent directly via the exchange REST client (Story 12.1) from the heartbeat OS thread — NOT via the Redis-backed order queue — so the emergency close works even if the event loop is frozen or the Redis connection is down

- **Given** an emergency close REST call that fails,
  **When** the error is raised,
  **Then** it is logged CRITICAL with the symbol, side, quantity, and error; the heartbeat retries every 5 seconds until filled or the process exits; the failure does NOT suppress further close attempts for other open positions

- **Given** `close_on_bus_timeout=True` (default for microstructure strategies) vs `False` (default for funding rate arb),
  **When** a concrete strategy subclass omits both attributes,
  **Then** mypy --strict raises a missing-attribute error; the defaults are enforced at the BaseStrategy abstract class level

**Test coverage (l1):** Unit tests — bus_timeout_seconds elapsed + close_on_bus_timeout=True → REST market-sell called (mocked), counter incremented; bus_timeout_seconds elapsed + close_on_bus_timeout=False → only log + counter, no REST call

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

## Epic 14 Stories

---

### Story 14.1: Shared Signal Layer — Pure Functions in strategy/signals/

**As** mrqdt,
**I want** all signal logic extracted into pure functions with no framework dependency,
**so that** the same function is called identically by live BaseStrategy handlers and Backtrader's `next()` — written once, tested once.

**Acceptance Criteria:**

- **Given** `bot_service/strategy/signals/` directory,
  **When** any `.py` file in it is inspected,
  **Then** it contains no imports from `backtrader`, `bot_service.strategy.base`, `bot_service.bus_manager`, or any other framework module; functions accept pandas `DataFrame` and scalar parameters only and return typed signal objects (`SignalResult` dataclass)

- **Given** `SignalResult: @dataclass(frozen=True)` with fields `action: Literal['buy','sell','hold']`, `confidence: float`, `reason: str`,
  **When** a signal function is called,
  **Then** it always returns a `SignalResult`; it never raises; if inputs are insufficient it returns `SignalResult(action='hold', confidence=0.0, reason='insufficient_data')`

- **Given** a live `BaseStrategy` handler and a Backtrader `bt.Strategy.next()` calling the same signal function,
  **When** both are passed identical DataFrames,
  **Then** both receive identical `SignalResult` outputs; there is no conditional import or runtime branching on live-vs-backtest context within the signal function

- **Given** mypy --strict run on `strategy/signals/`,
  **When** executed,
  **Then** zero errors; all function signatures are fully annotated including return types

**Test coverage (l1):** Unit tests per signal function — known DataFrame input produces expected SignalResult; NaN-heavy DataFrame returns hold with reason 'insufficient_data'; function is importable with no framework side-effects

---

### Story 14.2: QuestDBFeed — 67-Feature Backtrader Data Feed

**As** mrqdt,
**I want** a Backtrader data feed that queries QuestDB `snapshot_1s`, filters gap-contaminated bars, and exposes all 67 microstructure features,
**so that** backtests use the same data as live signal computation with explicit gap handling.

**Acceptance Criteria:**

- **Given** `bot_service/backtest/feeds.py` with `QuestDBFeed(bt.feeds.PandasData)`,
  **When** instantiated with `exchange`, `symbol`, `start`, `end` parameters,
  **Then** it queries QuestDB `snapshot_1s` via HTTP REST (`POST /exec`) for the given exchange/symbol/time range; returns a DataFrame sorted by `ts_second` ascending

- **Given** rows in the query result where `has_gap = true`,
  **When** the feed processes them,
  **Then** each gap row is replaced with a NaN/NaT placeholder row (all numeric fields set to `float('nan')`, `ts_second` set to `pd.NaT`); the placeholder row is inserted at the correct timestamp position so the index remains contiguous; Backtrader's `next()` can detect the gap by checking `math.isnan(self.data.close[0])`

- **Given** the 67 microstructure fields from `snapshot_1s`,
  **When** the feed is loaded into Backtrader,
  **Then** each field is declared as a `bt.feeds.PandasData` line; all 67 lines are accessible in strategy `next()` as `self.data.{field_name}[0]`; the OHLCV standard lines are mapped to the candle OHLCV columns

- **Given** a QuestDB query that returns zero rows for the requested time range,
  **When** the feed is used,
  **Then** it raises `InsufficientHistoryError` with exchange, symbol, start, and end; the backtest run does not silently proceed with an empty dataset

**Test coverage (l1/l2):**
- l1: Gap rows replaced with NaN/NaT; index remains contiguous
- l1: Zero rows → InsufficientHistoryError raised
- l2: Integration with real QuestDB — all 67 fields accessible as Backtrader lines

---

### Story 14.3: Custom CommissionInfo — Exchange Fee Models

**As** mrqdt,
**I want** exact KuCoin and Bybit fee models in Backtrader,
**so that** backtests accurately account for trading costs and the fee impact gate has precise inputs.

**Acceptance Criteria:**

- **Given** `bot_service/backtest/commission.py` with `KuCoinCommissionInfo(bt.CommissionInfo)` and `BybitCommissionInfo(bt.CommissionInfo)`,
  **When** a trade is simulated,
  **Then** KuCoin applies: maker fee `0.001` (0.1%) for limit orders that rest on book, taker fee `0.001` (0.1%) for market orders or limit orders that cross spread; Bybit applies: maker fee `-0.0001` (−0.01% rebate) for post-only limit orders, taker fee `0.0006` (0.06%) for market orders; fee rates are configurable via constructor parameters, not hardcoded, so they can be updated without code changes

- **Given** a perpetual futures strategy using `BybitCommissionInfo`,
  **When** a position is held across a funding interval,
  **Then** funding rate cost is applied: `position_size × mark_price × funding_rate` at each funding event; funding rate data is provided as a time-indexed Series passed to the commission constructor; if funding rate data is unavailable for a timestamp the cost is zero and a WARN is logged

- **Given** `bot_service/backtest/fee_impact.py` with `fee_impact_gate(strategy_signals, commission_info, expected_slippage_bps) -> FeeImpactReport`,
  **When** called,
  **Then** it computes `required_edge = 2 × (fee_rate + slippage_bps / 10000)` per round-trip; returns `FeeImpactReport` with `passes: bool`, `required_edge: float`, `mean_signal_edge: float`, `margin: float`; `passes=True` only when `mean_signal_edge > required_edge`

**Test coverage (l1):** Unit tests — KuCoin maker/taker fee computed correctly on known trade; Bybit maker rebate and taker fee computed correctly; funding rate cost applied at correct timestamps; fee_impact_gate returns pass/fail correctly

---

### Story 14.4: Walk-Forward, Stress Test & Monte Carlo Harnesses

**As** mrqdt,
**I want** automated validation harnesses with structured go/no-go reports,
**so that** no strategy is paper-deployed without passing quantitative validation gates.

**Acceptance Criteria:**

- **Given** `bot_service/backtest/validation.py` with `run_walk_forward(strategy_cls, feed, n_splits, ...)`,
  **When** executed,
  **Then** it partitions the dataset into `n_splits` in-sample / out-of-sample folds; runs Backtrader on each fold; computes Sharpe ratio, max drawdown, and P&L degradation (out-of-sample P&L as fraction of in-sample P&L) for each fold; returns a `WalkForwardReport` dataclass

- **Given** `run_stress_test(strategy_cls, feed)`,
  **When** executed,
  **Then** it runs the strategy on three named stress windows: LUNA collapse (`2022-05-05` to `2022-05-15`), FTX collapse (`2022-11-07` to `2022-11-14`), and a configurable flash crash window; returns a `StressTestReport` dataclass with per-window Sharpe and drawdown

- **Given** `run_monte_carlo(trade_results, n_shuffles=10_000)`,
  **When** executed,
  **Then** it shuffles the `TradeAnalyzer` trade result sequence `n_shuffles` times, computing P&L for each shuffle; returns the 5th-percentile P&L across all shuffles

- **Given** `generate_validation_report(walk_forward, stress, monte_carlo) -> ValidationReport`,
  **When** executed,
  **Then** it produces a `ValidationReport` with `passes: bool` that is `True` only when ALL of the following hold on the out-of-sample window: Sharpe >= 1.0, max drawdown <= 0.15, P&L degradation <= 0.30 (out-of-sample P&L >= 70% of in-sample), Monte Carlo 5th-percentile P&L > 0; the report is serializable to JSON via `report.to_json()`; thresholds are constructor parameters with the above as defaults — tightening is allowed, loosening is not enforced at runtime but documented as prohibited

- **Given** the fee impact gate (Story 14.3) and the validation report,
  **When** a strategy is being evaluated,
  **Then** fee impact analysis runs FIRST (cheapest computation); if it fails, walk-forward and stress tests are skipped; the validation report records which gates were run and their outcomes

**Test coverage (l1):** Unit tests with synthetic trade sequences — walk-forward correctly partitions folds; Monte Carlo 5th-percentile computed correctly on known distribution; ValidationReport.passes=False when any threshold fails; JSON serialization round-trips correctly

---

### Story 14.5: Backtest Results Persistence

**As** mrqdt,
**I want** backtest order events written to the same QuestDB table as live and paper results,
**so that** strategy performance across all modes is queryable in a single Grafana panel.

**Acceptance Criteria:**

- **Given** a Backtrader run completing with order fill events,
  **When** results are persisted,
  **Then** each order event is written to QuestDB `order_events` via ILP with `backtest=true`, `paper_trading=false`; all other columns (strategy, symbol, exchange, side, order_role, qty, fill_price, fee, ts_signal, ts_filled) are populated from the Backtrader `Order` and `TradeAnalyzer` outputs

- **Given** a Grafana query against `order_events`,
  **When** filtered with `WHERE backtest = true AND strategy = 'OFIBot'`,
  **Then** only backtest rows for OFIBot appear; live rows (`backtest=false, paper_trading=false`) and paper rows (`paper_trading=true`) are excluded

- **Given** a backtest run that fails mid-way (exception in Backtrader `next()`),
  **When** the exception propagates,
  **Then** any rows already written to `order_events` remain (no rollback); the failure is logged CRITICAL with the strategy name, timestamp of last successful bar, and exception; the partial run is identifiable in QuestDB by its `ts_signal` range

**Test coverage (l2):** Integration with real QuestDB — run a synthetic backtest, assert order_events rows written with correct backtest=true flag; verify Grafana filter returns only backtest rows

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

## Epic 15 Stories

---

### Story 15.1: Fee Impact Analysis Gate — Prerequisite for All Strategies

**As** mrqdt,
**I want** fee impact analysis run and passing before any strategy proceeds to backtesting or paper deployment,
**so that** no compute is wasted validating strategies with insufficient edge to cover costs.

**Acceptance Criteria:**

- **Given** a strategy's signal function in `strategy/signals/`,
  **When** `fee_impact_gate()` (Story 14.3) is run against a representative sample of its historical signals,
  **Then** the `FeeImpactReport` is written to `_results/{strategy_name}/fee_impact.json`; if `passes=False`, the story for that strategy is blocked and cannot proceed to walk-forward validation or paper deployment

- **Given** a strategy that fails the fee impact gate,
  **When** the failure is documented,
  **Then** the strategy `.py` file is placed in `strategies/inactive/` (not `strategies/active/`); a comment at the top of the file records the fee impact result and the date evaluated

- **Given** a `FeeImpactReport` that passes,
  **When** the next story (walk-forward validation) is started,
  **Then** it reads `fee_impact.json` as a prerequisite artifact; if the file is absent or `passes=False`, the walk-forward story acceptance criteria cannot be met

**Test coverage (l1):** Unit test — fee_impact_gate passes for strategy with large synthetic edge; fails for strategy with edge < 2x fee; report written to correct path

---

### Story 15.2: OFI Microstructure Bot — Signal Function & Paper Deployment

**As** mrqdt,
**I want** an Order Flow Imbalance signal strategy running in paper mode,
**so that** I can validate a microstructure edge on live data before committing capital.

**Prerequisites:** Story 15.1 fee impact gate must pass. Epic 14 walk-forward go/no-go JSON report must show all thresholds met (Sharpe >= 1.0, drawdown <= 15%, degradation <= 30%, Monte Carlo 5th-pct > 0).

**Acceptance Criteria:**

- **Given** `bot_service/strategy/signals/ofi.py` with `compute_ofi_signal(df: DataFrame, lookback: int, threshold: float) -> SignalResult`,
  **When** called with a DataFrame containing `ofi`, `ofi_l1`, `bid_order_arrivals`, `ask_order_arrivals` columns,
  **Then** it computes the OFI z-score over the lookback window; returns `buy` when z-score > threshold, `sell` when z-score < -threshold, `hold` otherwise; all computation is pure (no side effects, no IO)

- **Given** `bot_service/strategies/active/ofi_bot.py` with `OFIBot(BaseStrategy)`,
  **When** inspected,
  **Then** it declares `min_lookback: int = 200`, `max_position_pct: float = 0.05`, `stop_loss_pct: float = 0.02`, `paper_trading: bool = True`, `bus_timeout_seconds: int = 30`, `close_on_bus_timeout: bool = True`; subscribes to 1-second bars; calls `compute_ofi_signal` from `strategy/signals/ofi.py`

- **Given** `OFIBot` running in paper mode,
  **When** `compute_ofi_signal` returns a `buy` or `sell` action,
  **Then** an `OrderRequest` is posted to the order queue only after the NaN guard, lookback gate, gap invalidation gate, and risk gate all pass; `PaperExchangeClient` handles the fill; `order_events` rows have `paper_trading=true`

- **Given** `_results/OFIBot/fee_impact.json` and `_results/OFIBot/validation_report.json`,
  **When** read,
  **Then** both exist and show `passes=true`; these are acceptance criteria prerequisites — the story cannot be marked done without them

**Test coverage (l1/l2):**
- l1: `compute_ofi_signal` — z-score above threshold → buy; below → sell; within → hold; NaN inputs → hold with reason
- l2: `OFIBot` end-to-end in paper mode with mock Redis feed → order posted, paper fill written to real QuestDB

---

### Story 15.3: MA-Cross Baseline — Signal Function & Paper Deployment

**As** mrqdt,
**I want** a moving-average crossover baseline strategy in paper mode,
**so that** I have a benchmark to compare microstructure strategies against.

**Prerequisites:** Story 15.1 fee impact gate must pass for MA-cross. Epic 14 walk-forward validation must pass.

**Acceptance Criteria:**

- **Given** `bot_service/strategy/signals/ma_cross.py` with `compute_ma_cross_signal(df: DataFrame, fast: int, slow: int) -> SignalResult`,
  **When** called,
  **Then** it computes EMA-fast and EMA-slow over the close column; returns `buy` on fast-crosses-above-slow (golden cross), `sell` on fast-crosses-below-slow (death cross), `hold` otherwise; when `len(df) < slow`, returns `hold` with reason `insufficient_data`

- **Given** `bot_service/strategies/active/ma_cross_bot.py` with `MACrossBot(BaseStrategy)`,
  **When** inspected,
  **Then** it declares `min_lookback: int = 50`, `max_position_pct: float = 0.05`, `stop_loss_pct: float = 0.03`, `paper_trading: bool = True`, `bus_timeout_seconds: int = 300`, `close_on_bus_timeout: bool = False`; subscribes to 1-minute bars; calls `compute_ma_cross_signal` from `strategy/signals/ma_cross.py`

- **Given** `_results/MACrossBot/fee_impact.json` and `_results/MACrossBot/validation_report.json`,
  **When** read,
  **Then** both exist and show `passes=true`

**Test coverage (l1):** `compute_ma_cross_signal` — golden cross → buy; death cross → sell; insufficient data → hold; EMA computed correctly against known reference values

---

### Story 15.4: Funding Rate Arb — Signal Function (BLOCKED)

**As** mrqdt,
**I want** a funding rate arbitrage strategy that profits from positive funding by holding a spot long and perp short simultaneously,
**so that** I can capture funding payments as a low-directional-risk income stream.

**BLOCKED:** This story cannot start until the aggregator perpetuals feed epic is complete and `funding:{exchange}:{symbol}` Redis stream entries are verifiable in Redis. The aggregator currently does not produce funding rate, open interest, perp mark price, or basis streams (FR37-42 are planned but not implemented). Do not begin implementation until the prerequisite gate below is met.

**Prerequisite Gate:** `redis-cli XLEN funding:bybit:BTCUSDT` returns > 0 on the production Redis instance, confirming live funding rate events are flowing.

**Acceptance Criteria:**

- **Given** the prerequisite gate is met,
  **When** `bot_service/strategy/signals/funding_arb.py` is implemented with `compute_funding_arb_signal(funding_rate: float, next_funding_ts: int, threshold: float) -> SignalResult`,
  **Then** it returns `buy` (open arb) when `funding_rate > threshold` and `time.time() < next_funding_ts - entry_buffer_s`; returns `sell` (close arb) when position is open and `time.time() >= next_funding_ts - exit_buffer_s`; returns `hold` otherwise

- **Given** a `FundingRate` event with `next_funding_ts` older than 2× the funding interval (typically 16 hours for KuCoin/Bybit 8-hour funding),
  **When** processed,
  **Then** the event is rejected as stale; WARN is logged; `bot_funding_rate_stale_total{strategy}` is incremented; no signal is computed

- **Given** an open arb position (spot long + perp short) and an unwind signal,
  **When** the unwind executes,
  **Then** spot close leg is submitted first; if spot close fails, unwind is aborted and CRITICAL is logged with full position state; if spot closes successfully, perp close is submitted; if perp close fails, CRITICAL is logged and the perp close is retried every 5 seconds until filled or operator intervenes; a half-unwound position is NEVER left silently — the `order_alerts` table receives an entry and CRITICAL is logged continuously until resolved

- **Given** `_results/FundingArb/fee_impact.json` and `_results/FundingArb/validation_report.json`,
  **When** read,
  **Then** both exist and show `passes=true` (evaluated once aggregator feed is live)

**Test coverage (l1):** `compute_funding_arb_signal` — funding_rate above threshold + time before cutoff → buy; stale next_funding_ts → hold + counter; unwind sequencing logic (spot-first atomicity) tested with mocked REST clients

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

## Epic 16 Stories

---

### Story 16.1: Prometheus /metrics Endpoint

**As** mrqdt,
**I want** a `/metrics` endpoint exposing per-strategy P&L, positions, drawdown, lag, and execution latency,
**so that** all running strategies are visible in Grafana without querying QuestDB directly.

**Acceptance Criteria:**

- **Given** `GET /metrics`,
  **When** scraped by Prometheus,
  **Then** it returns all metrics in Prometheus text format; the endpoint responds within 200ms regardless of strategy thread state; it uses a `prometheus_client.CollectorRegistry()` instance (never `DefaultRegistry`) consistent with the rest of the system

- **Given** per-strategy Prometheus gauges,
  **When** a strategy is running with an open position,
  **Then** the following gauges are present and current: `bot_position_size{strategy, symbol}` (signed, negative = short), `bot_unrealized_pnl{strategy, symbol}` (USD), `bot_drawdown{strategy}` (fraction, 0.0–1.0), `bot_consumer_lag{strategy}` (number of unprocessed Redis stream entries)

- **Given** per-strategy Prometheus counters,
  **When** events occur,
  **Then** the following counters are present: `bot_order_placed_total{strategy, exchange, symbol, side}`, `bot_order_filled_total{strategy, exchange, symbol, side}`, `bot_order_rejected_total{strategy, exchange, symbol}`, `bot_queue_drop_total{strategy}`, `bot_nan_guard_total{strategy, symbol}`, `bot_heartbeat_timeout_total{strategy}`, `bot_strategy_restart_total{strategy}`, `bot_orphaned_order_total{exchange}`, `bot_ws_fallback_active{exchange}` (gauge, not counter)

- **Given** per-strategy Prometheus histograms,
  **When** orders are processed,
  **Then** `bot_order_execution_latency_ms{strategy, exchange}` histogram tracks time from `OrderRequest` posted to exchange REST response received; buckets: 10ms, 50ms, 100ms, 250ms, 500ms, 1s, 5s

- **Given** a strategy thread that has died,
  **When** `/metrics` is scraped,
  **Then** that strategy's `bot_position_size` and `bot_unrealized_pnl` gauges are 0 (reset by watchdog on crash detection); `bot_drawdown` is 0; `bot_consumer_lag` is 0; no stale non-zero position metrics appear for dead strategies

**Test coverage (l1/l2):**
- l1: All required metric names present in scraped output; dead strategy gauges are 0
- l2: Integration — place a paper trade, assert bot_order_placed_total incremented; assert bot_order_execution_latency_ms histogram populated

---

### Story 16.2: Hardened /health & Docker Compose Integration

**As** mrqdt,
**I want** a `/health` endpoint that reports per-strategy status and a Docker Compose configuration that starts the service safely,
**so that** Grafana can display strategy health at a glance and the service starts only when dependencies are ready.

**Acceptance Criteria:**

- **Given** `GET /health`,
  **When** called,
  **Then** the response body includes `{"status": "ok"|"degraded", "bus_manager": "running"|"dead", "strategies": {"OFIBot": "running"|"restarting"|"stopped", ...}}`; `status` is `degraded` if any strategy is not `running` or if Bus Manager is dead; the check is in-memory only, no Redis or QuestDB calls; response time < 100ms

- **Given** a strategy in exponential backoff restart (Story 13.3),
  **When** `/health` is called during the backoff sleep,
  **Then** that strategy appears as `"restarting"` with its restart count in the response body

- **Given** `docker-compose.yml` for the bot service,
  **When** inspected,
  **Then** it includes: `stop_grace_period: 45s`; `mem_limit: 2g`; `depends_on: questdb: condition: service_healthy`; `env_file: bot-service/.env` (credentials never in `environment:` block directly); `restart: unless-stopped`

- **Given** the QuestDB service in `docker-compose.yml`,
  **When** inspected,
  **Then** it has a healthcheck defined: `test: ["CMD", "curl", "-f", "http://localhost:9000/health"]`; `interval: 10s`; `retries: 3`; `start_period: 30s`; this ensures the bot service does not start before QuestDB is serving queries

- **Given** credentials in `bot-service/.env`,
  **When** the compose file is inspected,
  **Then** no credential values appear in the `environment:` block of any service; all secrets flow exclusively through `env_file:`

**Test coverage (l1):** Unit test — /health returns degraded when any strategy dict entry is not 'running'; /health returns ok when all strategies running and Bus Manager alive; response time assertion < 100ms on mocked state

---

### Story 16.3: Operations Runbook Entry

**As** mrqdt,
**I want** a runbook section for the bot service covering deploy, rollback, credential rotation, and alert playbooks,
**so that** I can operate the service without relying on memory or reverse-engineering logs.

**Acceptance Criteria:**

- **Given** `docs/ops.md`,
  **When** a new section `## Bot Service` is added,
  **Then** it covers the following subsections with concrete commands, not general advice:

  **Deploy**: `docker compose pull bot && docker compose up -d bot` — describes pre-deploy checklist (verify paper mode, check fee_impact.json and validation_report.json exist and pass for all strategies in `strategies/active/`)

  **Rollback**: tag-based rollback procedure; `docker compose down bot && docker compose up -d bot` with previous image tag; confirm `/health` returns ok within 60s

  **Credential rotation**: steps for rotating each of the 5 exchange credentials without downtime; update `.env`, restart bot service, verify `/health` ok; note that the old credential is revoked only AFTER the new one is confirmed working

  **Alert playbooks — one entry per alert**:
  - `bot_heartbeat_timeout_total` spiking → check strategy log for freeze cause; if `close_on_bus_timeout=True` verify positions were closed; escalate to manual close if not
  - `bot_strategy_restart_total` spiking → check strategy log for crash cause; move strategy to `strategies/inactive/` if crash is unrecoverable
  - `bot_orphaned_order_total` > 0 → do NOT auto-cancel; query `order_alerts` table; verify position on exchange manually; cancel only after confirming no open hedge

- **Given** the cross-strategy position concentration decision from Epic 13 implementation notes,
  **When** `bot-service/DESIGN.md` is inspected,
  **Then** it contains a section documenting the intentional design decision: two strategies holding the same symbol create combined exposure with no system-level cap; rationale and trade-offs are stated explicitly so future implementers do not add cross-strategy caps thinking they are filling a gap

**Test coverage:** N/A — documentation story; acceptance is human review of the ops.md section and DESIGN.md

---

## Epic 16: Production Observability & Operations

mrqdt can monitor all running strategies from Grafana — per-strategy P&L, open position sizes, drawdown, fill rate, consumer lag behind Redis streams, and order execution latency — via a Prometheus `/metrics` endpoint. The service runs in Docker Compose alongside the aggregator and candle service. A runbook entry covers deploy, rollback, credential rotation, and alert playbooks for strategy failures.

**FRs covered:** BS-FR35, BS-FR36 (production-hardened /metrics + /health)
**NFRs addressed:** BS-NFR5 (credentials in Docker Compose env_file pattern)

**Implementation notes (failure mode hardening):**
- Prometheus gauges for per-strategy metrics (`bot_position_size`, `bot_drawdown`, `bot_unrealized_pnl`, etc.) must be reset to sentinel values (0 for sizes, NaN or a reserved value for P&L) when a strategy thread dies — before the watchdog attempts restart. Stale non-zero position_size on a dead strategy will appear in Grafana as an active position and could trigger false alerts. Reset happens synchronously in the watchdog's death-detection path, before the backoff sleep. [Gap N]
- `/health` response must include per-strategy status: `{"strategies": {"OFIBot": "running", "FundingRateArb": "restarting", ...}}` so that Grafana and the runbook can identify which strategies are live vs recovering at a glance.
- Docker Compose `mem_limit: 2g` for the bot service (5 strategies x 200 bars x 67 features x 8 bytes ~= 5MB data; Python/pandas process overhead + GC spikes ~= 500MB-1.5GB; 2g provides headroom). `depends_on: questdb: condition: service_healthy` — requires a QuestDB healthcheck defined in the compose file (e.g., `curl -f http://questdb:9000/health` every 10s, 3 retries, 30s start period) so bot service doesn't start until QuestDB is serving queries. [Gap X]
