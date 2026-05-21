# Chapter 03 — Candle Service

**30-second summary:** The candle service reads raw tick streams from Redis, runs each
tick through a pure accumulator that builds 67-feature 1-second bars, folds those bars
through a cascade engine that maintains 7 timeframes (1m through 1w), and writes the
results to QuestDB and Redis. It deploys blue/green with a shadow warmup phase.

All source lives under `candle-service/`.

---

## 1. Package Structure

```
candle-service/
├── cmd/candle/main.go              Entry point + accWriter + startup
└── internal/
    ├── accumulator/accumulator.go  Pure 1-second bar builder (67 fields)
    ├── cascade/cascade.go          7-timeframe fold engine (1m → 1w)
    ├── consumer/
    │   ├── consumer.go             XREADGROUP loop + blue-green shadow
    │   ├── messages.go             Redis stream entry → Tick struct
    │   └── obadapter.go            Order book state for OB features
    ├── features/                   Feature computation modules
    │   ├── features.go             Core OHLCV + OFI aggregation
    │   ├── footprint.go            Footprint chart data (bid/ask at each price)
    │   ├── depth.go                L1/L2/top-10/total depth calculations
    │   ├── imbalance.go            Order flow imbalance
    │   ├── divergence.go           Price/volume divergence signals
    │   └── auction.go              Auction-style market signals
    ├── flusher/
    │   ├── flusher.go              Throttled flush coordinator
    │   └── row.go                  Bar → QuestDB ILP row serializer
    ├── writer/
    │   ├── questdb/writer.go       ILP write to snapshot_1s / snapshot_1m / snapshot_15m
    │   ├── redis/publisher.go      XADD candles:close + XADD candles:ob
    │   ├── pubsub/publisher.go     PUBLISH candles1s:{ex}:{sym}
    │   └── heatmap/writer.go       ZADD heatmap data for OB depth charts
    ├── cascade/cascade.go          Multi-TF accumulation
    ├── migrator/migrator.go        QuestDB DDL migrations on startup
    └── health/health.go            Health check handler
```

---

## 2. Startup Sequence

[`candle-service/cmd/candle/main.go`](../../candle-service/cmd/candle/main.go) — ~950 lines.

**Phase 1 — Build entries (cascade reconstruction):**

1. Load config from environment.
2. Run QuestDB DDL migrations via `migrator.go` (idempotent).
3. For each symbol, query QuestDB `snapshot_1s` for the last N rows.
4. Replay those rows back through the `Cascade` engine to reconstruct in-memory
   state for each of the 7 timeframes.
5. This means if the service restarts mid-bar, the 1-hour bar it was building
   is reconstructed from persisted 1-second data — no data is lost.

**Phase 2 — Start goroutines:**

6. One `accWriter` goroutine per symbol.
7. One `Consumer` goroutine per symbol (XREADGROUP loop).
8. Global 1-second ticker goroutine — at each second boundary, signals all
   `accWriter` goroutines to flush their current bar.
9. 250ms partial-publish goroutine — publishes an in-progress bar snapshot
   mid-second for low-latency consumers.
10. Lag-monitoring goroutine — polls Redis stream lag.

**Phase 3 — Blue-green:**

11. If `CANDLE_SHADOW_MODE=true`, the consumer reads streams but the writers
    are no-ops. The slot warms up its in-memory state.
12. `POST /promote` switches the slot from shadow to live: activates writers,
    updates `CANDLE_SLOT`.

> ⚠ **Constraint:** The consumer `XACK`s before processing, not after. Unacked
> messages on crash are recovered via `XAUTOCLAIM` on next start.

---

## 3. The `accWriter` — Per-Symbol Goroutine

Each symbol runs one `accWriter` goroutine that owns:
- An `Accumulator` instance (1-second bar state)
- A `Cascade` instance (7-TF fold state)
- A `Flusher` reference (writes to QuestDB + Redis)

The flow per tick arrival:

```
Consumer reads tick from Redis stream
    ↓
accWriter channel receives the tick
    ↓
accumulator.Feed(tick)    ← mutates 1-second bar state
    ↓
(on second boundary)
bar := accumulator.Flush()   ← emits complete Bar, resets state
    ↓
cascade.Fold(bar)         ← updates all 7 TF accumulators
    ↓
flusher.Write(bar, cascadeBars)  ← QuestDB ILP + Redis XADD + pub/sub
```

