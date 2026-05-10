---
description: Requirements inventories for all magnum-opus services. Referenced by epics.md, epics-candle.md, and epics-bot.md.
---

# magnum-opus — Requirements Inventories

This file is the authoritative FR/NFR/ARC reference for all services. Epic files cross-reference these codes without duplicating them here.

---



### Functional Requirements

**Exchange Feed Connectivity**
- FR1: The service can establish an authenticated WebSocket connection to KuCoin using a token obtained from the KuCoin REST API
- FR2: The service can renew its KuCoin WebSocket authentication token automatically before the 24-hour expiry window elapses
- FR3: The service can maintain a KuCoin connection heartbeat (ping/pong) at the exchange-required interval to prevent silent connection drops
- FR4: The service can distribute Bybit symbol subscriptions across multiple concurrent WebSocket connections to respect the 10-topics-per-connection limit
- FR5: The service can verify subscription confirmation for each symbol on each exchange before treating that feed as active
- FR6: The service can subscribe to L2 order book and trade event streams for up to 200 symbols per exchange simultaneously
- FR7: The service can detect when a WebSocket connection has been dropped by the exchange or the network
- FR8: The service can reconnect to an exchange feed after a disconnection and resume data capture without operator intervention

**Order Book State Management**
- FR9: The service can maintain an in-memory L2 order book per symbol by applying sequential delta updates from the exchange feed
- FR10: The service can initialize a symbol's L2 order book from a REST snapshot when the WebSocket feed is first established or after a gap event
- FR11: The service can buffer incoming delta messages for a symbol while a REST snapshot request for that symbol is in flight
- FR12: The service can merge a received snapshot with its corresponding buffered deltas by replaying only deltas whose sequence numbers are greater than the snapshot's sequence number
- FR13: The service can detect that a received snapshot's sequence number is lower than the oldest buffered delta, indicating the merge window has been missed (stale snapshot)
- FR14: The service can detect incoming delta messages with sequence numbers at or below the last applied sequence number (out-of-order or duplicate)
- FR15: The service can discard out-of-order and duplicate delta messages without applying them to book state

**Gap Detection & Classification**
- FR16: The service can detect a sequence number gap in a symbol's message stream when an incoming sequence number is non-contiguous with the last applied sequence number
- FR17: The service can classify each detected gap as exactly one of four causes: `internal_buffer_overflow`, `internal_merge_error`, `external_disconnect`, or `external_rate_limit`
- FR18: The service can emit an in-band gap marker into the same `ticks:{exchange}:{symbol}` Redis Stream as tick data, carrying `gap_cause`, `seq_before`, `seq_after`, and gap timestamp
- FR19: The service can write every emitted gap marker to the `gaps:log` Redis Stream regardless of gap cause
- FR20: The service can distinguish `internal_*` gap causes (bugs in this service) from `external_*` gap causes (exchange behaviour) in its monitoring output

**Data Output**
- FR21: The service can publish normalized L2 update events to `ticks:{exchange}:{symbol}` Redis Streams with a stable, versioned schema
- FR22: The service can publish normalized trade events to `ticks:{exchange}:{symbol}` Redis Streams alongside L2 updates
- FR23: The service can write raw tick data to QuestDB via ILP in batched writes
- FR24: The service can detect QuestDB WAL suspension and automatically issue a resume command without operator intervention
- FR25: The service can bound Redis Stream memory usage by trimming streams to a maximum entry count
- FR26: Downstream consumers can read from `ticks:{exchange}:{symbol}` streams using Redis consumer groups and resume from their last-acknowledged position after a restart

**Operational Observability**
- FR27: The operator can query the service's health status including per-exchange connection state, 24-hour gap count, and an overall status value of `ok`, `degraded`, or `critical`
- FR28: The operator can query the deployed binary's version, git SHA, and build timestamp
- FR29: The operator can scrape a Prometheus metrics endpoint exposing per-symbol tick rates, gap counts by cause, feed connection state, and write latency distributions
- FR30: The operator can identify whether the aggregator has been a source of data loss (any `internal_*` gap) versus whether a gap was caused by exchange behaviour (`external_*`) without inspecting logs

