# Glossary — magnum-opus

Precise definitions for every term used across this codebase. When a term appears
in code, docs, or logs, it matches exactly one definition here.

---

## A

**Absorption** (`absorption_detected`)
A footprint pattern where high buy volume appears at a price level without the
price rising, suggesting sellers are "absorbing" the buy flow. Stored as BOOLEAN
in `snapshot_1m` and `snapshot_15m`. See `candle-service/internal/features/footprint.go`.

**Accumulator**
The per-(exchange, symbol) stateful component in candle-service that collects tick
events within a 1-second window and computes all 67 microstructure features.
Resets on each bar close. Lives at `candle-service/internal/accumulator/`.

**ACK / XACK**
Redis Stream acknowledgment. Moves a message from the Pending Entry List (PEL) to
"processed" state. The candle consumer ACKs *before* dispatching (see NOMICON §2).

**Aggregator**
The Go service (`aggregator/`) that connects to exchange WebSocket feeds, maintains
per-symbol L2 order books, and writes ticks to Redis Streams and QuestDB.

**ApplyGap**
A method on the candle-service's order book adapter. For `internal_merge_error`
cause it resets `snapshotSeen=false` (forces re-snapshot). For all other causes it
preserves book state. See NOMICON §1 and CLAUDE.md architecture constraints.

---

## B

**Bar** (also: **candle**)
A fixed-time-window OHLCV aggregate. The primary unit of output from candle-service.
Default window: 1 second (`snapshot_1s`). Cascaded to 1m, 15m via the cascade engine.

**Bar close** (`BarClose`)
The event fired at the boundary of each 1-second window by the candle-service ticker
goroutine. Triggers `Flush(false)` on the accumulator and dispatches a `BarClose`
event to the bot-service via Redis Streams.

**Barrier** (bot-service)
A synchronization primitive in the bot-service that blocks a strategy's event loop
until all expected data frames for a given timeframe have arrived. Timeout is
configurable per-timeframe (see `BOT_BARRIER_TIMEOUT_MS_*` env vars).

**BaseStrategy**
The abstract Python class (`bot-service/bot_service/strategy/base.py`) that all
trading strategies must subclass. Provides: NaN guard, lookback gate, gap invalidation,
heartbeat, bus-timeout safety, and emergency close.

**Best bid / best ask**
The highest bid price and lowest ask price in the order book. Used for spread
computation, OFI, and mid-price. Exposed via `OrderBook.BestQuote()`.

**Block trade**
A trade whose size exceeds a threshold (typically 2× the average trade size for
the symbol). Tracked in `block_buy_volume` and `block_sell_volume`.

**Blue-green deployment**
A zero-downtime deployment strategy where the new slot (`candle-blue` on port 8081
or `candle-green` on port 8082) warms up in shadow mode, then receives a `/promote`
HTTP call to switch it live. See `docs/architecture/03-candle-service.md`.

**BusManager**
The Python component in bot-service (`bus/event_bus.py`) that reads all Redis Streams
via XREADGROUP and routes parsed events to per-strategy asyncio queues.

---

## C

**Candle-service**
The Go service (`candle-service/`) that consumes ticks from Redis Streams, computes
1-second OHLCV bars with 67 microstructure features, and cascades them to longer
timeframes. Writes to QuestDB, Redis pub/sub, and daily Parquet on B2.

**Cascade** / **cascade engine**
The mechanism in candle-service that aggregates 1-second bars into higher timeframes
(1m, 15m). Operates on in-memory state and emits bars to all configured writers.

**Clock interface**
An interface with a single method `Now() time.Time`. Injected into all internal
packages. `realClock{}` is used in `cmd/` entry points; `testutil.MockClock` is
used in tests. Enforces the `time.Now()` ban. See NOMICON §10.

**Cold-start buffer overflow**
A gap cause (`cold_start_buffer_overflow`) emitted when the delta buffer fills before
the first snapshot arrives. Does not reset `snapshotSeen` in the candle-service OB.

**Consumer group** (Redis)
A Redis Streams feature that allows multiple consumers to cooperatively read from a
stream, each receiving a unique subset of messages. The candle-service uses one group
per (exchange, symbol): `candle-{exchange}-{symbol}`.

**Coordinator**
The Go component in aggregator (`internal/coordinator/`) that owns all per-symbol
`Worker` goroutines, the snapshot dispatcher, and the tick fanout logic.

**Credential** (type)
A sealed Go type in `aggregator/internal/config/config.go` that prevents raw API
keys from appearing in logs or error messages. See NOMICON §5.

