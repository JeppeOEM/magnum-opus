---
project_name: magnum-opus — Candle Service
user_name: mrqdt
date: '2026-05-08'
service: candle-service
optimized_for_llm: true
---

# Project Context: magnum-opus Candle Service

_Critical implementation rules for AI agents. Each rule is non-obvious — things that cause silent failures or wrong behavior if missed. Read before writing any code for the candle-service._

---

## What This Service Does

The Candle Service reads normalized ticks from Redis Streams produced by the Go aggregator, maintains per-symbol L2 order book state, computes 1-second OHLCV + microstructure aggregates (67 fields), cascades to 7 higher timeframes, writes to QuestDB, publishes candle and OB feature streams to Redis, and flushes daily Parquet archives to Backblaze B2.

It does NOT connect to exchanges. It does NOT manage WebSocket connections. All tick data arrives via Redis consumer groups.

---

## Technology Stack & Versions

- **Language:** Go 1.21+ — same as aggregator; `go.mod` must declare `go 1.21` minimum
- **Module path:** `github.com/mrqdt/magnum-opus/candle-service` — separate module from the aggregator
- **Directory:** `candle-service/` at the repo root — completely separate codebase
- **Redis client:** `github.com/redis/go-redis/v9` — set `MaxLen` on every `XAdd`; use `XREADGROUP` + `XACK` for consumption
- **QuestDB client:** `github.com/questdb/go-questdb-client/v3` — ILP over TCP port **9009**; NOT goroutine-safe — single sender per process behind a channel; WAL suspension detection via `wal_tables()` polling
- **Prometheus:** `github.com/prometheus/client_golang` — always `prometheus.NewRegistry()`; never `prometheus.MustRegister` or `prometheus.DefaultRegisterer`; use `promhttp.HandlerFor(registry, promhttp.HandlerOpts{})`
- **Parquet:** `github.com/parquet-go/parquet-go` — for daily flush to B2
- **B2 (S3-compatible):** `github.com/aws/aws-sdk-go-v2/service/s3` — B2 uses S3-compatible API
- **Logging:** `log/slog` (stdlib) — custom `slog.Handler` must implement all 4 methods: `Enabled`, `Handle`, `WithAttrs`, `WithGroup`
- **Testing:** `github.com/stretchr/testify`
- **Backoff:** `internal/backoff/` (internal package, same pattern as aggregator) — never an external backoff library
- **No WebSocket library** — this service does not connect to exchanges
- **Redis image:** `redis:7-alpine`
- **QuestDB image:** pin to `questdb/questdb:8.2.1` — never `:latest` or `:8.x`

---

## Package Layout

```
candle-service/
  cmd/candle/               # composition root only — no logic here
  internal/
    config/                 # Config struct, Load(), sealed credential type (no Stringer)
    symbol/                 # normalized symbol type — zero internal imports
    orderbook/              # L2 book state machine — pure, zero IO, L1-testable
    accumulator/            # 1s OHLCV + feature accumulator — pure state machine, Clock injected
    features/               # pure feature-computation functions: per-tick OFI delta (stateless), Gini, skewness, percentile — OFI running sum lives in accumulator/
    consumer/               # Redis XREADGROUP loop, dedup filter, message dispatch
    cascade/                # timeframe cascade engine — pure, Clock injected
    writer/questdb/         # QuestDB ILP writer: WAL detection, upsert, retry
    writer/redis/           # candle + ob_features stream publisher, MAXLEN trim
    flush/                  # daily Parquet flush: QuestDB → Parquet → B2; flush_manifest write
    health/                 # /health /version /metrics HTTP server
    metrics/                # named Prometheus vars, Register(prometheus.Registerer) — zero internal imports
    backoff/                # exponential backoff: initial=1s, multiplier=2, max=60s, jitter=±20%
    testutil/               # MockClock, FakeRedis, FakeQuestDB, TickFixtureBuilder
  Makefile                  # test-l1, test-l2, test-l3, test-l4 targets
  Dockerfile                # multi-stage: Go build stage + minimal runtime
  go.mod
  go.sum
  .env.example
```

---

## Concurrency Model

One goroutine per `(exchange, symbol)`. Each goroutine owns its L2 book, accumulator, cascade accumulators, and OFI state — no shared mutable state across goroutines.

One `time.Ticker` lives in `cmd/candle/` and fires once per second. Its goroutine sends a `BarClose` signal to each symbol's channel in sequence. There is exactly one active `time.Ticker` for the entire process — not one per symbol.

The `BarClose` channel per symbol is buffered with **capacity 1**. The ticker goroutine uses a non-blocking send: if the previous `BarClose` hasn't been consumed yet (slow batch), the signal is dropped and `candle_bar_close_dropped_total` is incremented. The consumer goroutine processes `BarClose` in-order after draining the current `XREADGROUP` batch — no mutex needed.