**Service Lifecycle & Configuration**
- FR31: The operator can configure all exchange credentials, connection parameters, and symbol lists via environment variables without modifying source code or compiled binary
- FR32: The service can perform a startup validation gate: if all feeds have not confirmed subscriptions within 90 seconds, exit with a non-zero status
- FR33: The operator can trigger a graceful shutdown via SIGTERM that drains in-flight tick writes and flushes buffered QuestDB writes before the process exits
- FR34: The operator can validate that the running binary on a deployment target matches the intended git SHA via a version endpoint and build tooling

**Credential Security**
- FR35: The service can load exchange API credentials exclusively from environment variables at runtime
- FR36: The service can prevent API credentials from appearing in log output, error messages, or panic stack traces

### NonFunctional Requirements

**Performance**
- NFR1: Tick-to-Redis Stream write latency must be < 10ms at p99
- NFR2: Tick-to-QuestDB write latency must be < 500ms at p99 (batched writes)
- NFR3: The service must sustain a minimum of 2,400 ticks/second across 400 simultaneous feeds without dropping messages
- NFR4: Steady-state memory footprint must remain below 512 MB RSS with all 400 feeds active
- NFR5: Steady-state CPU utilization must remain below 1 vCPU on a Hetzner CPX41 during normal operation

**Reliability**
- NFR6: The service must automatically detect and begin reconnecting to a dropped feed within 5 seconds of the disconnect event
- NFR7: The service must restore all feed subscriptions within 90 seconds of a clean process restart
- NFR8: A process crash must not produce silent data corruption — any record written before the crash must be complete and well-formed
- NFR9: QuestDB write failures must not halt feed processing — the service must continue capturing ticks to Redis Streams even when QuestDB writes are failing or retrying
- NFR10: The service must operate continuously for ≥30 days without requiring an operator-initiated restart under normal exchange conditions

**Data Correctness**
- NFR11: The service must produce zero `internal_*` gap causes under normal operating conditions
- NFR12: The external gap SLO is ≤1 `external_*` gap per symbol per 24-hour period
- NFR13: The snapshot/delta merge must verify sequence overlap before resuming the live feed
- NFR14: No delta message may be applied to L2 book state more than once

**Security**
- NFR15: Exchange API credentials must not appear in log output, error messages, panic stack traces, or Prometheus metric label values at any log level including DEBUG
- NFR16: Credentials must be loaded exclusively from environment variables at process startup
- NFR17: The `/version`, `/health`, and `/metrics` endpoints must not require authentication

**Integration Stability**
- NFR18: The Redis Stream schema for tick messages and gap markers must be backward-compatible across service versions
- NFR19: The Prometheus `/metrics` endpoint must expose all defined metrics at all times, including when feeds are disconnected
- NFR20: The `/health` endpoint must respond within 100ms regardless of exchange feed connection state

**Testability**
- NFR21: The service must be testable at five independent layers (L1–L5) without modifying production code paths
- NFR22: The four injectable test seams (MockWSServer, FakeRedis/Toxiproxy, FakeQuestDB, MockClock) must be substitutable via interfaces or constructor injection

### Additional Requirements

Architecture-derived requirements that directly affect implementation scope:

