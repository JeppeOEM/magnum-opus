# Chapter 02 — Aggregator

**30-second summary:** The aggregator connects to KuCoin and Bybit WebSocket feeds,
maintains a per-symbol L2 order book, detects sequence-number gaps, and emits every
tick to three destinations: a Redis stream, QuestDB, and a Redis pub/sub channel.

All source lives under `aggregator/`.

---

## 1. Startup Sequence

[`aggregator/cmd/aggregator/main.go`](../../aggregator/cmd/aggregator/main.go) runs a 13-step startup:

```
1  Load config (env vars via aggregator/internal/config/config.go)
2  Set up structured logging (slog)
3  Connect to Redis
4  Connect to QuestDB ILP
5  Start /health + /metrics HTTP server (port 8080)
6  Build exchange drivers (KuCoin, Bybit) from SYMBOLS config
7  Build per-symbol coordinator Workers
8  Start Coordinator (manages all Workers)
9  Fetch initial order book snapshots via REST (multiSnapshotFetcher)
10 Wait for startup gate (all symbols have at least one snapshot)
11 Start gap detection
12 Enter main event loop
13 Graceful shutdown on SIGTERM
```

The startup gate (`startupGate`) blocks the main loop until every symbol has received
its first REST snapshot. This prevents publishing stale/partial order books during
the warm-up period.

---

## 2. Package Structure

```
aggregator/
├── cmd/aggregator/main.go          Entry point
└── internal/
    ├── coordinator/
    │   ├── coordinator.go          Coordinator: owns all Workers, routes ticks
    │   ├── symbol.go               Worker: per-symbol state + goroutine
    │   ├── snapshot.go             multiSnapshotFetcher (REST snapshot bootstrap)
    │   └── interfaces.go           TickWriter, SnapshotFetcher interfaces
    ├── orderbook/
    │   └── orderbook.go            Pure L2 state machine (zero IO)
    ├── exchange/
    │   ├── exchange.go             Exchange interface + Tick/Signal types
    │   ├── kucoin/                 KuCoin WebSocket driver
    │   └── bybit/                  Bybit WebSocket driver
    ├── gapdetector/
    │   └── gapdetector.go          Sequence-number gap detection
    ├── writer/
    │   ├── redis/writer.go         XADD to ticks:{exchange}:{symbol}
    │   ├── questdb/writer.go       ILP writes to ticks table
    │   └── pubsub/publisher.go     PUBLISH to orderbook:{exchange}:{symbol}
    ├── metrics/metrics.go          Prometheus counters/gauges
    └── reconnect/reconnect.go      Exponential backoff reconnect logic
```

---

## 3. The `Exchange` Interface

[`aggregator/internal/exchange/exchange.go`](../../aggregator/internal/exchange/exchange.go)

Every exchange driver implements:

```go
type Exchange interface {
    Connect(ctx context.Context) error
    Subscribe(symbols []string) error
    Ticks() <-chan Tick
    Signals() <-chan Signal
    Close() error
}
```

A `Tick` carries:
- `Exchange`, `Symbol`, `FeedType` (trade/quote), `EventType` (snapshot/update)
- `Seq` — exchange sequence number (used by `GapDetector`)
- `Price`, `Size`, `Side` (for trades)
- `Bids`, `Asks` — delta slices `[][2]string` for book updates
- `Timestamp` — exchange-provided timestamp

A `Signal` is an out-of-band event: `Reconnected`, `SnapshotRequired`, etc. The
`Worker` listens on both channels and handles them separately.

---

## 4. Order Book: Pure State Machine

[`aggregator/internal/orderbook/orderbook.go`](../../aggregator/internal/orderbook/orderbook.go)

> ⚠ **Constraint:** No goroutines in `internal/orderbook`. It is a pure, zero-IO
> state machine with single-goroutine ownership.

The `OrderBook` struct holds `bids` and `asks` as sorted price-level maps. Its API:

| Method | What it does |
|--------|-------------|
| `Apply(tick)` | Applies a trade or quote delta; returns gap event if seq jumps |
| `Reset()` | Clears book state; called after `internal_merge_error` |
| `ApplySnapshot(snap)` | Replaces book with a REST snapshot |
| `Snapshot()` | Returns a serializable copy of current top N levels |

The book tracks `snapshotSeen bool`. The `ApplyGap` method resets it to `false` **only**
for `internal_merge_error` — other gap causes (`external_disconnect`,
`external_rate_limit`, `cold_start_buffer_overflow`) leave it unchanged because the
book data is still valid; only the sequence counter needs resetting.

---

## 5. Gap Detection

[`aggregator/internal/gapdetector/gapdetector.go`](../../aggregator/internal/gapdetector/gapdetector.go)

```go
func Detect(prev, next uint64, cause string, clock Clock) *GapEvent
```

Returns a non-nil `*GapEvent` when `next != prev+1`. The `GapEvent` carries:
- `Cause` — one of `external_disconnect`, `external_rate_limit`,
  `internal_merge_error`, `cold_start_buffer_overflow`