A second ticker in `cmd/candle/` fires every `CANDLE_PARTIAL_PUBLISH_MS` (default 250ms) and sends a non-blocking `PartialPublish` signal to each symbol's channel (capacity 1, same pattern). The consumer goroutine handles both `BarClose` and `PartialPublish` cases in the same select loop. The 250ms ticker is owned by **story 8-2** (Redis candle stream publisher). The cascade engine `CurrentBar(tf)` read-only API is owned by **story 8-1** and is what the partial publish calls to read in-progress cascade state without blocking the consumer goroutine.

**SIGTERM shutdown (post-ACK semantics):** On SIGTERM: (1) flush current accumulator as partial (`is_partial=true`), (2) XACK all processed messages from the current batch, (3) close ILP connection, exit. The candle service uses post-ACK — NOT the pre-ACK pattern used by the aggregator. A crash before XACK causes redelivery; QuestDB WAL dedup prevents duplicate rows.

---

## Language-Specific Rules

### Interface Design — same rule as aggregator
Interfaces defined in the **consuming** package, not the implementing package:
- `consumer/` defines its own `StreamReader` interface
- `consumer/` defines `ILPWriter` — the interface for its downstream QuestDB writer dependency
- `accumulator/` defines `Clock interface { Now() time.Time }` — each package needing time defines its own compatible Clock
- Interface names: noun or noun+er — never `I`-prefix