- **ARC1:** Greenfield Go module — initialize with `go mod init github.com/mrqdt/magnum-opus/aggregator` and pin specific library versions: `github.com/coder/websocket v1.8.14` (maintained fork of archived `nhooyr.io/websocket`), `github.com/redis/go-redis/v9`, `github.com/questdb/go-questdb-client/v3`, `github.com/prometheus/client_golang`, `github.com/stretchr/testify`
- **ARC2:** `.env.example` must document all environment variables with descriptions and example values — must be written before `internal/config/` implementation begins
- **ARC3:** `raw_ticks` QuestDB `CREATE TABLE` DDL must be written as the first story in `writer/questdb/` to prevent schema drift — field list: exchange, symbol, seq, ts_exchange, ts_local, side, price (string), size (string), event_type, is_gap, gap_cause (nullable)
- **ARC4:** Implementation sequence is ordered and must not be reordered: (1) config/+symbol/, (2) orderbook/+reconnect/+gapdetector/ with L1 tests, (3) exchange interface+transport/, (4) kucoin/+bybit/mux/+bybit/, (5) coordinator/interfaces.go then writer/redis/+writer/questdb/, (6) coordinator/, (7) metrics/+health/, (8) cmd/aggregator/main.go, (9) Makefile+docker-compose.yml+Dockerfile
- **ARC5:** Five-layer test architecture enforced via Go build tags: L1 (no tag), L2 (`//go:build l2`), L3 (`//go:build l3`), L4 (`//go:build l4`), L5 (`//go:build live`) — Makefile targets: test-l1, test-l2, test-l3, test-l4, test-all, test-live
- **ARC6:** CI/CD pipeline: GitHub Actions runs L1+L2 on every push/PR; `release.yml` builds multi-stage Docker image and pushes to `ghcr.io/mrqdt/magnum-opus/aggregator` on git tag push
- **ARC7:** Docker Compose deployment on Linode (8 vCPU, 16 GB RAM) with resource limits — aggregator: mem_limit 600m/cpus 2.0; redis: 2g; questdb: 8g; stop_grace_period: 15s
- **ARC8:** Linode VM snapshot before each deploy for 5-minute rollback
- **ARC9:** Credential sanitizing `slog.Handler` wrapper must be wired in `main.go` before any other package initializes its logger — covers DEBUG output and third-party library log calls
- **ARC10:** Dockerfile multi-stage build required: build stage (Go toolchain) + minimal runtime stage — not yet specified in architecture, must be authored as part of deployment story
- **ARC11:** All external dependencies must be actively maintained at time of adoption and on every future dependency update. Criteria: not archived, has had a commit within the last 12 months, has an active maintainer. The canonical example: `nhooyr.io/websocket` is archived — use `github.com/coder/websocket v1.8.14` (its maintained fork) instead. `gorilla/websocket` is also archived and must not be used. Before adding any new dependency, verify active maintenance status. Pinned versions (e.g. QuestDB image tag) must be updated deliberately — not left on `:latest` — but must be periodically reviewed for security patches.

### UX Design Requirements

N/A — magnum-opus aggregator is a headless backend daemon with no user interface.

### FR Coverage Map

| FR | Epic | Area |
|---|---|---|
| FR1–FR8 | Epic 2 | Exchange feed connectivity (KuCoin auth/heartbeat/renewal, Bybit mux, subscription confirmation, reconnect) |
| FR9–FR15 | Epic 1 | Order book state machine (pure: apply delta, snapshot init, buffer, merge, dedup) |
| FR16–FR17 | Epic 1 | Gap detection and 4-cause classification (pure: gapdetector/) |
| FR18–FR19 | Epic 3 | In-band gap marker emission (coordinator + writer/redis) |
| FR20 | Epic 4 | Internal vs external gap visibility in monitoring output (/health, /metrics) |
| FR21–FR26 | Epic 3 | Redis Streams (ticks + gap markers), QuestDB ILP, MAXLEN trimming, consumer groups |
| FR27–FR30 | Epic 4 | /health, /version, /metrics, internal vs external gap distinguishability |
| FR31, FR35–FR36 | Epic 1 | Env-var config loading, credential isolation from logs/errors/panics |
| FR32–FR34 | Epic 4 | Startup validation gate, SIGTERM drain, deploy verification via /version |

## Candle Service Requirements

### Functional Requirements