---

## 4. The Accumulator — 67-Feature Bar

[`candle-service/internal/accumulator/accumulator.go`](../../candle-service/internal/accumulator/accumulator.go)

The accumulator is **pure** (zero IO, injected clock). Single-goroutine ownership.

A completed `Bar` has these field groups:

| Group | Fields | Source |
|-------|--------|--------|
| Time | `TsSecMs`, `Exchange`, `Symbol` | Tick metadata |
| OHLCV | `Open`, `High`, `Low`, `Close`, `Volume`, `QuoteVolume`, `TradeCount`, `TWAP` | Trade ticks |
| OB best quotes | `BestBidOpen`, `BestAskOpen`, `BestBid`, `BestAsk` | Quote ticks |
| Mid-price path | `MidPriceOpen`, `MidPriceHigh`, `MidPriceLow`, `VWMP` | Mid of bid/ask |
| Spread | `SpreadHigh`, `SpreadLow`, `SpreadMean`, `EffectiveSpread` | Ask−Bid |
| Depth (open) | 8 fields: L1/L2/top-10/total bid & ask | OB at second open |
| Depth (close) | 8 fields: same at second close | OB at flush time |
| Book shape | `WeightedBidPrice`, `WeightedAskPrice` | VWAP of book |
| OFI | `OFI`, `OFICumulative`, `OFIPerTrade` | Order Flow Imbalance |
| Trade flow | `BuyVolume`, `SellVolume`, `BuyTradeCount`, `SellTradeCount` | Trade side |
| Quote stuffing | `QuoteStuffRatio` | (arrivals+cancels)/trade_count |
| Consecutive runs | `MaxConsecutiveRun` | Max run of same-side trades |
| Volume profile | `POCPrice`, `ValueAreaHigh`, `ValueAreaLow` | Trade price distribution |
| Footprint | `FootprintJSON` | JSON: {price: {bid_vol, ask_vol}} |
| Signals | `AbsorptionDetected`, `IcebergDetected` | Derived boolean signals |
| Single prints | `SinglePrintLevelsJSON` | JSON list of thin price levels |
| Realized vol | `RealizedVol` | Std-dev of mid-price returns this second |
| Divergence | `PriceDivergence`, `VolumeDivergence` | Between halves of second |

**Nil semantics:** pointer fields (`*float64`) are `nil` when no data arrived for
that field group this second. The ILP writer sends them as QuestDB NULL.

---

## 5. The Cascade Engine — 7 Timeframes

[`candle-service/internal/cascade/cascade.go`](../../candle-service/internal/cascade/cascade.go)

**Pure:** zero IO, injected clock, single-goroutine ownership.