### Package Dependency Direction
- Leaf nodes (zero internal imports): `symbol/`, `metrics/`, `backoff/`, `orderbook/`, `accumulator/`, `features/`, `cascade/`
- `config/` — only package that reads `os.Getenv`
- `consumer/` — wires orderbook, accumulator, cascade; imports writer/*
- `cmd/candle/main.go` — sole composition root

### Context Propagation
- `ctx context.Context` is the **first parameter** on every IO-touching function
- Pure functions (`orderbook.Apply()`, `accumulator.Apply()`, `features.OFIDelta()`) do NOT accept ctx
- Every `for-select` loop must have `case <-ctx.Done(): return` as a peer case, never nested

### Time Injection
- `time.Now()` and `time.Sleep()` banned outside `cmd/candle/` initialization
- `accumulator/` and `cascade/` each define their own `type Clock interface { Now() time.Time }`
- `time.Ticker` for second boundaries lives in `cmd/candle/` — sends on a typed channel
- Never call `time.Now()` inside `accumulator/`, `cascade/`, or `features/`

### Error Handling
- All errors crossing a package boundary: `fmt.Errorf("package: operation: %w", err)`
- No panic recovery inside helper functions — only at goroutine entry points

---

## Critical Behavioral Rules

### Accumulator Reset — Two Methods, Different Scopes

The accumulator has two reset methods with distinct scopes:

- **`Reset()`** — full reset. Called on gap events and snapshot events. Clears OHLCV, OFI, per-bar OB tracking, gap_count, AND the carry-forward OB state (`lastKnownBid`/`lastKnownAsk`/`lastKnownDepth`). Use when OB state is genuinely unknown (gap/snapshot = book wiped).
- **`BarReset()`** — bar-flush reset. Called by `accWriter.Flush()` after each 1-second bar write. Clears OHLCV, OFI, and per-bar OB tracking. Does NOT clear carry-forward state. The next bar's idle-second carry inherits from this bar's close.

`Reset()` MUST be called on every gap and snapshot event. `BarReset()` MUST be called after every bar flush (replaces the current `Reset()` call in `accWriter.Flush()`). Mixing them up corrupts OFI or idle-second carry-forward state.

### OFI State Location
OFI accumulator and prior-book-state are fields inside `accumulator/` — NOT computed in `features/`. `features.OFIDelta(prev, curr BestQuote)` is a pure, stateless function that computes a single per-tick OFI delta from before/after best quotes. It does NOT hold state between calls. All OFI accumulation (running sum) happens in `accumulator/`.

### OFI Formula (Cont et al. 2014)
For each tick, `accumulator/` computes the per-tick OFI delta:
- `Δ_bid` = change in best-bid quantity (0 if best-bid price dropped — the prior best bid was displaced by a worse one)
- `Δ_ask` = change in best-ask quantity (0 if best-ask price rose — the prior best ask was displaced by a worse one)
- per-tick OFI delta = `Δ_bid − Δ_ask`
- OFI accumulator += per-tick OFI delta on each tick
- Reset OFI accumulator and prior-book-state on every gap or snapshot event (call `Reset()`, not `BarReset()`).

**`ofi` and `ofi_l1` column semantics (resolved design decision):**
Both `ofi` and `ofi_l1` in `snapshot_1s` receive the same value: the L1 OFI running sum from `acc.ofiSum`. They are written identically from the same accumulator field. This is deliberate — full-book OFI (accumulating across all touched price levels) is deferred out of scope. A future migration can differentiate them when full-book OFI is implemented. Do NOT write different values for these two columns until that work is done.

### Tick Redelivery OFI Protection
`consumer/` maintains a per-symbol dedup set of processed Redis stream entry IDs within the current 1-second window. If a message ID is already in the set (XACK failed → redelivery by Redis), XACK it and skip without applying to the accumulator or order book. Clear the dedup set on each `BarClose`. This prevents OFI inflation from at-least-once redelivery — QuestDB WAL dedup protects the persisted row but not the in-flight accumulator.

### Zero-Size Delta = Level Removal
A delta with `size="0"` removes the price level from the order book. Never leave a stale level. This is the most common source of silent book divergence. If a zero-size delta arrives for a price level not in the book, silently ignore (no-op) — do NOT create the level. Log at TRACE only to avoid spam.

### Cold-Start Delta Buffering
If an update delta arrives before the first snapshot for a symbol, buffer it. The cold-start buffer is capped at `COLD_START_BUFFER_SIZE` entries (default 1000) per symbol; see env vars.

After the snapshot arrives: replay buffered messages in order. Replay deltas with `seq > snapshot.seq`. Gap markers in the buffer: if `gap_ts` is after the snapshot, replay them (they increment gap_count); if before the snapshot, discard.

**Exchange seq reset:** If `snapshot.seq < max(buffered_delta.seq) / 2` (heuristic for exchange sequence counter reset, e.g. Bybit resets to 0 after reconnect), treat all buffered deltas as stale, discard them, and emit a gap marker with `gap_cause=seq_reset`.

**Second snapshot during replay:** If a second snapshot arrives while replaying buffered deltas, stop the replay immediately, call `accumulator.Reset()`, apply the new snapshot, discard remaining buffered deltas, and emit a gap marker with `gap_cause=snapshot_superseded`.

**Buffer overflow:** If the buffer reaches `COLD_START_BUFFER_SIZE` before a snapshot arrives, discard all buffered deltas and emit a gap marker with `gap_cause=cold_start_buffer_overflow`. Do NOT use `gap_cause=external_disconnect` — that is reserved for aggregator-emitted gap markers for actual exchange disconnects.

### Upsert Semantics in QuestDB
`snapshot_1s` rows have deduplication key `(exchange, symbol, ts_second)`. A crash-before-XACK restart writes the same bar twice — that is correct and expected. The QuestDB WAL dedup prevents duplicate rows. Never use this as an error signal.

### Gap Attribution
`gap_count` for a bar is attributed to the second containing `gap_ts` from the gap marker — NOT the second the marker is consumed from the stream. A gap marker consumed in second N+1 but with `gap_ts` in second N increments second N's bar.

If `gap_ts` falls in a second whose bar has already been committed to QuestDB (late-arriving gap marker on restart), issue a QuestDB UPDATE to increment `gap_count` for that row. If the row does not exist (gap predates service start), discard and log WARN.

### Depth Snapshot Interface (Epic 6 architectural pattern)

OB depth fields (16 columns: L1/L2/top10/total at open and close) plus weighted prices require full-book access at specific points in time. The pattern:

1. `features.DepthSnapshot` struct — defined in `features/` to avoid import cycles. Contains `BidL1, AskL1, BidL2, AskL2, BidTop10, AskTop10, BidTotal, AskTotal, WeightedBidPrice, WeightedAskPrice float64`.
2. `features.ComputeDepthSnapshot(bids, asks map[string]string) features.DepthSnapshot` — pure function in `features/`. Takes the output of `ob.AllBids()`/`ob.AllAsks()`.
3. `acc.SetOpenDepth(d features.DepthSnapshot)` — called by `accWriter.Apply()` the first time `hasOpenQuote` transitions to true for a new bar.
4. `acc.SetCloseDepth(d features.DepthSnapshot)` — called by `accWriter.Flush()` before `CurrentBar()`.

The `accWriter` in `cmd/candle/` owns both `ob *orderbook.OrderBook` and `acc *accumulator.Accumulator`. It is the coordination point — the accumulator stays pure (no OB reference).

**`weighted_bid_price` / `weighted_ask_price`** are bar-close snapshots computed once at flush time. They live in `DepthSnapshot.WeightedBidPrice`/`WeightedAskPrice`. No `_open` suffix — book shape is represented at bar close only.

**`mid_price_close` is intentionally absent from the DDL.** It is always derivable as `(best_bid + best_ask) / 2` from the existing close-quote columns. Do NOT add a `mid_price_close` column or migration.

### Idle Second Field Semantics
For seconds with zero ticks but valid prior OB state, write a row with the following semantics:

**OB fields — carry from last-known state, do NOT write null:**
`best_bid_open`, `best_ask_open`, `best_bid`, `best_ask`, and all depth columns for both open and close: `bid_depth_l1_open`, `ask_depth_l1_open`, `bid_depth_l2_open`, `ask_depth_l2_open`, `bid_depth_top10_open`, `ask_depth_top10_open`, `bid_depth_total_open`, `ask_depth_total_open`, `bid_depth_l1_close`, `ask_depth_l1_close`, `bid_depth_l2_close`, `ask_depth_l2_close`, `bid_depth_top10_close`, `ask_depth_top10_close`, `bid_depth_total_close`, `ask_depth_total_close`.

**Trade and feature fields — write null:**
`open`, `high`, `low`, `close`, `volume`, `quote_volume`, `trade_count`, `twap`, `block_buy_volume`, `block_sell_volume`, and all microstructure features. No trades = no price action.

CS-FR18's "null row for empty seconds" means trade/feature fields are null. OB state fields are carried. These are NOT contradictory — they apply to different column groups.

### Partial Bar on Snapshot Flush
When `event_type=snapshot` arrives mid-second, flush the current accumulator with `is_partial=true` before reinitializing. Downstream consumers must handle `is_partial=true` — it is a valid bar, not an error.

If the current accumulator has zero ticks when a snapshot arrives (snapshot at the very start of a second), skip the partial flush — do not write a zero-tick partial bar.

`is_partial` and `is_complete` are **different fields on different stores** — not interchangeable:
- `is_partial` — boolean column in QuestDB `snapshot_1s` table
- `is_complete` — field in Redis candle stream messages (`candles:{exchange}:{symbol}:{tf}`)

Both must be set correctly on every bar close.

### Idle Symbol Skip on Partial Publish
For the 250ms partial bar publish cadence: if a symbol has no updates in the current 250ms window, do NOT republish the last-known state. Downstream consumers treat absence as no-update. Never send a stale repeated message.

### Block Trade Cold-Start
Write `null` for `block_buy_volume` / `block_sell_volume` until `BLOCK_TRADE_MIN_SAMPLE` (default 100) trades have been seen for the symbol.

The block trade threshold is the rolling 99th-percentile of trade sizes over the last `BLOCK_TRADE_WINDOW` (default 1000) trades per symbol. A trade is classified as a block trade if its size exceeds this threshold. Never compute the percentile threshold from fewer than `BLOCK_TRADE_MIN_SAMPLE` observations.

### Zero-Volume Trade Discard
Trades with `size="0"` (parsed as zero) are XACK'd with a DEBUG log and not forwarded to the accumulator. Zero-volume trades carry no market information and corrupt TWAP if included. Some exchanges send these as correction signals.

### Minimum Sample Guards (return null, never NaN)
- `trade_clustering` (Gini): requires `trade_count >= 2`; return null otherwise
- `realized_skewness`: requires `>= 3` price observations; return null otherwise
- `trade_sign_autocorr`: requires `>= 2` trades; return null otherwise
- `weighted_bid_price` / `weighted_ask_price`: if total volume on either side is zero, return null — never divide by zero

### Cascade Accumulator Persistence
On each 1s bar close, write accumulator state to Redis (`candle:acc:{exchange}:{symbol}:{tf}`) as a **HASH**. Required fields: `open_ts`, `open`, `high`, `low`, `volume`, `quote_volume`, `trade_count`, `bar_count` (number of 1s bars merged so far), `gap_count`. No TTL — key persists until next write. Schema evolution: read with HGETALL; any absent field is treated as 0/zero-value (never fail on a missing field).

**Startup reconstruction sequence (run synchronously before consumer goroutines start):**
1. For each `(exchange, symbol, tf)`: read Redis HASH `candle:acc:{exchange}:{symbol}:{tf}`
2. If HASH is present and all 9 fields are readable: restore cascade accumulator state from HASH
3. Run completeness check: `SELECT COUNT(*) FROM snapshot_1s WHERE exchange=? AND symbol=? AND ts_second >= ? AND ts_second < now_boundary` (COUNT only — no row replay)
4. If count < (expected_bar_count × 0.95): emit gap marker `gap_cause=reconstruction_incomplete`, override `gap_count` in restored state
5. If HASH absent or malformed: start empty, emit gap marker with `gap_cause=reconstruction_incomplete`, skip QuestDB check

**No row replay from QuestDB.** The Redis HASH is the only reconstruction source. The QuestDB query is a single COUNT(*) for completeness validation only.

**Startup ordering in `cmd/candle/main.go`:** (1) run migrations → (2) restore all cascade accumulators from Redis HASHes (synchronously, all symbols) → (3) run QuestDB completeness checks → (4) start consumer goroutines. Consumer goroutines MUST NOT start until step 3 is complete for all symbols.

If QuestDB unavailable at startup, skip step 3, start empty, emit gap markers.

**Write failure:** use `backoff/` with max 3 retries, initial=50ms, max=2s. If all retries fail, log ERROR, increment `candle_cascade_state_write_failure_total{exchange,symbol}`, and continue — never block the bar-close flow on a cascade Redis write failure.

### Cascade Nil Aggregation Rules
The cascade accumulator is OHLCV-only (9 fields). Microstructure fields (TWAP, OFI, etc.) are 1s-only — never aggregated to higher TFs. When folding a 1s `Bar` into a higher-TF cascade accumulator:
- `open_ts`: set once on first 1s bar with any data; never overwritten
- `open`: set once on first 1s bar where `open` is non-nil; never overwritten
- `high`: if 1s bar's `high` is nil → skip (no update to cascade high)
- `low`: if 1s bar's `low` is nil → skip (no update to cascade low)
- `close`: if non-nil → update cascade close; if nil → carry last known close (no update)
- `volume`, `quote_volume`, `trade_count`, `gap_count`: nil = +0
- `bar_count`: always +1 regardless of 1s bar content (counts every second, including no-trade seconds)

### Timeframe Boundaries
All timeframe calculations use UTC:
- 1m, 5m, 15m, 1h, 4h: aligned to UTC clock (e.g., 4h bars start at 00:00, 04:00, 08:00 UTC)
- 1d: midnight UTC
- 1w: **Monday 00:00:00 UTC** — use `time.Weekday() == time.Monday` check, not `time.Truncate` (which gives wrong results for weekly periods)

The 1w bar closes to `candles:{exchange}:{symbol}:1w` (partial + close stream) and `candles:close:{exchange}:{symbol}:1w` (close-only stream). There is no duplicate alias key.

### Non-Atomic Redis Publish
`candles:{exchange}:{symbol}:{tf}`, `candles:close:{exchange}:{symbol}:{tf}`, and `ob_features:{exchange}:{symbol}` are published as separate `XADD` calls on each bar close. If any fails after backoff retries, log ERROR, increment `candle_redis_publish_failure_total`, and continue — do not stall the consumer loop.

### Daily Flush Catch-Up
On startup, read `last_flush_date` from Redis. If the key is **absent**, query `flush_manifest` in QuestDB for the most recent row where `success = true` and use its `date_flushed` as `last_flush_date`. Only if `flush_manifest` is also empty (true first run) start from the current day. This prevents silent data loss when Redis is reset in production — never silently skip catch-up on Redis key absence.

If more than 1 day behind, flush all missing days sequentially before entering normal operation. Before flushing a date, query `flush_manifest` in QuestDB for an existing row with `date_flushed = target_date AND success = true`. If found, skip and advance `last_flush_date` in Redis — catch-up must be idempotent against Redis loss.

Wrap the upload goroutine's context with `context.WithTimeout` of 4 hours. On context cancellation or upload failure, call `s3.AbortMultipartUpload` with a **fresh (non-cancelled) context** to prevent leaked incomplete parts in B2. The `aws-sdk-go-v2` `manager.Uploader` handles abort automatically if used — otherwise call abort explicitly on any non-nil upload ID.

### Alert Write Fallback
If the Redis write to `alerts:flush_failure` fails after a flush failure, increment `candle_flush_alert_failure_total` Prometheus counter. Flush failure must be observable even when Redis is down.

---

## Naming Conventions

- **Go code:** PascalCase exported, camelCase unexported, `ErrXxx` sentinel errors, no `I`-prefix interfaces
- **Prometheus metrics prefix:** `candle_` — NOT `aggregator_`
  - Pattern: `candle_{noun}_{unit}_total` for counters
  - Required metrics:
    - `candle_bars_total{exchange,symbol}`
    - `candle_consumer_lag{exchange,symbol}` — clamp to `max(0, calculated_lag)`; log WARN once per symbol per hour if a negative value is observed
    - `candle_questdb_write_latency_ms`
    - `candle_gap_count_total{exchange,symbol}`
    - `candle_flush_success_total`
    - `candle_flush_failure_total`
    - `candle_flush_alert_failure_total`
    - `candle_bar_close_dropped_total{exchange,symbol}` — ticker signal dropped due to slow batch
    - `candle_redis_publish_failure_total{exchange,symbol}` — XADD failure on candle/ob_features streams
    - `candle_cascade_state_write_failure_total{exchange,symbol}` — cascade acc Redis write failure
  - **Metric initialization:** At startup, after parsing `SYMBOLS_*` env vars, initialize all per-symbol gauge and counter metrics to 0 for every configured symbol. This ensures configured-but-silent symbols appear in `/metrics` output — absence-of-data must be distinguishable from never-configured.
- **slog fields:** `snake_case`; use `error` not `err`
- **QuestDB columns:** `snake_case` — table `snapshot_1s`, `flush_manifest`
- **Redis fields:** `snake_case`, string prices, int64 timestamps
- **Env vars:** `UPPER_SNAKE_CASE`
  - `REDIS_URL`, `QUESTDB_ILP_ADDR`
  - `SYMBOLS_KUCOIN`, `SYMBOLS_BYBIT`
  - `B2_ACCESS_KEY_ID`, `B2_SECRET_ACCESS_KEY`, `B2_BUCKET_NAME`, `B2_ENDPOINT`
  - `BLOCK_TRADE_WINDOW` (default 1000), `BLOCK_TRADE_MIN_SAMPLE` (default 100)
  - `LOG_LEVEL`
  - `CANDLE_STREAM_MAXLEN` (default 10000) — MAXLEN for `candles:{exchange}:{symbol}:{tf}` partial+close stream
  - `CANDLE_CLOSE_STREAM_MAXLEN` (default 500) — MAXLEN for `candles:close:{exchange}:{symbol}:{tf}` close-only stream
  - `CANDLE_SERVICE_PORT` (default 8081)
  - `CANDLE_PARTIAL_PUBLISH_MS` (default 250)
  - `CANDLE_CONSUMER_GROUP` — Redis consumer group name (default `candle-service`)
  - `COLD_START_BUFFER_SIZE` — per-symbol cold-start delta buffer cap (default 10,000 — ~10s of ticks at peak rate)
  - `QUESTDB_ILP_FLUSH_MS` — ILP batch flush interval in milliseconds (default 500)

### Serialization
- Price and size: **always string** — never `float64` in Redis, QuestDB, or JSON
- Timestamps: Unix milliseconds as `int64`

---

## Test Architecture (L1–L4, no L5)

| Layer | Build Tag | Scope | CI |
|---|---|---|---|
| L1 | *(none)* | `orderbook/`, `accumulator/`, `features/`, `cascade/`, `symbol/`, `backoff/`, `metrics/` — zero IO, MockClock injected | yes |
| L2 | `//go:build l2` | `consumer/`, `writer/*/` with FakeRedis, FakeQuestDB | yes |
| L3 | `//go:build l3` | Mock Redis server in-process; full consumer loop | local |
| L4 | `//go:build l4` | Toxiproxy + testcontainer QuestDB | local |