**Stream Reading**
- CS-FR1: Read `ticks:{exchange}:{symbol}` streams via Redis consumer group; on first connect start from current position (`$`) — if the stream has been trimmed to MAXLEN (>45,000 entries at startup), emit a gap marker for the skipped range; on restart resume from last-ack'd position
- CS-FR2: Parse both `type=tick` and `type=gap` entries from the stream; on unknown message type log WARN and XACK without processing — do not stall the consumer
- CS-FR3: Deduplicate gap markers on `(exchange, symbol, seq_before, seq_after, gap_cause)` — both `exchange` and `symbol` are required fields in the dedup key; aggregator may emit retries

**L2 Order Book Maintenance**
- CS-FR25: Maintain in-memory L2 order book per symbol: `event_type=snapshot` resets state, `event_type=update` applies deltas; zero-size delta removes the price level
- CS-FR27: On `event_type=snapshot`: immediately flush the current 1-second accumulator with `is_partial=true` and reinitialize OB state; call `accumulator.Reset()` before feeding new ticks

**1-Second Aggregation**
- CS-FR4: Compute 1-second OHLCV bars (open, high, low, close, volume, quote_volume, trade_count, twap)
- CS-FR5: Track mid-price path per second (mid_price_open, mid_price_high, mid_price_low, vwmp)
- CS-FR6: Compute spread features per second (spread_high, spread_low, spread_mean, effective_spread)
- CS-FR7: Capture L2 OB state at open and close of each second (best_bid, best_ask, depth at L1/L2/top10/total for both open and close snapshots)
- CS-FR8: Compute book shape features (weighted_bid_price, weighted_ask_price)
- CS-FR9: Compute market impact features (depth_to_1pct_bid, depth_to_1pct_ask)
- CS-FR10: Compute OFI features (ofi: full-book, ofi_l1: L1-only)
- CS-FR11: Classify trades by aggressor side (`side=bid` → buy, `side=ask` → sell); accumulate buy_volume, buy_count
- CS-FR12: Classify block trades using rolling 99th-percentile threshold over last N trades per symbol (N = `BLOCK_TRADE_WINDOW`, default 1000); accumulate block_buy_volume, block_sell_volume
- CS-FR13: Compute trade distribution features (max_trade_size, large_bid_orders, large_ask_orders, first_trade_offset_ms, last_trade_offset_ms, trade_clustering as Gini coefficient of inter-trade intervals, max_consecutive_run)
- CS-FR14: Compute volatility features (realized_vol, realized_skewness, uptick_count, downtick_count)
- CS-FR15: Compute OB activity features (bid/ask_order_arrivals, bid/ask_cancel_count, ob_modify_count, avg_bid/ask_order_size, best_bid/ask_changes, quote_stuff_ratio)
- CS-FR16: Compute trade microstructure features (trade_sign_autocorr, inter_trade_interval_std_ms, num_trade_price_levels)
- CS-FR17: Track bar quality fields: gap_count (incremented per gap marker in window), bar_count

**Data Output**
- CS-FR18: Write completed 1-second bars to QuestDB `snapshot_1s` via ILP; for empty seconds write a null row (ts/exchange/symbol populated, all computed fields null)
- CS-FR19: Cascade 1-second bars to 1m, 5m, 15m, 1h, 4h, 1d, 1w OHLCV timeframes
- CS-FR20: Publish to `candles:{exchange}:{symbol}:{tf}` on every accumulator update (`is_complete: false`) and on bar close (`is_complete: true`); publish weekly bars also to `candles:1w:{exchange}:{symbol}` alias
- CS-FR21: Publish 1-second OB feature snapshots to `ob_features:{exchange}:{symbol}` on bar close

**Cold Storage**
- CS-FR22: Perform daily Parquet flush of `snapshot_1s` to Backblaze B2 in Hive-partitioned format (zstd compressed)
- CS-FR23: Write `flush_manifest` record to QuestDB after each flush attempt (success or failure, with row count, B2 path, error message if failed)
- CS-FR24: Publish to `alerts:flush_failure` Redis stream on daily flush failure