Timeframes: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`, `1w`.

Each TF maintains a `cascadeAcc` which accumulates:
- `open`, `high`, `low`, `close` (OHLC from 1s bars)
- `volume`, `quoteVol`, `tradeCount`
- `barCount` — number of 1s bars folded in
- `gapCount` — 1s bars that had no trades (empty bars still advance the cascade)
- `buyVolume`, `sellVolume`

`Fold(bar accumulator.Bar)` returns a `[]Bar` containing every TF that completed
in this second (e.g., if the 1m boundary falls on this second, a complete 1m bar
is returned along with any higher-TF bars that also completed).

**Completion logic:** A bar completes when `floor(currentTs / tfSeconds) != floor(prevTs / tfSeconds)`.

---

## 6. Consumer — XREADGROUP Loop

[`candle-service/internal/consumer/consumer.go`](../../candle-service/internal/consumer/consumer.go)

The consumer runs `XREADGROUP GROUP candle-service {hostname}-{pid} STREAMS ticks:{ex}:{sym} > COUNT 100 BLOCK 1000`.

Key behaviors:
- **ACK-before-process:** `XACK` is called immediately after reading (before the
  tick is handed to the accumulator). This means crashes may miss the last few
  ticks, but the cascade reconstruction on startup (phase 1 of startup) compensates.
- **XAUTOCLAIM on startup:** Before entering the read loop, the consumer calls
  `XAUTOCLAIM` with a 30-second idle threshold to reclaim any messages that were
  delivered but never ACKed by a previous crashed instance.
- **Gap deduplication:** Gaps reported by the exchange are tracked in a Redis ZSET
  to avoid processing the same gap twice (e.g., if two consumers both detect it).
- **Shadow mode (blue-green):** When `CANDLE_SHADOW_MODE=true`, the consumer uses
  plain `XREAD` (not `XREADGROUP`) to read from the stream without advancing
  the consumer group's ACK position. This allows the shadow slot to warm up its
  cascade state without interfering with the active slot.
- **Promotion:** `POST /promote` atomically:
  1. Switches from shadow `XREAD` to live `XREADGROUP`.
  2. Activates the QuestDB and Redis writers.
  3. Updates `CANDLE_SLOT`.

---

## 7. Flusher and Writers

### Flusher [`candle-service/internal/flusher/flusher.go`](../../candle-service/internal/flusher/flusher.go)

Throttles writes to avoid flooding QuestDB. Batches bars within a 100ms window
and flushes them together as a single ILP transaction (one TCP connection open/close).

### QuestDB Writer [`writer/questdb/writer.go`](../../candle-service/internal/writer/questdb/writer.go)

Writes to three tables:
- `snapshot_1s` — every 1-second bar with all 67 features.
- `snapshot_1m` — 1-minute bars from the cascade.
- `snapshot_15m` — 15-minute bars from the cascade.

Uses QuestDB ILP TCP (fire-and-forget). Row serialization is in
[`flusher/row.go`](../../candle-service/internal/flusher/row.go).

### Redis Writers

- **Stream publisher** [`writer/redis/publisher.go`](../../candle-service/internal/writer/redis/publisher.go):
  - `XADD candles:close:{ex}:{sym}:{tf} *` for each completed bar.
  - `XADD candles:ob:{ex}:{sym} *` for the OB feature snapshot.
- **Pub/sub publisher** [`writer/pubsub/publisher.go`](../../candle-service/internal/writer/pubsub/publisher.go):
  - `PUBLISH candles1s:{ex}:{sym} <json>` for real-time 1s bar delivery to the gateway.
- **Heatmap writer** [`writer/heatmap/writer.go`](../../candle-service/internal/writer/heatmap/writer.go):
  - `ZADD` price-level data for the dashboard OB depth heatmap.

---

## 8. Blue-Green Deployment

Two slots are always running: `candle-blue` (port 8081) and `candle-green` (port 8082).

Deployment flow (handled by [`scripts/deploy-candle.sh`](../../scripts/deploy-candle.sh)):

1. Build the new image for the inactive slot.
2. Start it with `CANDLE_SHADOW_MODE=true` — it warms up its cascade state by
   reading the tick stream, but writes nothing.
3. Wait for the health check to pass (cascade state reconstructed, OB warm).
4. `POST /promote` to the new slot — it switches to live write mode.
5. Stop the old slot.
6. Update `CANDLE_SLOT` in `.env.deploy` for the next deploy.

Both slots share the same Redis streams and QuestDB tables. Only one slot writes
at a time — the promotion is atomic within the slot itself.

---

## 9. Health and Metrics

`/health` reports `{"status":"ok","slot":"blue","mode":"live"}`.

Key Prometheus metrics:
- `candle_bars_total{exchange, symbol, tf}` — bars flushed
- `candle_consumer_lag{exchange, symbol}` — stream lag in messages
- `candle_flush_duration_seconds` — ILP write latency
- `candle_gaps_total{exchange, symbol}` — gap events
- `candle_accumulator_empty_seconds_total{exchange, symbol}` — seconds with no trades

---

## 10. Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `SYMBOLS` | (required) | `exchange:SYMBOL` pairs |
| `REDIS_ADDR` | `redis:6379` | Redis connection |
| `QUESTDB_ILP_ADDR` | `questdb:9009` | QuestDB ILP TCP |
| `QUESTDB_HTTP_ADDR` | `http://questdb:9000` | QuestDB HTTP (for migration + reconstruction) |
| `CANDLE_SLOT` | `blue` | `blue` or `green` |
| `CANDLE_SHADOW_MODE` | `false` | Start in shadow (warmup) mode |
| `CASCADE_LOOKBACK` | `3600` | Seconds of history to replay on startup |
| `PARTIAL_PUBLISH_INTERVAL_MS` | `250` | Mid-second partial bar publish interval |
| `LOG_LEVEL` | `info` | slog level |