**CVD** (Cumulative Volume Delta)
The running sum of (buy volume − sell volume) across bars. Stored in `cum_delta`.
`cvd_divergence` is a signal byte when price and CVD diverge directionally.

---

## D

**Dashboard**
The Python Dash+Plotly web application (`dashboard/`) that visualizes real-time
and historical market data from QuestDB and Redis.

**DDL** (Data Definition Language)
SQL statements that create or alter QuestDB tables. Stored as migration files in
`candle-service/migrations/`. Applied by the migrator on service startup.

**Delta**
A single L2 order book update from the exchange: one price level update (add,
modify, or remove). Represented as `orderbook.Delta` in the aggregator.

**Depth** (order book depth)
The cumulative size available within N levels or N% of mid-price. Fields:
`bid_depth_l1_open/close`, `bid_depth_l2_open/close`, `bid_depth_top10_open/close`,
`bid_depth_total_open/close` (and ask equivalents).

**Disconnect** (`external_disconnect`)
A gap cause indicating the WebSocket connection was lost by the network or exchange
without a rate-limit signal. Transient; excluded from the health window. See NOMICON §4.

---

## E

**Effective spread**
The realized cost of a trade: `2 × |trade_price − mid_price_at_execution|`.
Unlike the quoted spread (`best_ask − best_bid`), this measures actual fill quality.

**Emergency close**
A safety mechanism in `BaseStrategy` that fires a market-sell order when the
bus timeout expires and `close_on_bus_timeout=True`. Runs from a daemon thread.
See `bot-service/bot_service/strategy/base.py:_emergency_close_symbol`.

**Exchange adapter**
A Go component that wraps a specific exchange's WebSocket feed and exposes a uniform
`exchange.Exchange` interface. Current adapters: KuCoin (`kucoin/`), Bybit (`bybit/`).

---

## F

**Feed state** (`aggregator_feed_state`)
A Prometheus gauge: 1 = connected and live, 0 = disconnected or initializing.
Pre-initialized to 0 for all configured (exchange, symbol) pairs at startup (NFR19).

**Flush** (candle-service)
The action of writing a completed (or partial) bar to QuestDB, Redis pub/sub, and
the cascade engine. `Flush(isPartial=false)` = full bar; `Flush(isPartial=true)` =
250ms partial bar for real-time dashboard updates.

**Flusher** (B2 export)
The candle-service component (`internal/flusher/`) that exports completed daily
`snapshot_1s` data to Backblaze B2 as zstd-compressed Parquet at `FLUSH_TIME_UTC`.

**Footprint** / **footprint chart**
A visualization and derived features that break OHLCV bars into bid/ask volume at
each price level. Stored as `footprint_json` (VARCHAR). See `candle-service/internal/features/footprint.go`.

**Funding rate**
The periodic payment between long and short holders of a perpetual futures contract.
Polled by `bot-service/bot_service/exchange/funding_poller.py`. Delivered to
strategies via `FundingRate` events.

---

## G

**Gap** / **gap event**
A discontinuity in the order book sequence number stream. Classified into four causes:
`internal_merge_error`, `internal_buffer_overflow`, `external_disconnect`,
`external_rate_limit`. Causes a bar to have `gap_count > 0`.

**Gap dedup** (ZSET)
A Redis sorted set (`candle:gap_dedup:{exchange}:{symbol}`) that prevents duplicate
gap markers from being applied when multiple consumer instances process the same
gap message. Keyed by `seqBefore:seqAfter:cause`. See NOMICON §7.

**Gap window** (`gapwindow`)
The aggregator's rolling window of recent gap events, used to decide whether the
startup health gate should fail. Distinct from the Prometheus health metric.

**Gateway**
The Go WebSocket gateway service (`gateway/`) that subscribes to Redis pub/sub
channels (`orderbook:*`, `candles1s:*`) and forwards binary-encoded payloads to
browser WebSocket clients.

---

## H

**Has-gap** (`has_gap`)
A boolean column in `snapshot_1m` and `snapshot_15m` (and a rolling-DF field in
bot-service) that marks bars contaminated by a gap event. Bot strategies check this
before acting on signal values.

**Heartbeat** (bot-service)
A watchdog mechanism in `BaseStrategy` that sends a signal every 5 seconds. If the
strategy's event loop does not ACK within 10 seconds, `SIGTERM` is sent to the process.
Detects event-loop freezes. See `bot-service/bot_service/strategy/base.py:_heartbeat_loop`.

**Hub** (gateway)
The component (`gateway/internal/hub/hub.go`) that manages WebSocket client connections
and routes Redis pub/sub messages to the correct clients based on symbol subscriptions.