**Configuration**
- CS-FR26: Load all configuration from env vars: `REDIS_URL`, `QUESTDB_ILP_ADDR`, `SYMBOLS_KUCOIN`, `SYMBOLS_BYBIT`, `B2_ACCESS_KEY_ID`, `B2_SECRET_ACCESS_KEY`, `B2_BUCKET_NAME`, `B2_ENDPOINT`, `BLOCK_TRADE_WINDOW`, `BLOCK_TRADE_MIN_SAMPLE`, `LOG_LEVEL`, `CANDLE_STREAM_MAXLEN`, `CANDLE_SERVICE_PORT`, `CANDLE_PARTIAL_PUBLISH_MS`, `CANDLE_CONSUMER_GROUP`, `COLD_START_BUFFER_MAX`, `QUESTDB_ILP_FLUSH_MS`

**Observability**
- CS-FR28: Expose `/health` (consumer lag per symbol, QuestDB write state, overall ok/degraded/critical), `/version` (git SHA, build timestamp), and `/metrics` (Prometheus) on `CANDLE_SERVICE_PORT`. Required metrics: `candle_bars_total{exchange,symbol}`, `candle_consumer_lag{exchange,symbol}`, `candle_questdb_write_latency_ms`, `candle_gap_count_total{exchange,symbol}`, `candle_flush_success_total`, `candle_flush_failure_total`, `candle_flush_alert_failure_total`, `candle_bar_close_dropped_total{exchange,symbol}`, `candle_redis_publish_failure_total{exchange,symbol}`, `candle_cascade_state_write_failure_total{exchange,symbol}`

### Non-Functional Requirements

- CS-NFR1: Bar publish latency ≤2s from second boundary to QuestDB write
- CS-NFR2: Sustained write rate: ~400 rows/sec (200 symbols × 2 exchanges)
- CS-NFR3: Zero silent gap corruption: every gap marker in a window must increment gap_count
- CS-NFR4: On restart, replay all unacknowledged stream entries without double-counting bars
- CS-NFR5: Daily B2 flush must complete within 4 hours of the day boundary
- CS-NFR6: All credentials (Redis, QuestDB, B2) loaded exclusively from env vars
- CS-NFR7: Service containerized and co-deployed via docker-compose alongside aggregator
- CS-NFR8: Testable at four layers: L1 pure-function unit tests (MockClock injected), L2 mock interfaces (FakeRedis/FakeQuestDB), L3 mock Redis server in-process, L4 Toxiproxy + testcontainer QuestDB — no L5 (this service does not connect to exchanges)

### FR Coverage Map

| FR | Epic | Description |
|---|---|---|
| CS-FR1 | 5 | Redis consumer group, startup position |
| CS-FR2 | 5 | Tick/gap message parsing |
| CS-FR3 | 5 | Gap marker deduplication |
| CS-FR25 | 5 | In-memory L2 OB maintenance |
| CS-FR26 | 5 | Env-var configuration |
| CS-FR27 | 5 | Snapshot flush + OB reinit |
| CS-FR4 | 5 | 1s OHLCV computation |
| CS-FR18 (OHLCV write) | 5 | QuestDB ILP write — OHLCV fields only; full DDL created from day one |
| CS-FR5 | 6 | Mid-price path features |
| CS-FR6 | 6 | Spread features |
| CS-FR7 | 6 | OB state at open/close |
| CS-FR8 | 6 | Book shape features |
| CS-FR9 | 6 | Market impact features |
| CS-FR10 | 6 | OFI features |
| CS-FR11 | 7 | Trade direction classification |
| CS-FR12 | 7 | Block trade rolling percentile |
| CS-FR13 | 7 | Trade distribution features |
| CS-FR14 | 7 | Volatility features |
| CS-FR15 | 7 | OB activity features |
| CS-FR16 | 7 | Trade microstructure features |
| CS-FR17 | 7 | Gap count + bar quality |
| CS-FR18 (null rows) | 7 | Empty second null-row behaviour |
| CS-FR19 | 8 | Timeframe cascade engine |
| CS-FR20 | 8 | Redis candle stream output (time-based partial updates) |
| CS-FR21 | 8 | Redis OB feature snapshot output |
| CS-FR22 | 9 | Daily B2 Parquet flush |
| CS-FR23 | 9 | flush_manifest QuestDB write (DDL created in same story) |
| CS-FR24 | 9 | alerts:flush_failure publish |
| CS-FR28 | 5 | Observability: /health, /version, /metrics |