- `GapSize` = `next - prev - 1`
- `Timestamp`

The coordinator's metrics layer in `metrics.go` filters out `external_disconnect` and
`internal_buffer_overflow` from the health gauge — those are transient and expected.

---

## 6. Coordinator and Workers

### Coordinator [`coordinator/coordinator.go`](../../aggregator/internal/coordinator/coordinator.go)

The `Coordinator` owns all per-symbol `Worker` instances. Its main responsibilities:

- `runTickFanout(ctx)` — goroutine that reads from all exchange `Ticks()` channels
  and dispatches each tick to the correct symbol's `Worker` via a `deltaChanCap=256`
  buffered channel.
- `runSignalDrain(ctx)` — goroutine that reads `Signals()` from all exchanges and
  routes reconnect/snapshot-required signals to the right worker.
- Holds references to the `TickWriter`, `QuestDBWriter`, and `OBPublisher` interfaces
  so workers can share them.

### Worker [`coordinator/symbol.go`](../../aggregator/internal/coordinator/symbol.go)

Each symbol gets one `Worker`, which runs in its own goroutine. The `Run()` method:

```
loop:
  select {
    case tick  → handleTick(tick)
    case signal → handleSignal(signal)
    case <-ctx.Done() → return
  }
```

`handleTick()`:
1. Calls `GapDetector.Detect(prevSeq, tick.Seq, ...)`.
2. If gap detected → logs, increments metric, may call `requestSnapshot()`.
3. Calls `OrderBook.Apply(tick)`.
4. Extracts OB snapshot via `OrderBook.Snapshot()`.
5. Writes to Redis stream via `TickWriter`.
6. Writes to QuestDB via `QuestDBWriter` (fire-and-forget).
7. Publishes OB JSON to Redis pub/sub via `OBPublisher`.

`requestSnapshot()`:
- Sends an async request to the `multiSnapshotFetcher`.
- The fetcher calls the exchange REST API and returns a snapshot.
- The Worker calls `OrderBook.ApplySnapshot()` when it arrives.

---

## 7. Writers

### Redis Stream Writer [`writer/redis/writer.go`](../../aggregator/internal/writer/redis/writer.go)

Uses `XADD ticks:{exchange}:{symbol} * field value ...`. Fields map directly to
`Tick` struct fields. The stream key pattern is `ticks:{exchange}:{symbol}` — e.g.,
`ticks:kucoin:BTC-USDT`.

### QuestDB ILP Writer [`writer/questdb/writer.go`](../../aggregator/internal/writer/questdb/writer.go)

Writes to the `ticks` table via TCP line protocol. Fire-and-forget: errors are logged
but never returned up the call stack. Uses a persistent TCP connection maintained by
`questdb-go/ingress.Sender`.

### OB Pub/Sub Publisher [`writer/pubsub/publisher.go`](../../aggregator/internal/writer/pubsub/publisher.go)

After every tick that mutates the book, the coordinator serializes the current top-N
bid/ask levels to JSON and calls `PUBLISH orderbook:{exchange}:{symbol} <json>`. This
channel is consumed by both the **gateway** (for browser clients) and the **bot
service** (for strategies that use orderbook mode).

---

## 8. Configuration

[`aggregator/internal/config/config.go`](../../aggregator/internal/config/config.go)

All config via environment variables:

| Env var | Default | Purpose |
|---------|---------|---------|
| `SYMBOLS` | (required) | Comma-separated `exchange:SYMBOL` pairs |
| `REDIS_ADDR` | `redis:6379` | Redis connection |
| `QUESTDB_ILP_ADDR` | `questdb:9009` | QuestDB ILP TCP |
| `TICK_RETENTION_MS` | `60000` | How long to keep ticks in the Redis stream |
| `LOG_LEVEL` | `info` | slog level |
| `OB_DEPTH` | `20` | Levels per side to publish |

`fileconfig.go` optionally loads a YAML file for per-symbol overrides.

---

## 9. Health and Metrics

`/health` returns `{"status":"ok"}` once the startup gate passes.

`/metrics` exposes Prometheus counters:
- `aggregator_ticks_total{exchange, symbol, feed_type}` — tick throughput
- `aggregator_gaps_total{exchange, symbol, cause}` — gap events (filtered)
- `aggregator_orderbook_health{exchange, symbol}` — 1=healthy, 0=degraded
- `aggregator_snapshot_fetch_duration_seconds` — REST snapshot latency

---

## 10. Reconnect Logic

[`aggregator/internal/reconnect/reconnect.go`](../../aggregator/internal/reconnect/reconnect.go)

Exponential backoff with jitter, starting at 1 s, capping at 60 s. On reconnect:
1. Exchange `Connect()` → `Subscribe()`.
2. A `Reconnected` signal is sent to all workers for that exchange.
3. Workers call `requestSnapshot()` to refetch REST snapshots.
4. Until a new snapshot arrives, `Apply()` on book updates is skipped (sequence
   tracking is in an unknown state).