**No L5** — this service does not connect to exchanges. L4 is the highest test layer.

### MockClock is mandatory for L1 tests
`accumulator/` and `cascade/` behavior at second boundaries MUST be tested deterministically using MockClock. Never use `time.Sleep` in tests. `testutil/MockClock` satisfies all Clock interfaces via structural typing.

### OFI L1 test boundary
`features.OFIDelta(prev, curr BestQuote)` is a pure function — test it in `features/` L1 tests with explicit before/after quote inputs. OFI accumulation (running sum, reset behavior) is tested via `accumulator/` L1 tests using MockClock and TickFixtureBuilder. Do not test accumulator state by calling `features.OFIDelta` directly from accumulator tests.

### TickFixtureBuilder
`testutil/TickFixtureBuilder` provides deterministic tick sequences including: gap events, snapshot events, zero-size deltas, empty-second scenarios, block trade warm-up sequences, mid-second snapshot flush scenarios, zero-volume trades, and cold-start buffer overflow sequences.

### Test file placement
Default: `package foo_test` (black-box). Use `package foo` (white-box) only when testing unexported invariants — document why at file top.

---

## Credential Sanitization

Identical dual strategy to the aggregator:
1. **Sealed config type** — `config.Credential` has no `fmt.Stringer` or `error` implementation; raw string inaccessible after construction
2. **slog handler wrapper** — wired in `cmd/candle/main.go` first, before any other package logs; redacts `B2_ACCESS_KEY_ID`, `B2_SECRET_ACCESS_KEY`, Redis URL (if contains password), QuestDB addr pattern from all log output at every level including DEBUG