## Bot Service Requirements

### Functional Requirements (BS-FR)

**Event Bus**
- BS-FR1: The bus reads typed events from Redis streams (`candles:close:*`, `ob_features:*`, `ticks:*`, `funding:*`) and routes them as typed dataclasses (BarClose, Tick, OBSnapshot, FundingRate, AISignal, GapMarker, OrderFilled, OrderRejected) to per-strategy asyncio Queues
- BS-FR2: The bus synchronizes multi-symbol BarClose events using a barrier that holds events until all subscribed symbols have arrived for a given timestamp, then delivers a unified MultiBarClose
- BS-FR3: The barrier times out after 500ms and delivers a partial MultiBarClose with missing symbols as None plus a GapMarker for each missing symbol
- BS-FR4: Barrier timestamp matching uses floor division (`ts // tf_ms * tf_ms`) to tolerate cross-exchange clock skew up to one full bar period
- BS-FR5: The routing table is built during `subscribe()` and is fixed for the lifetime of the strategy — no mid-run re-subscription for MVP

**BaseStrategy Foundation**
- BS-FR6: Each strategy declares subscriptions via `subscribe(bus: EventBus)` at instantiation; this method may query QuestDB for dynamic symbol discovery, check volatility regime, and conditionally subscribe to AI signals
- BS-FR7: BaseStrategy maintains one rolling DataFrame per `(symbol, timeframe)` pair, cold-started from QuestDB `snapshot_1s` on first `get_history()` call
- BS-FR8: A minimum lookback gate (`if len(df) < self.min_lookback: return`) prevents signal computation until the DataFrame is sufficiently warm
- BS-FR9: A NaN guard decorator (applied by BaseStrategy) raises before any signal action when NaN values are present in the computation inputs
- BS-FR10: GapMarker events are mandatory subscriptions for all strategies; BaseStrategy invalidates/pauses signal computation on receipt — not optional per strategy
- BS-FR11: Per-strategy risk gate (`max_position_pct`, `stop_loss_pct`) is enforced in BaseStrategy before any OrderRequest reaches the order queue

**Concurrency**
- BS-FR12: Each loaded strategy runs in a dedicated thread with its own `asyncio.new_event_loop()`; a crash in one thread cannot affect others
- BS-FR13: A central Bus Manager thread reads all Redis streams and routes events into per-strategy `asyncio.Queue`s via `run_coroutine_threadsafe()`

**Exchange Connectivity**
- BS-FR14: Direct `httpx` async REST calls to KuCoin and Bybit for order placement — no ccxt dependency
- BS-FR15: Private WebSocket per exchange provides real-time fill events (~10ms); on WebSocket silence >N seconds, REST poll fallback activates
- BS-FR16: Authentication implemented directly: HMAC signing for KuCoin, API key signing for Bybit

**Order Lifecycle**
- BS-FR17: Each strategy has a dedicated asyncio order queue and worker coroutine; strategy posts OrderRequest and immediately continues processing events
- BS-FR18: Three-layer open order state: (1) in-memory `self.open_orders` dict checked before every new order, (2) QuestDB write only after exchange confirmation with every outcome persisted, (3) on restart QuestDB non-terminal orders reconciled against exchange REST
- BS-FR19: Orphaned exchange orders (on exchange, absent from QuestDB) are written to `order_alerts` table and operator alerted — never auto-cancelled
- BS-FR20: `order_events` QuestDB table records all order state transitions: placed/open/partially_filled/filled/cancelled/rejected/failed

