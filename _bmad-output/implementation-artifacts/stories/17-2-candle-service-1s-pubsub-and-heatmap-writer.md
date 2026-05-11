# Story 17.2: Candle Service 1s Pub/Sub + Heatmap Writer

Status: done

## Story

As mrqdt,
I want the candle service to publish computed 1s features to Redis pub/sub and snapshot the top-20 order book price levels to QuestDB every second,
so that the depthview gateway can serve charts and OFI/spread data to the frontend, and I can replay heatmap history for strategy research.

## Acceptance Criteria

### AC 1 — New `candles1s` pub/sub publisher package

1. New file: `candle-service/internal/writer/pubsub/publisher.go` with package `pubsub`.
2. Defines a narrow `PubSubClient` interface:
   ```go
   type PubSubClient interface {
       Publish(ctx context.Context, channel string, message any) error
   }
   ```
3. Defines `RealClient` wrapping `*goredis.Client`:
   ```go
   type RealClient struct{ c *goredis.Client }
   func NewRealClient(c *goredis.Client) *RealClient { return &RealClient{c: c} }
   func (r *RealClient) Publish(ctx context.Context, ch string, msg any) error {
       return r.c.Publish(ctx, ch, msg).Err()
   }
   ```
4. `Publisher` struct with `Publish1sBar(ctx context.Context, bar accumulator.Bar)` method.
5. Channel name pattern: `fmt.Sprintf("candles1s:%s:%s", exchange, symbol)`.

### AC 2 — Candles1s JSON payload

6. `Publisher.Publish1sBar` builds a JSON payload using `map[string]any` (NOT a named struct with JSON tags):

   | Field | Type | Value |
   |---|---|---|
   | `ts_ns` | int64 | `bar.TsSecMs * 1_000_000` (ms → ns) |
   | `exchange` | string | `bar.Exchange` |
   | `symbol` | string | `bar.Symbol` |
   | `open` | float64 | `*bar.Open` or `0.0` if nil |
   | `high` | float64 | `*bar.High` or `0.0` if nil |
   | `low` | float64 | `*bar.Low` or `0.0` if nil |
   | `close` | float64 | `*bar.Close` or `0.0` if nil |
   | `volume` | float64 | `*bar.Volume` or `0.0` if nil |
   | `ofi` | float64 | `*bar.OFI` or `0.0` if nil |
   | `ofi_l1` | float64 | `*bar.OFIL1` or `0.0` if nil |
   | `spread` | float64 | `*bar.SpreadMean` or `0.0` if nil |
   | `bid_depth_l1` | float64 | `*bar.BidDepthL1Close` or `0.0` if nil |
   | `ask_depth_l1` | float64 | `*bar.AskDepthL1Close` or `0.0` if nil |
   | `bid_depth_top10` | float64 | `*bar.BidDepthTop10Close` or `0.0` if nil |
   | `ask_depth_top10` | float64 | `*bar.AskDepthTop10Close` or `0.0` if nil |
   | `realized_vol` | float64 | `*bar.RealizedVol` or `0.0` if nil |

7. On `client.Publish` error: log `slog.Warn("candles1s pubsub: publish failed", ...)` and return the error — caller decides whether to propagate.
8. If `ctx.Err() != nil`, suppress the warn log (same ctx-cancelled guard as aggregator pattern from story 17-1).

### AC 3 — New `orderbook_heatmap` QuestDB ILP writer package

9. New file: `candle-service/internal/writer/heatmap/writer.go` with package `heatmap`.
10. Defines a narrow `ILPSender` interface for testability:
    ```go
    type ILPSender interface {
        Table(name string) qdb.LineSender
    }
    ```
    Actually use a simple approach: the `Writer` holds a `qdb.LineSender` directly (like `questdb.Writer`). For unit testing use a `FakeWriter` that records calls.
11. `Writer` struct:
    ```go
    type Writer struct {
        sender   qdb.LineSender
        exchange string
        symbol   string
    }
    func New(ctx context.Context, ilpAddr, exchange, symbol string) (*Writer, error)
    func (w *Writer) WriteHeatmap(ctx context.Context, tsSecMs int64, bids, asks map[string]string) error
    func (w *Writer) Close(ctx context.Context) error
    ```