---

## QuestDB Schema

### Migration Runner
The service runs QuestDB migrations on startup (Story 5-1) before the Redis consumer starts. Migration files live in `candle-service/migrations/` as numbered SQL files (`001_snapshot_1s.sql`, `002_flush_manifest.sql`, …). A `schema_migrations` table tracks applied migrations with SHA-256 checksums. The service refuses to start if any migration fails or a previously-applied file has changed. Migrations are **additive only** — `ALTER TABLE ADD COLUMN` is permitted; `DROP COLUMN` and `RENAME COLUMN` are not supported by QuestDB and must never appear.

### `snapshot_1s` table
Full 67-column schema including `best_bid_open` and `best_ask_open`. All non-identity columns nullable. DDL is migration file `001_snapshot_1s.sql` applied by the migration runner (Story 5-1) before any accumulator code.
Upsert key: `(exchange, symbol, ts_second)` — enforced via `DEDUP UPSERT KEYS(ts, exchange, symbol)` in the DDL. ILP is append-only; deduplication happens in the WAL layer.

### `flush_manifest` table
Fields: `ts_flush timestamp, exchange symbol, date_flushed date, row_count long, b2_path string, success boolean, error_msg string nullable, duration_ms long`
DDL is migration file `002_flush_manifest.sql`, applied by the migration runner on startup before any flush code (Epic 9, Story 9-1).