**Position Tracking & Recovery**
- BS-FR21: In-memory position state updated on every OrderFilled; on startup exchange REST queried and reconciled against QuestDB — exchange is authoritative
- BS-FR22: Open positions from reconciliation force-subscribed before dynamic pair discovery runs
- BS-FR23: Simultaneous opposite positions across independent strategies on the same symbol are explicitly allowed — no cross-strategy portfolio-level netting or guards (architectural constraint, not code)
- BS-FR24: Watchdog monitors strategy threads with `thread.is_alive()`; on crash: exponential backoff restart (5s->10s->30s->60s max), reconcile exchange on each restart
- BS-FR25: Per-strategy heartbeat: `bus_timeout_seconds` + `close_on_bus_timeout: bool`; on timeout: always alert; if `close_on_bus_timeout=True`, market-sell all open positions immediately (default True for microstructure bots, False for funding rate arb)

**Paper Trading**
- BS-FR26: `paper_trading = True` class variable on a strategy transparently swaps `ExchangeClient` for `PaperExchangeClient`
- BS-FR27: PaperExchangeClient simulates REST latency from a configurable distribution (50-250ms); checks live tick stream for whether price crossed the limit during the latency window; fills market orders at mid-price + configurable slippage bps
- BS-FR28: PaperExchangeClient writes to QuestDB `order_events` with `paper_trading=true` column — filterable in Grafana alongside live results

**Backtesting**
- BS-FR29: `QuestDBFeed` subclasses `bt.feeds.PandasData`; queries `snapshot_1s` and cascade tables; exposes all 67 microstructure features as Backtrader extra lines
- BS-FR30: Custom `bt.CommissionInfo` subclass matches exact KuCoin/Bybit maker/taker fee schedules; funding rate cost applied for perp strategies
- BS-FR31: Signal logic extracted into pure functions in `strategy/signals/` with no framework dependency; both live BaseStrategy handlers and Backtrader `bt.Strategy.next()` call the same functions
- BS-FR32: Backtest results written to QuestDB `order_events` with `backtest=True` — queryable in Grafana alongside paper and live
- BS-FR33: Walk-forward harness, stress test windows (LUNA collapse 2022-05, FTX collapse 2022-11, flash crashes), and Monte Carlo (10,000 TradeAnalyzer result shuffles) available as utilities

**Strategy Lifecycle**
- BS-FR34: File watcher monitors `strategies/active/` directory; `.py` files spawned as strategy threads; files moved to `strategies/inactive/` stop their threads

**Observability**
- BS-FR35: FastAPI `/metrics` Prometheus endpoint with per-strategy metrics: P&L, position sizes, fill count, drawdown, consumer lag, execution latency
- BS-FR36: FastAPI `/health` endpoint returning service status and active strategy list

**Persistence Schema**
- BS-FR37: `order_events` DDL (as specified in architecture.md) committed before any persistence code
- BS-FR38: `order_alerts` DDL committed before any persistence code

### Non-Functional Requirements (BS-NFR)

- BS-NFR1: A strategy thread crash must not affect any other running strategy thread — crash isolation is complete at thread boundaries
- BS-NFR2: Signal computation must be suspended on GapMarker receipt; no OrderRequest may be posted while signal is invalidated from a gap
- BS-NFR3: No signal computation until minimum lookback DataFrame length is satisfied — NaN propagation into orders is a critical failure mode
- BS-NFR4: NaN propagation prevented by BaseStrategy decorator on every signal action
- BS-NFR5: Exchange API credentials never in logs, errors, panics, or Prometheus metric labels at any log level
- BS-NFR6: Credentials loaded exclusively from environment variables at startup — no hardcoded defaults, no config files in source control
- BS-NFR7: QuestDB ILP writes are fire-and-forget — write failures logged but do not halt strategy execution (same pattern as aggregator and candle service)
- BS-NFR8: After any crash, open positions and non-terminal orders must be fully recoverable from exchange REST + QuestDB reconciliation — no silent position loss on restart