12. `New` connects a dedicated `qdb.LineSender` using `qdb.WithTcp()` + `qdb.WithAddress(ilpAddr)`.
13. `WriteHeatmap` writes up to 20 ILP rows to table `orderbook_heatmap`. One row per level index 1..20 where level `i` pairs bid rank `i` and ask rank `i`:
    - Bids sorted **descending** by price (best bid = level 1); asks sorted **ascending** by price (best ask = level 1).
    - Each row: `table("orderbook_heatmap").Symbol("exchange", exchange).Symbol("symbol", symbol).Int64Column("level", int64(i)).Float64Column("bid_price", bidPrice_i).Float64Column("bid_size", bidSize_i).Float64Column("ask_price", askPrice_i).Float64Column("ask_size", askSize_i).At(ctx, time.UnixMilli(tsSecMs))`
    - If bid side has fewer than `i` levels: `bid_price=0.0`, `bid_size=0.0` for that row.
    - If ask side has fewer than `i` levels: `ask_price=0.0`, `ask_size=0.0` for that row.
    - Skip the row entirely only when BOTH sides are absent at level `i`.
    - Levels with size `"0"` or `""` are excluded before sorting (corrupted state guard).
14. After writing all rows, call `sender.Flush(ctx)` to send the ILP batch.
15. On any error: log `slog.Warn("heatmap: ilp write failed", "exchange", exchange, "symbol", symbol, "error", err)` and return the error — caller swallows it (fire-and-forget).
16. Price string→float64 parsing uses `strconv.ParseFloat`. Parse errors on price keys are silently skipped.

### AC 4 — Shared level-sorting helper

17. The level-sorting logic (string price-map → sorted `[]levelEntry` with cap) is the same pattern as story 17-1's `sortedLevels`. Define it in the `heatmap` package:
    ```go
    type levelEntry struct{ price float64; priceStr, sizeStr string }
    func sortedLevels(m map[string]string, descending bool) []levelEntry
    ```
    This function:
    - Iterates the map, parses price string to float64 (skip parse errors), excludes `size == "0"` or `size == ""`.
    - Sorts by price (descending for bids, ascending for asks).
    - Does NOT cap at 20 (caller caps with `min(len(sorted), 20)`).

### AC 5 — `accWriter` wiring in `cmd/candle/main.go`

18. `accWriter` struct gains two new optional fields:
    ```go
    candles1sPub  *pubsubwriter.Publisher  // nil = disabled
    heatmapWriter *heatmapwriter.Writer    // nil = disabled
    ```
    (import aliases: `pubsubwriter` for `candle-service/internal/writer/pubsub`, `heatmapwriter` for `candle-service/internal/writer/heatmap`)
19. In `accWriter.Flush(isPartial bool)`, inside the `!isPartial` branch, AFTER `aw.publisher.PublishOBFeatures` call, add:
    ```go
    if aw.candles1sPub != nil {
        if err := aw.candles1sPub.Publish1sBar(aw.ctx, bar); err != nil && aw.ctx.Err() == nil {
            slog.WarnContext(aw.ctx, "candles1s pubsub publish failed",
                "exchange", aw.exchange, "symbol", aw.symbol, "err", err)
        }
    }
    if aw.heatmapWriter != nil {
        if err := aw.heatmapWriter.WriteHeatmap(aw.ctx, bar.TsSecMs, bids, asks); err != nil && aw.ctx.Err() == nil {
            slog.WarnContext(aw.ctx, "heatmap write failed",
                "exchange", aw.exchange, "symbol", aw.symbol, "err", err)
        }
    }
    ```
    Note: `bids` and `asks` are already captured at the top of `Flush` via `aw.ob.AllBids()` and `aw.ob.AllAsks()` — no additional call needed.
20. In the `main()` per-symbol build loop (Phase 1), construct both writers and assign to `accWriter`:
    ```go
    candles1sPub := pubsubwriter.New(pubsubwriter.NewRealClient(rdb), p.exchange, p.symbol)
    hw, err := heatmapwriter.New(ctx, cfg.QuestDBILPAddr, p.exchange, p.symbol)
    if err != nil {
        logger.Error("heatmap writer init failed", "exchange", p.exchange, "symbol", p.symbol, "error", err)
        os.Exit(1)
    }
    ```
    Assign to `aw.candles1sPub = candles1sPub` and `aw.heatmapWriter = hw`.