---

## Redis Stream Contracts

### Consuming: `ticks:{exchange}:{symbol}`
- Consumer group name: `CANDLE_CONSUMER_GROUP` env var (default `candle-service`); one group per service instance; resume from last-ack'd position on restart
- On first connect (new group): start from `$` — if stream is near MAXLEN (>45,000 entries), emit a gap marker with `gap_cause=stream_overflow`
- Dedup key for gap markers: `(exchange, symbol, seq_before, seq_after, gap_cause)` — all five fields required
- Unknown message type: log WARN, XACK, continue — never stall

### Publishing: `candles:{exchange}:{symbol}:{tf}`
- MAXLEN: 10,000 (configurable via `CANDLE_STREAM_MAXLEN`)
- Partial bars: `is_complete: "false"`; closed bars: `is_complete: "true"`
- **No weekly alias.** The `candles:1w:{exchange}:{symbol}` alias is NOT published. Use `candles:close:{exchange}:{symbol}:1w` for weekly close signals (see below).
- Full field specification: see `docs/data-contract.md` section 1.3

### Publishing: `candles:close:{exchange}:{symbol}:{tf}`
- Close events only (`is_complete: "true"`); never partial events
- MAXLEN: 500 (configurable via `CANDLE_CLOSE_STREAM_MAXLEN`)
- Published simultaneously with the close event in `candles:{exchange}:{symbol}:{tf}` on each bar close
- Exists for all timeframes (1m, 5m, 15m, 1h, 4h, 1d, 1w)
- Bots that need bar-completion events subscribe to this stream. Bots that need live partial state read `candles:{exchange}:{symbol}:{tf}`.
- Rationale: at 4 partial publishes/s, a 1h bar generates 14,400 entries — MAXLEN=10,000 would trim the close event 41 minutes after it was written. The close stream is always ≤500 entries per TF per symbol, so close events are never trimmed before the next bar closes.