### Additional Requirements from Architecture

- Python 3.11+ FastAPI service — new `bot-service/` directory at repo root
- `httpx` (async) for all exchange REST calls — no ccxt
- QuestDB ILP via Python client — TCP port 9009, fire-and-forget pattern matching aggregator/candle service
- Backtrader as the backtesting engine
- Docker Compose integration — new service alongside existing aggregator, candle-service, redis, questdb
- Credentials via environment variables, `.env` + Docker Compose `env_file:` pattern (same as other services)
- Hedge Arb (Go service) and JavaScript frontend explicitly out of scope for Epics 11-16

### Bot Service FR Coverage Map

| FR | Epic | Description |
|---|---|---|
| BS-FR1 | 11 | Event bus reads Redis streams, routes typed events to per-strategy queues |
| BS-FR2 | 11 | MultiBarClose barrier synchronization |
| BS-FR3 | 11 | Barrier timeout with partial delivery + GapMarker |
| BS-FR4 | 11 | Floor-timestamp barrier matching for clock skew tolerance |
| BS-FR5 | 11 | Routing table fixed after subscribe() completes |
| BS-FR6 | 11 | subscribe() registration method on BaseStrategy |
| BS-FR7 | 11 | Multi-timeframe DataFrame cold-start from QuestDB |
| BS-FR8 | 11 | Minimum lookback gate |
| BS-FR9 | 11 | NaN guard decorator |
| BS-FR10 | 11 | GapMarker mandatory handler in BaseStrategy |
| BS-FR11 | 11 | Risk gate (max_position_pct, stop_loss_pct) in BaseStrategy |
| BS-FR12 | 11 | Per-strategy dedicated thread with own asyncio event loop |
| BS-FR13 | 11 | Central Bus Manager thread routing |
| BS-FR14 | 12 | Direct httpx REST to KuCoin and Bybit |
| BS-FR15 | 12 | Private WebSocket per exchange + REST poll fallback |
| BS-FR16 | 12 | Exchange-specific auth (HMAC, API key signing) |
| BS-FR17 | 12 | Per-strategy asyncio order queue + worker coroutine |
| BS-FR18 | 12 | Three-layer open order state |
| BS-FR19 | 12 | Orphaned order detection -> order_alerts (never auto-cancel) |
| BS-FR20 | 12 | order_events all-outcome persistence |
| BS-FR21 | 13 | Startup exchange reconciliation + in-memory position tracking |
| BS-FR22 | 13 | Open positions force-subscribed before dynamic discovery |
| BS-FR23 | 13 | Opposite positions across strategies explicitly allowed (architectural constraint) |
| BS-FR24 | 13 | Watchdog + exponential backoff restart |
| BS-FR25 | 13 | Heartbeat timeout + configurable emergency close |
| BS-FR26 | 12 | paper_trading class var -> PaperExchangeClient swap |
| BS-FR27 | 12 | PaperExchangeClient latency simulation + tick-based fill |
| BS-FR28 | 12 | paper_trading=true column in order_events |
| BS-FR29 | 14 | QuestDBFeed with 67 microstructure feature lines |
| BS-FR30 | 14 | Custom CommissionInfo matching exchange fee schedules |
| BS-FR31 | 14 | Shared signal layer pure functions in strategy/signals/ |
| BS-FR32 | 14 | Backtest results to order_events with backtest=True |
| BS-FR33 | 14 | Walk-forward, stress test, Monte Carlo harnesses |
| BS-FR34 | 13 | File watcher strategies/active/ directory |
| BS-FR35 | 16 | Prometheus /metrics per-strategy gauges, counters, histograms |
| BS-FR36 | 11+16 | Basic /health in Epic 11; production /metrics + hardened /health in Epic 16 |
| BS-FR37 | 11 | order_events DDL committed before any persistence code |
| BS-FR38 | 11 | order_alerts DDL committed before any persistence code |