---

## I

**ILP** (InfluxDB Line Protocol)
The wire format used to write data to QuestDB. Fire-and-forget: errors are logged
but not propagated. See `aggregator/internal/writer/questdb/writer.go`.

**Imbalance** (footprint imbalance)
A footprint signal where the ratio of buy volume to sell volume at a price level
exceeds a threshold. Tracked in `imbalance_buy_count`, `imbalance_sell_count`,
`imbalance_stack_buy`, `imbalance_stack_sell`, `imbalance_ratio`.

**Internal gap** (`internal_*`)
A gap caused by a bug in this service (as opposed to exchange behavior). Includes
`internal_merge_error` (failed snapshot/delta merge) and `internal_buffer_overflow`
(delta buffer full). Internal gaps affect the health gauge.

---

## L

**L2 order book**
A price-level aggregated view of market liquidity: for each price, the total size
available. Contrast with L3 (per-order). The aggregator maintains an L2 book for
each configured (exchange, symbol) pair.

**Lag** (consumer lag)
The number of unprocessed messages in a Redis Stream consumer group's Pending Entry
List (PEL). Exposed as `aggregator_consumer_lag_ms` and `bot_consumer_lag`.

**Level** (order book level)
A single price with its associated size in the order book. A size of "0" means the
level is removed.

**Lookback gate**
A safety gate in `BaseStrategy.register_bar_handler` that suppresses handler invocations
until at least `min_lookback` bars have been received. Prevents signals computed on
insufficient history from generating trades.

---

## M

**MAXLEN** (Redis Stream)
The maximum number of entries a Redis Stream will retain (approximately, with `~`).
Configured via `REDIS_STREAM_MAXLEN` (default 50,000). The candle consumer checks
stream length on first connect and emits a `stream_overflow` gap marker if it exceeds
45,000 entries.

**Merge** / **snapshot-delta merge**
The process of combining a REST snapshot (captured at sequence S) with buffered
WebSocket deltas (seq > S) to produce a consistent book state. Managed by the
`reconnect.Machine`. See NOMICON §3.

**Mid-price**
The average of best bid and best ask: `(best_bid + best_ask) / 2`. Used for spread,
realized_vol (which uses mid-price returns, not trade prices), and market impact.

**Migration** (QuestDB)
An ordered SQL file in `candle-service/migrations/` that adds a column or creates a
table. Applied idempotently by the migrator on service startup. Filenames follow
`NNN_description.sql` format.

---

## N

**NaN guard**
A safety check in `BaseStrategy.register_bar_handler` that blocks handler invocation
if the most recent bar row contains any NaN value (indicator warmup). Prevents
NaN from propagating into order sizing calculations.

**NFR19**
The non-functional requirement that all Prometheus time series must be pre-initialized
to 0 before any feed connects. Prevents "No data" gaps in Grafana dashboards.
Implemented by `metrics.PreInit`. See NOMICON §9.

**NOMICON**
This file. Named after the Rust Nomicon (dark corners of unsafe Rust). Documents
things that look ordinary but are catastrophically dangerous if misunderstood.

---

## O

**OFI** (Order Flow Imbalance)
A microstructure metric measuring the net aggressive order flow. Formula:
`ofi = Σ(ΔBidSizeAtBest − ΔAskSizeAtBest)` across all ticks in the window.
Both `ofi` and `ofi_l1` currently store the same L1 value; full-book OFI is deferred.

**Order book** (`OrderBook`)
The in-memory L2 book maintained by the aggregator for each (exchange, symbol).
Pure state machine: no IO, no goroutines, single-goroutine ownership. See NOMICON §1.

**OrderQueueWorker** (bot-service)
The component in bot-service that receives `OrderRequest` objects from strategies
and executes them on the exchange. Serializes orders to prevent concurrent fills.

---

## P

**Partial bar** (`is_partial=true`)
A bar published mid-second (every 250ms) for real-time dashboard updates. Written
to QuestDB with `is_partial=true`; overwritten by the complete bar at second boundary
via QuestDB's UPSERT DEDUP mechanism.

**PEL** (Pending Entry List)
The Redis Streams structure that tracks messages delivered to a consumer but not
yet ACKed. XAUTOCLAIM transfers ownership of PEL entries between consumers.

**POC** (Point of Control)
The price level with the highest traded volume in a footprint bar. Stored as
`poc_price` and `poc_volume`.

**Promote** (blue-green)
The HTTP `POST /promote` call that transitions the shadow candle-service slot from
XREAD (warmup) to XREADGROUP (active). Implemented in `consumer.Promote()`.