### Publishing: `ob_features:{exchange}:{symbol}`
- MAXLEN: 10,000 (same `CANDLE_STREAM_MAXLEN`)
- Published on each 1s bar close, from the **closed `Bar` struct** (after `CurrentBar()`, before `BarReset()`)
- Flush sequence in `accWriter.Flush()`: (1) `acc.SetCloseDepth()`, (2) `bar := acc.CurrentBar()`, (3) write bar to QuestDB, (4) publish to `candles:` and `candles:close:` streams, (5) publish to `ob_features:` from `bar`, (6) `acc.BarReset()`. ob_features MUST NOT read from live OB state after `BarReset()`.
- Full field specification: see `docs/data-contract.md` section 1.4

### Cascade persistence: `candle:acc:{exchange}:{symbol}:{tf}`
- Data type: **HASH**
- Required fields: `open_ts`, `open`, `high`, `low`, `volume`, `quote_volume`, `trade_count`, `bar_count`, `gap_count`
- No TTL — key persists until overwritten
- Written on each 1s bar close; read on startup for reconstruction
- Absent or malformed key on startup: treat as fresh (same as QuestDB-unavailable path)
- Schema evolution: read with HGETALL; any absent field is treated as 0/zero-value

### Flush tracking: `last_flush_date` (Redis string key)
### Alerts: `alerts:flush_failure` (Redis stream)

---

## docker-compose Integration

```yaml
candle-service:
  image: ghcr.io/mrqdt/magnum-opus/candle-service:${VERSION}
  env_file: .env
  mem_limit: 400m
  cpus: 1.0
  stop_grace_period: 15s
  depends_on: [redis, questdb]
  ports: ["${CANDLE_SERVICE_PORT:-8081}:8081"]
```

### Blue/Green Deployment (Story 9-4)

Two docker-compose profiles: `candle-blue` (port 8081) and `candle-green` (port 8082). `CANDLE_SLOT` env var (`blue` or `green`) is included in `/health` response and all structured log lines.

**`/health` response fields:** `status`, `shadow_lag` (max messages-behind-stream-tip across all configured symbols during shadow XREAD warmup — 0 after all symbols have promoted), `shadow_lag_symbol` (name of the symbol with the highest lag when `shadow_lag > 0`; omitted when `shadow_lag = 0`), `consumer_lag_max`, `slot`, `version`. The deploy script gates promotion on `shadow_lag=0`. If a symbol is permanently lagged and cannot catch up, the operator must remove it from `SYMBOLS_*` and redeploy — there is no automatic override.