21. Add `heatmapWriter.Close(shutdownCtx)` calls in the shutdown section (alongside existing `w.Close(shutdownCtx)` calls).
22. No new environment config variables — pub/sub uses the same `rdb` (*redis.Client); heatmap ILP uses the same `cfg.QuestDBILPAddr`.

### AC 6 — Redis pub/sub failure never blocks the 1s flush

23. A `client.Publish` error from `Publish1sBar` is logged at WARN and the pipeline continues. The 1s cycle (QuestDB snapshot_1s write, cascade, heatmap) is unaffected.
24. If `ctx` is already cancelled, the warn log is suppressed (`ctx.Err() == nil` guard).

### AC 7 — Heatmap ILP failure never blocks the 1s flush

25. `WriteHeatmap` errors are fire-and-forget: logged at WARN, never returned to the consumer loop.
26. Each `WriteHeatmap` call sends its own complete ILP batch (`Flush` inside `WriteHeatmap`). A batch failure for one second does not affect the next second's write.

### AC 8 — L1 tests: pub/sub publisher

27. New file: `candle-service/internal/writer/pubsub/publisher_test.go` (no build tag).
28. `FakePubSubClient` captures publish calls:
    ```go
    type FakePubSubClient struct {
        Calls []struct{ Channel string; Payload []byte }
        Err   error
    }
    func (f *FakePubSubClient) Publish(_ context.Context, ch string, msg any) error {
        f.Calls = append(f.Calls, struct{...}{ch, []byte(fmt.Sprint(msg))})
        return f.Err
    }
    ```
29. Tests required:
    - `TestPublisher_ChannelName`: exchange="kucoin", symbol="BTCUSDT" → channel `"candles1s:kucoin:BTCUSDT"`.
    - `TestPublisher_PayloadFields`: build a bar with known non-nil fields → JSON contains `ts_ns`, `open`, `ofi`, `spread`, `bid_depth_l1`, `realized_vol` with correct values.
    - `TestPublisher_NilFieldsZero`: bar with all nil pointer fields → JSON has `open=0`, `ofi=0`, `spread=0`, etc. (no null, no missing fields).
    - `TestPublisher_TsNs`: bar with TsSecMs=1_700_000_000_000 → `ts_ns = 1_700_000_000_000_000_000`.
    - `TestPublisher_ClientError`: `FakePubSubClient.Err` set → `Publish1sBar` returns the error.

### AC 9 — L1 tests: heatmap writer

30. New file: `candle-service/internal/writer/heatmap/writer_test.go` (no build tag).
31. Define a `FakeHeatmapSender` that satisfies the testable interface (see AC 3 note); captures rows written.
32. Tests required:
    - `TestHeatmapWriter_LevelOrder`: bids at prices 100, 102, 101; asks at 103, 105, 104 → level 1 has bid=102, ask=103; level 2 has bid=101, ask=104; level 3 has bid=100, ask=105.
    - `TestHeatmapWriter_ThinBook_BidOnly`: 3 bid levels, 0 ask levels → 3 rows written; rows have ask_price=0.0, ask_size=0.0.
    - `TestHeatmapWriter_ThinBook_AskOnly`: 0 bid levels, 3 ask levels → 3 rows written; bid_price=0.0, bid_size=0.0.
    - `TestHeatmapWriter_Cap20`: 25 bid levels, 25 ask levels → exactly 20 rows written.
    - `TestHeatmapWriter_EmptyBook`: 0 bids, 0 asks → 0 rows written (no ILP calls, no flush).
    - `TestHeatmapWriter_ExcludesZeroSize`: bid level with size="0" excluded from sorted levels.

    **Testing approach for heatmap writer:** Since `qdb.LineSender` is a concrete interface-like type, mock the ILP sender via interface injection. Define in `heatmap` package:
    ```go
    // ilpRow records one ILP row for test assertions.
    type ilpRow struct { table, exchange, symbol string; level int64; bidPrice, bidSize, askPrice, askSize float64 }
    type fakeLineSender struct { rows []ilpRow; flushed bool }
    ```
    The `Writer` should accept an `ilpSender` interface (rather than `qdb.LineSender` directly) to enable injection. Define the interface in the package:
    ```go
    type LineSender interface {
        Table(name string) qdb.LineSender
    }
    ```
    Wait — `qdb.LineSender` is itself an interface in the go-questdb-client library. The `Table()` method returns `qdb.LineSender` (fluent builder). So we can mock it. Define:
    ```go
    // LineSenderFactory creates or provides a qdb.LineSender for this writer.
    // In production: connects via TCP. In tests: returns a fake.
    type LineSenderFactory func(ctx context.Context) (qdb.LineSender, error)
    ```
    Then `Writer` holds a `qdb.LineSender` (populated at `New` time), plus a way to replace it for tests:
    - `New(ctx, ilpAddr, exchange, symbol)` connects real sender.
    - `NewWithSender(sender qdb.LineSender, exchange, symbol)` for tests.