---

## Q

**QuestDB**
The time-series database used for all OHLCV and microstructure data. Ingested via
ILP (InfluxDB Line Protocol) on port 9009. Queried via HTTP REST API on port 9000.

**quote_stuff_ratio**
The ratio of non-trade order book activity to trade count:
`(bid_order_arrivals + ask_order_arrivals + bid_cancel_count + ask_cancel_count) / trade_count`.
High values indicate quote stuffing or low-liquidity conditions.

---

## R

**Realized vol** (`realized_vol`)
Intra-second volatility computed from mid-price returns (not trade prices).
Formula: standard deviation of `log(mid[t] / mid[t-1])` for all mid-price changes
within the 1-second window.

**Reconnect machine** (`reconnect.Machine`)
A pure state machine (`aggregator/internal/reconnect/reconnect.go`) that manages the
Initial → Buffering → Live cycle for each symbol. Emits `NeedsSnapshot` when a
gap requires a REST snapshot fetch.

**Redis Stream**
An append-only log structure in Redis. Used for: ticks (aggregator → candle-service),
candle bars (candle-service → bot-service), and alerts. Keys follow `{type}:{exchange}:{symbol}` pattern.

---

## S

**Shadow mode** (candle-service)
A consumer mode where the incoming slot reads the stream via `XREAD` (no consumer
group, no ACK) to warm up its order book and accumulator state, without writing output.
Activated by `CANDLE_SHADOW_MODE=true`.

**Signal** (bot-service)
A directional or quantitative output from a strategy's indicator computation. A signal
is "invalid" when the symbol has had a recent gap and `min_lookback` clean bars have
not yet been observed.

**Slot** (candle-service)
One of the two candle-service instances: `candle-blue` (port 8081) or
`candle-green` (port 8082). Only one slot is "active" (writes output) at a time.
Identified by `CANDLE_SLOT` env var.

**Snapshot** (order book)
A point-in-time REST snapshot of the full L2 book for a symbol. Fetched when the
reconnect machine emits `NeedsSnapshot`. Applied via `OrderBook.ApplySnapshot`.

**Strategy** (bot-service)
A Python class subclassing `BaseStrategy` that implements a trading algorithm.
Registered with the `StrategyRegistry` and run in its own asyncio event loop / OS thread.

---

## T

**Tick**
A single market event: either a trade or an L2 order book update. Encoded as a
Redis Stream message. Consumed by candle-service to build bars.

**Timeframe** (`tf`)
The bar aggregation window. Values: `1s`, `1m`, `5m`, `15m`, `1h`, `4h`, `1d`, `1w`.
`1s` bars are stored in `snapshot_1s`. `1m/5m` use `snapshot_1m`. `15m+` use `snapshot_15m`.

**Trade** (tick type)
A matched order: a real transaction between a buyer and a seller. Distinguished from
order book updates by `level == 0` in the tick stream encoding.

**Trade sign autocorrelation** (`trade_sign_autocorr`)
The Pearson correlation between consecutive trade direction indicators (+1 = buy,
−1 = sell). Positive values indicate momentum; negative values indicate mean-reversion.

**TWAP** (Time-Weighted Average Price)
Average trade price weighted by time, not volume. Computed over the 1-second window.

---

## U

**Unfinished auction** (`unfinished_top`, `unfinished_bottom`)
A footprint pattern where the highest (or lowest) price level of the bar has only
single-sided volume, indicating the move ended without full market participation.

---

## V

**Value area** (`value_area_high`, `value_area_low`)
The price range containing 70% of the bar's traded volume, centered around the POC.
Standard volume profile concept from market profile theory.

**VWMP** (Volume-Weighted Mid Price)
The volume-weighted average of mid-price observations during the bar. Similar to
VWAP but uses mid-price instead of trade price, making it independent of trade activity.

---

## W

**WAL** (Write-Ahead Log)
QuestDB's durability mechanism. All tables use `WAL` mode with `DEDUP UPSERT KEYS(ts, exchange, symbol)`,
which enables safe partial-bar overwrites (partial → complete bar within the same second).

**Worker** (aggregator)
The per-symbol goroutine in the aggregator coordinator that processes deltas from the
fanout channel, manages the reconnect state machine, and emits ticks to the stream writer.

---

## X

**XAUTOCLAIM**
Redis command that transfers ownership of pending stream messages from one consumer
to another. Used by candle-service on blue-green promotion to recover un-ACKed messages.

**XREADGROUP**
Redis command for consumer-group-based stream reading. Each message is delivered to
exactly one consumer in the group. Used by candle-service and bot-service.