**Graceful shutdown on SIGTERM:** flush current in-flight 1s accumulator (`is_partial=true`), XACK all pending Redis messages, close ILP connection. Timeout: `SHUTDOWN_TIMEOUT_S` (default 10s).

**Shadow XREAD warmup:** on startup the new slot reads the live stream via `XREAD` (no consumer group) building OB state and rolling windows. Tracks `lastShadowID` — the last processed message ID. At promotion: switch to `XREADGROUP` starting from `lastShadowID` (NOT `>`), call `XAUTOCLAIM` for pending messages, discard in-memory OHLCV accumulator and reconstruct from QuestDB last `is_partial=true` row. This ensures every message is processed exactly once at the XREAD→XREADGROUP boundary.

**Rollback:** if new slot fails post-promotion, restart old slot. Redis state (cascade accumulators, block trade window, gap dedup ZSET) is intact. `XAUTOCLAIM`-on-startup recovers orphaned messages. Restart IS rollback.

---

## Forbidden Anti-Patterns

- `time.Now()` or `time.Sleep()` outside `cmd/candle/` initialization
- `init()` with mutable state; package-level `var` for shared mutable state
- Closing a channel from the receiver side
- `float64` for price/size in any serialized format
- `sync.Mutex` on `OrderBook` or `accumulator` — single goroutine ownership model
- Panic recovery inside helper functions
- `I`-prefix interfaces
- `prometheus.MustRegister`, `prometheus.DefaultRegisterer`, `promhttp.Handler()`
- `gorilla/websocket`, `nhooyr.io/websocket`, `github.com/go-redis/go-redis/v8`
- `os.Getenv` outside `internal/config/`
- `:latest` or `:8.x` QuestDB image tags
- Calling `accumulator.Apply()` after a gap/snapshot without first calling `accumulator.Reset()`
- Using `gap_ts` of a gap marker for the consumption second — always attribute to the second containing `gap_ts`
- Computing OFI state in `features/` — OFI running sum lives in `accumulator/`
- One `time.Ticker` per symbol — there is exactly one shared ticker in `cmd/candle/` that fans out
- Unbounded cold-start delta buffer (use `COLD_START_BUFFER_SIZE`, default 1000)
- `gap_cause=external_disconnect` for cold-start buffer overflow — use `cold_start_buffer_overflow`
- Forwarding zero-volume trades (`size="0"`) to the accumulator
- Publishing `candles:1w:{exchange}:{symbol}` as a weekly alias — use `candles:close:{exchange}:{symbol}:1w` instead
- Publishing `ob_features` from live OB state after `BarReset()` — must come from the closed `Bar` struct before `BarReset()`
- Starting consumer goroutines before cascade reconstruction is complete for all symbols
- B2 multipart upload without calling `AbortMultipartUpload` on context cancellation
- Re-flushing a date without checking `flush_manifest` for an existing `success=true` row

---

## Implementation Sequence

1. Go project setup (Story 5-1) — `candle-service/` module, `go.mod`, Dockerfile, docker-compose blue/green profiles
2. Migration runner (Story 5-2) — `candle-service/migrations/`, `schema_migrations` table, SHA-256 checksum enforcement
3. `snapshot_1s` CREATE TABLE DDL (Story 5-3) — migration file `001_snapshot_1s.sql`, applied by migration runner
4. `internal/config/` + `internal/symbol/` + `.env.example`
5. `internal/orderbook/` — pure L2 book (can import from aggregator's implementation as reference, but must be its own package)
6. `internal/accumulator/` + `internal/features/` — pure, L1 tests with MockClock first
7. `internal/cascade/` — pure, L1 tests with MockClock (Story 8-1)
8. `internal/consumer/` — Redis XREADGROUP, dedup, dispatch
9. `internal/writer/questdb/` + `internal/writer/redis/` (story 8-2: writer/redis adds candle streams + 250ms ticker)
10. `internal/metrics/` + `internal/health/`
11. `cmd/candle/main.go` — composition root; startup order: migrations → cascade reconstruction (all symbols) → completeness checks → start consumer goroutines; 1s and 250ms tickers start after consumer goroutines are running
12. `internal/flush/` (Epic 9) — after QuestDB writes are stable

---

## Usage Guidelines

**For AI agents:** Read this file before writing any code. The aggregator's project-context.md covers a different service — rules there (WebSocket handling, exchange adapters, Bybit multiplexer) do not apply here. When in doubt, prefer the more restrictive interpretation. Flag any rule that conflicts with a framework or library default.

_Last updated: 2026-05-08 (Epic 8 adversarial review resolutions applied)_