### AC 10 — L2 integration tests

33. New file: `candle-service/internal/writer/pubsub/publisher_l2_test.go` (build tag: `l2`).
34. Test `TestPublisher_RealRedis`: start a real `*goredis.Client`, PSUBSCRIBE to `candles1s:*`, call `Publish1sBar` with a complete accumulator.Bar. Assert:
    - Message arrives within 100ms.
    - Channel is `candles1s:{exchange}:{symbol}`.
    - JSON payload is valid and contains `"ts_ns"`, `"open"`, `"ofi"`.

### AC 11 — Build and test gates

35. `go build ./...` in `candle-service/` compiles clean.
36. `go test ./...` (L1) passes — all existing tests still pass; nil pub/heatmap paths untouched.
37. New L1 tests pass: 5 publisher tests + 6 heatmap tests.

## Tasks / Subtasks

- [x] Task 1: Create `candle-service/internal/writer/pubsub/publisher.go` (AC 1–2, 6)
  - [x] `PubSubClient` interface, `RealClient` wrapper
  - [x] `Publisher` struct with `New(client PubSubClient, exchange, symbol string)` constructor
  - [x] `Publish1sBar` method: build JSON payload, publish, warn-log on error with ctx guard

- [x] Task 2: Write L1 pub/sub publisher tests (AC 8)
  - [x] `FakePubSubClient`
  - [x] 5 test cases: channel name, payload fields, nil→zero, ts_ns, client error

- [x] Task 3: Write L2 pub/sub integration test (AC 10)
  - [x] `publisher_l2_test.go` with build tag `l2`
  - [x] Real Redis PSUBSCRIBE, assert message within 100ms

- [x] Task 4: Create `candle-service/internal/writer/heatmap/writer.go` (AC 3–4, 7)
  - [x] `sortedLevels` helper (descending/ascending, exclude size "0"/"")
  - [x] `NewWithSender(sender qdb.LineSender, exchange, symbol)` for tests
  - [x] `New(ctx, ilpAddr, exchange, symbol)` for production (TCP ILP connection)
  - [x] `WriteHeatmap(ctx, tsSecMs, bids, asks)`: sort levels, write up to 20 ILP rows, flush
  - [x] `Close(ctx)` delegates to sender.Close

- [x] Task 5: Write L1 heatmap writer tests (AC 9)
  - [x] Implement `fakeLineSender` that records rows and flush calls
  - [x] 6 test cases: level ordering, thin bid, thin ask, cap 20, empty book, zero-size exclusion

- [x] Task 6: Wire into `accWriter` and `main.go` (AC 5)
  - [x] Add `candles1sPub` and `heatmapWriter` fields to `accWriter`
  - [x] Add publish calls in `Flush(!isPartial)` branch (after `PublishOBFeatures`)
  - [x] Wire `candles1sPub` and `heatmapWriter` construction in Phase 1 build loop
  - [x] Add `heatmapWriter.Close` in shutdown section

- [x] Task 7: Build and test gates (AC 11)
  - [x] `go build ./...` clean
  - [x] `go test ./...` passes all L1 tests (existing + 11 new)

## Dev Notes

### File map — what changes, what's new

| File | Status | Notes |
|---|---|---|
| `candle-service/internal/writer/pubsub/publisher.go` | NEW | PubSubClient, RealClient, Publisher.Publish1sBar |
| `candle-service/internal/writer/pubsub/publisher_test.go` | NEW | 5 L1 unit tests |
| `candle-service/internal/writer/pubsub/publisher_l2_test.go` | NEW | L2 real-Redis integration test |
| `candle-service/internal/writer/heatmap/writer.go` | NEW | sortedLevels, Writer, WriteHeatmap |
| `candle-service/internal/writer/heatmap/writer_test.go` | NEW | 6 L1 unit tests |
| `candle-service/cmd/candle/main.go` | UPDATE | Add fields to accWriter, wire in main build loop and shutdown |

### Flush call site — exactly where to add the new calls

In `cmd/candle/main.go`, `accWriter.Flush(isPartial bool)` at the `!isPartial` branch (currently around line 105):
```go
if !isPartial {
    closedBars := aw.engine.Fold(bar, tsSecMs)
    aw.persistCascadeHashes(closedBars)
    if aw.publisher != nil {
        aw.publisher.PublishBars(aw.ctx, closedBars)
        aw.publisher.PublishOBFeatures(aw.ctx, bar)
    }
    // ADD HERE: candles1s pub/sub and heatmap ILP write
}
```
The `bids` and `asks` variables are already computed at the top of `Flush`:
```go
bids, asks := aw.ob.AllBids(), aw.ob.AllAsks()
```
They are already used for `SetCloseDepth`. They are captured before any mutation — safe to reuse for heatmap.

### JSON payload: nil → 0.0 pattern

All pointer fields in `accumulator.Bar` can be nil (no data for that second). The JSON must still include the field with value `0.0` (the depthview gateway expects all fields present). Use a helper:
```go
func derefF(p *float64) float64 {
    if p == nil { return 0 }
    return *p
}
```
Apply to every nullable field when building the payload map.

### Heatmap ILP row design — 20 rows, paired bid+ask per level

Each row represents level `i` (1 = best). `bid_price` and `bid_size` hold bid level `i`; `ask_price` and `ask_size` hold ask level `i`. If a side has fewer than `i` levels (thin book), those fields are `0.0`. Skip the row only when BOTH sides have fewer than `i` levels (both absent). This produces a clean columnar view for QuestDB heatmap queries:
```sql
SELECT ts, level, bid_price, bid_size, ask_price, ask_size
FROM orderbook_heatmap
WHERE exchange='kucoin' AND symbol='BTC-USDT'
AND ts BETWEEN '...' AND '...'
ORDER BY ts, level
```

### ILP At() timestamp

Use `time.UnixMilli(tsSecMs)` — same pattern as `questdb.Writer.writeBar`:
```go
row = row.At(ctx, time.UnixMilli(tsSecMs))
```
The `tsSecMs` comes from `bar.TsSecMs` (already in the bar passed to `WriteHeatmap`).

### Separate ILP connection per symbol

Each (exchange, symbol) gets its own `heatmap.Writer` with its own `qdb.LineSender` — same pattern as the existing `questdb.Writer` in the candle service (one writer per symbol). This avoids cross-symbol serialisation.

### qdb.LineSender is an interface

`qdb.LineSender` is defined as an interface in `github.com/questdb/go-questdb-client/v3`. The `Writer` can store a `qdb.LineSender` field and accept it via `NewWithSender`. This is the cleanest test injection approach — no factory function needed:
```go
type Writer struct {
    sender   qdb.LineSender
    exchange string
    symbol   string
}

func New(ctx context.Context, ilpAddr, exchange, symbol string) (*Writer, error) {
    sender, err := qdb.NewLineSender(ctx, qdb.WithTcp(), qdb.WithAddress(ilpAddr))
    if err != nil { return nil, err }
    return &Writer{sender: sender, exchange: exchange, symbol: symbol}, nil
}

func NewWithSender(sender qdb.LineSender, exchange, symbol string) *Writer {
    return &Writer{sender: sender, exchange: exchange, symbol: symbol}
}
```

### Testing heatmap rows

Since `qdb.LineSender` is an interface, define a `fakeLineSender` in the test file that implements the full interface but only captures the data needed for assertions. The ILP fluent API (`Table → Symbol → Int64Column → Float64Column → At`) returns `qdb.LineSender` at each step, so the fake needs to implement all of those methods and collect the column values per row.

A simpler approach: define the fake as a struct that collects field calls per row and validates in the test. The fake's `Table` method starts a new row; each `Symbol`/`Int64Column`/`Float64Column` call records the field; `At` finalises and appends the row to a slice.

### No new config fields needed

- `candles1sPub`: uses existing `rdb` (*redis.Client) — same connection as stream writes.
- `heatmapWriter`: uses existing `cfg.QuestDBILPAddr` — same ILP address as snapshot_1s.
- No new env vars; no config.go changes.

### README.md log message reference

Per CLAUDE.md instructions, these new `slog.Warn` calls must be added to `README.md`:
- `"candles1s pubsub: publish failed"` → "Candle service — consumer" section
- `"heatmap: ilp write failed"` → "Candle service — QuestDB writer" section
- `"candles1s pubsub publish failed"` (in main.go) → "Candle service — consumer" section
- `"heatmap write failed"` (in main.go) → "Candle service — consumer" section

### References

- `accWriter.Flush`: `candle-service/cmd/candle/main.go` lines ~95–120
- `accWriter` struct definition: `candle-service/cmd/candle/main.go` lines ~52–70
- ILP write chain pattern: `candle-service/internal/writer/questdb/writer.go:138–386`
- ILP sender `At()` pattern: `candle-service/internal/writer/questdb/writer.go:386`
- `ob.AllBids()` / `ob.AllAsks()`: `candle-service/internal/orderbook/orderbook.go:251–265`
- `accumulator.Bar` struct (all fields): `candle-service/internal/accumulator/accumulator.go:22–131`
- Existing redis publisher (pattern for new pubsub): `candle-service/internal/writer/redis/publisher.go`
- Aggregator pubsub pattern (story 17-1 established): `aggregator/internal/writer/pubsub/publisher.go`
- go-questdb-client ILP: `github.com/questdb/go-questdb-client/v3 v3.2.0`
- go-redis Publish: `github.com/redis/go-redis/v9` — same dependency already in candle-service

### Review Findings

- [x] [Review][Patch] Unused `"context"` import — compilation failure under `-tags l2` [candle-service/internal/writer/pubsub/publisher_l2_test.go:6]
- [x] [Review][Patch] ILP sender buffer dirty after mid-loop `At()` error — stale rows poison next second's heatmap [candle-service/internal/writer/heatmap/writer.go:79-83]
- [x] [Review][Patch] Double WARN logging: internal writer AND main.go both log on error — every failure emits 2 lines [candle-service/cmd/candle/main.go:115-124]
- [x] [Review][Patch] Missing `ctx.Err() == nil` guard in `WriteHeatmap` WARN logs — log spam on shutdown [candle-service/internal/writer/heatmap/writer.go:80-89]
- [x] [Review][Patch] NaN/Inf prices not filtered in `sortedLevels` — violates strict-weak-ordering in `sort.Slice` [candle-service/internal/writer/heatmap/writer.go:114-121]

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

- Fixed `fakePubSubClient.Publish` in tests: `fmt.Sprint([]byte)` produces array notation — used type switch to correctly capture raw bytes as JSON payload.
- Fixed `fakeLineSender.Long256Column` signature: actual `qdb.LineSender` interface takes `*big.Int` not 4 `uint64`s.
- Renamed test helper `strconv` (clashed with package import from writer.go) to `itoa`.

### Completion Notes List

- All 7 tasks and 11 ACs satisfied.
- 5 new L1 publisher tests pass (`writer/pubsub`); 6 new L1 heatmap tests pass (`writer/heatmap`).
- 1 L2 test created with build tag `l2`; requires real Redis.
- `go build ./...` and `go test ./...` both pass clean with no regressions (18 packages).
- Two new packages added: `internal/writer/pubsub` (candles1s Redis pub/sub) and `internal/writer/heatmap` (orderbook_heatmap ILP writer).
- `accWriter.Flush` wired: both pub/sub and heatmap calls are fire-and-forget with ctx-guard suppress-on-shutdown.
- Heatmap rows: 20 rows per second (one per level index), paired bid+ask per row; thin book writes 0.0 for absent side.
- README.md updated with 4 new log message rows per CLAUDE.md rules.

### File List

- `candle-service/internal/writer/pubsub/publisher.go` — NEW: PubSubClient, RealClient, Publisher
- `candle-service/internal/writer/pubsub/publisher_test.go` — NEW: 5 L1 unit tests
- `candle-service/internal/writer/pubsub/publisher_l2_test.go` — NEW: L2 real-Redis integration test
- `candle-service/internal/writer/heatmap/writer.go` — NEW: sortedLevels, Writer, WriteHeatmap
- `candle-service/internal/writer/heatmap/writer_test.go` — NEW: 6 L1 unit tests
- `candle-service/cmd/candle/main.go` — UPDATED: new imports, accWriter fields, Flush wiring, shutdown close
- `README.md` — UPDATED: 4 new log message rows in candle service consumer/QuestDB writer sections
