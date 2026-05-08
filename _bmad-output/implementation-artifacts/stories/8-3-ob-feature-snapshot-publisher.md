# Story 8.3: OB Feature Snapshot Publisher

Status: ready-for-dev

## Story

As the candle service,
I want to publish 1-second order book feature snapshots to `ob_features:{exchange}:{symbol}` on each bar close,
So that downstream bots and ML consumers can subscribe to a live feed of close-time OB state (best quotes, depth, OFI) without polling QuestDB.

## Acceptance Criteria

### AC 1 — `Publisher.PublishOBFeatures` method

1. `internal/writer/redis/publisher.go` gains a new method:
   ```go
   func (p *Publisher) PublishOBFeatures(ctx context.Context, bar accumulator.Bar)
   ```
   where `accumulator` is aliased as `"github.com/mrqdt/magnum-opus/candle-service/internal/accumulator"`.
2. The method XADDs to stream key `ob_features:{exchange}:{symbol}` using `p.maxLen` as MAXLEN and `Approx: true`.
3. On XADD failure: log ERROR with exchange/symbol/error, call `failureFn` if set, **continue** — never block or return early.
4. Zero IO beyond the Redis client. No `time.Now()`, no goroutines, no `init()`.

### AC 2 — OB feature field serialization

5. An `accumulator.Bar` is serialized to `map[string]any` by a private helper `obFields(bar accumulator.Bar) map[string]any` with exactly these keys and values:

   | Key | Value |
   |-----|-------|
   | `ts` | `strconv.FormatInt(bar.TsSecMs, 10)` — Unix ms of bar open (second boundary) |
   | `exchange` | `bar.Exchange` |
   | `symbol` | `bar.Symbol` |
   | `best_bid` | `""` when nil; `strconv.FormatFloat(*bar.BestBid, 'f', -1, 64)` when set |
   | `best_ask` | same nil convention |
   | `bid_depth_l1` | `floatOrEmpty(bar.BidDepthL1Close)` |
   | `ask_depth_l1` | `floatOrEmpty(bar.AskDepthL1Close)` |
   | `bid_depth_l2` | `floatOrEmpty(bar.BidDepthL2Close)` |
   | `ask_depth_l2` | `floatOrEmpty(bar.AskDepthL2Close)` |
   | `bid_depth_top10` | `floatOrEmpty(bar.BidDepthTop10Close)` |
   | `ask_depth_top10` | `floatOrEmpty(bar.AskDepthTop10Close)` |
   | `bid_depth_total` | `floatOrEmpty(bar.BidDepthTotalClose)` |
   | `ask_depth_total` | `floatOrEmpty(bar.AskDepthTotalClose)` |
   | `ofi` | `floatOrEmpty(bar.OFI)` |
   | `ofi_l1` | `floatOrEmpty(bar.OFIL1)` |

6. Nil pointer fields serialize to `""` — never `"<nil>"`, `"0"`, or any other string.
7. `floatOrEmpty` from the existing `publisher.go` is reused — no duplication.
8. `XAddArgs.Approx = true` for efficiency.

### AC 3 — Flush sequence step 5

9. `accWriter.Flush()` in `cmd/candle/main.go` is updated to call `publisher.PublishOBFeatures(aw.ctx, bar)` in the `!isPartial` block, **after** `publisher.PublishBars(aw.ctx, closedBars)` (step 4) and **before** `acc.BarReset()` (step 6).

   Exact updated block:
   ```go
   if !isPartial {
       closedBars := aw.engine.Fold(bar, tsSecMs)
       aw.persistCascadeHashes(closedBars)
       if aw.publisher != nil {
           aw.publisher.PublishBars(aw.ctx, closedBars)      // step 4 — candles: + candles:close:
           aw.publisher.PublishOBFeatures(aw.ctx, bar)       // step 5 — ob_features: (NEW)
       }
   }
   aw.acc.BarReset()  // step 6
   ```

10. `bar` passed to `PublishOBFeatures` is the `accumulator.Bar` returned by `acc.CurrentBar(tsSecMs, false)` earlier in `Flush()` — the closed, immutable snapshot. It is NOT re-read from the accumulator after `BarReset()`. This guarantees the correct bar state is captured before reset.

11. The nil guard `if aw.publisher != nil` already covers step 5 — no additional guard needed.

### AC 4 — No partial publishes, no TF dimension

12. `ob_features:{exchange}:{symbol}` is published on 1s bar close only — **never** on the 250ms partial publish cadence.
13. There is exactly one stream key per (exchange, symbol) — no timeframe segment, no `ob_features:close:` variant.
14. `candle_redis_publish_failure_total{exchange,symbol}` (2 labels, no tf) is the failure counter — same metric as candle stream failures, same closure pattern.

### AC 5 — Import: `accumulator` in `publisher.go`

15. `publisher.go` gains the import:
    ```go
    "github.com/mrqdt/magnum-opus/candle-service/internal/accumulator"
    ```
    This is valid — `accumulator/` is a leaf node (imports only `features/`); adding it to `writer/redis/` introduces no cycle.
16. The package comment is updated to note ob_features publishing:
    ```go
    // Package redis publishes cascade OHLCV bars and OB feature snapshots to Redis Streams.
    ```

### AC 6 — L2 tests

17. `internal/writer/redis/publisher_l2_test.go` (existing file, build tag `l2`, package `redis_test`) gains at least 4 new test functions:

    - **`TestPublishOBFeatures_AllFields`**: publish one bar with `BestBid=50000.0`, `BestAsk=50001.0`, `BidDepthL1Close=1.5`, `AskDepthL1Close=2.0`, non-nil depth fields, `OFI=0.5`, `OFIL1=0.5`. Assert:
      - `XLEN ob_features:kucoin:BTC-USDT` == 1
      - All 15 fields present and correct in the stream entry
      - `ts` == `strconv.FormatInt(bar.TsSecMs, 10)`
      - `best_bid` == `"50000"`, `best_ask` == `"50001"`
      - `ofi` == `"0.5"`, `ofi_l1` == `"0.5"`

    - **`TestPublishOBFeatures_NilFields`**: publish bar with nil BestBid/BestAsk and nil all depth/OFI fields. Assert:
      - Stream has 1 entry
      - `best_bid` == `""` (not `"<nil>"`)
      - `best_ask` == `""`
      - `bid_depth_l1` == `""`
      - `ofi` == `""`

    - **`TestPublishOBFeatures_StreamKey`**: publish bar for exchange="bybit", symbol="BTCUSDT". Assert:
      - `XLEN ob_features:bybit:BTCUSDT` == 1
      - No key `ob_features:kucoin:BTCUSDT` exists (publisher bound to bybit)
      - No key `ob_features:bybit:BTCUSDT:1s` or any TF-suffixed key exists

    - **`TestPublishOBFeatures_FailureCounter`**: create publisher, inject failure counter, `mr.Close()`, call `PublishOBFeatures`. Assert counter called ≥ 1 time.

    - (Optional) **`TestPublishOBFeatures_MAXLEN`**: publish 5 entries with `maxLen=2`. Assert stream length ≤ 3 (same pattern as `TestPublishBars_MAXLEN`).

### AC 7 — Build and test gates

18. `go build ./...` compiles clean, no unused imports or variables.
19. `make test-l1` passes (no L1 tests for this story — `writer/redis/` has IO dependencies).
20. `make test-l2` passes with all new L2 tests.

---

## Dev Notes

### ob_features stream summary

```
Stream key:    ob_features:{exchange}:{symbol}
MAXLEN:        p.maxLen (CANDLE_STREAM_MAXLEN, default 10,000)
Published:     on each 1s bar close (NOT on 250ms partial publish cadence)
Source:        accumulator.Bar from acc.CurrentBar() before acc.BarReset()
Fields:        15 (ts, exchange, symbol, best_bid, best_ask, 8 close-depth fields, ofi, ofi_l1)
Nil → "":      same convention as candle stream OHLC fields
No close variant: ob_features does not have a separate ob_features:close: stream
```

### Flush sequence (complete after story 8-3)

```
accWriter.Flush(isPartial bool):
  (1) acc.SetCloseDepth(features.ComputeDepthSnapshot(bids, asks))
  (2) acc.SeedFromLastKnown()
  (3) tsSecMs = time.Now().Truncate(time.Second).UnixMilli()
      bar := acc.CurrentBar(tsSecMs, isPartial)
  (4) w.WriteBar(ctx, bar)                      ← QuestDB ILP
  (5) if !isPartial:
        closedBars := engine.Fold(bar, tsSecMs)
        persistCascadeHashes(closedBars)
        publisher.PublishBars(ctx, closedBars)   ← candles: + candles:close: [8-2]
        publisher.PublishOBFeatures(ctx, bar)    ← ob_features: [8-3 NEW]
  (6) acc.BarReset()
  (7) openDepthSet = false
  (8) persistBlockWindow()
```

### Why `bar` not `closedBars` for ob_features?

`closedBars` is the list of **cascade** bars (1m, 5m, …, 1w) that closed this second. These are `cascade.Bar` structs — they have only 9 cascade fields (OHLCV + counts), not the full 67-field `accumulator.Bar`.

`bar` is the **1s accumulator bar** from `acc.CurrentBar()`. It carries all the OB close-state fields (BestBid, BestAsk, BidDepthL1Close, …, OFI, OFIL1). This is the correct source for ob_features.

The ob_features snapshot represents the OB state at the close of the 1s bar. Always use the `accumulator.Bar` — never the `cascade.Bar`.

### accumulator.Bar OB close fields

From `accumulator.Bar` (accumulator.go), the fields for ob_features:

| ob_features key | accumulator.Bar field |
|---|---|
| `best_bid` | `BestBid *float64` — close quote (nil if no OB ticks this second) |
| `best_ask` | `BestAsk *float64` — close quote |
| `bid_depth_l1` | `BidDepthL1Close *float64` — from `closeDepth.BidL1` |
| `ask_depth_l1` | `AskDepthL1Close *float64` — from `closeDepth.AskL1` |
| `bid_depth_l2` | `BidDepthL2Close *float64` — from `closeDepth.BidL2` |
| `ask_depth_l2` | `AskDepthL2Close *float64` — from `closeDepth.AskL2` |
| `bid_depth_top10` | `BidDepthTop10Close *float64` — from `closeDepth.BidTop10` |
| `ask_depth_top10` | `AskDepthTop10Close *float64` — from `closeDepth.AskTop10` |
| `bid_depth_total` | `BidDepthTotalClose *float64` — from `closeDepth.BidTotal` |
| `ask_depth_total` | `AskDepthTotalClose *float64` — from `closeDepth.AskTotal` |
| `ofi` | `OFI *float64` — nil when no ticks this second |
| `ofi_l1` | `OFIL1 *float64` — same value as OFI (per project-context.md) |

All depth fields are nil if no close-depth snapshot was computed (happens on empty seconds or when OB state is unknown after a gap). Nil → "" in the stream.

### Empty bars and ob_features

An "empty second" (no ticks, no OB updates) will produce a bar where BestBid/BestAsk and all depth fields are nil. `PublishOBFeatures` still publishes the entry — it doesn't skip on nil fields. Downstream consumers handle empty ob_features entries by treating nil ("") fields as "no data for this second."

Do NOT add a skip-if-all-nil guard — the publish is unconditional on bar close.

### Import in publisher.go

The existing `publisher.go` imports:
```go
import (
    "context"
    "log/slog"
    "strconv"

    goredis "github.com/redis/go-redis/v9"

    "github.com/mrqdt/magnum-opus/candle-service/internal/cascade"
)
```

After this story:
```go
import (
    "context"
    "log/slog"
    "strconv"

    goredis "github.com/redis/go-redis/v9"

    "github.com/mrqdt/magnum-opus/candle-service/internal/accumulator"
    "github.com/mrqdt/magnum-opus/candle-service/internal/cascade"
)
```

No cycle is introduced: `accumulator` is a leaf package (imports only `features/`); `writer/redis/` is imported only by `cmd/candle/main.go`.

### floatOrEmpty reuse

`floatOrEmpty(f *float64) string` is already defined in `publisher.go`. Reuse it in `obFields` — do not duplicate.

### Failure counter pattern

The `failureFn` is already set via `SetFailureCounter()`. For ob_features failures, call `p.failureFn()` in the same pattern as PublishBars and PublishPartialBar — no changes to the counter or main.go wiring needed.

`candle_redis_publish_failure_total{exchange,symbol}` counts all Redis publish failures (candle streams + ob_features). The two-label metric covers this correctly.

### L2 test helper: makeOBBar

Add a helper to `publisher_l2_test.go` for constructing test `accumulator.Bar` values:

```go
func makeOBBar(tsMs int64) accumulator.Bar {
    bid := 50000.0
    ask := 50001.0
    bidL1 := 1.5
    askL1 := 2.0
    ofi := 0.5
    return accumulator.Bar{
        TsSecMs:        tsMs,
        Exchange:       "kucoin",
        Symbol:         "BTC-USDT",
        BestBid:        &bid,
        BestAsk:        &ask,
        BidDepthL1Close: &bidL1,
        AskDepthL1Close: &askL1,
        OFI:            &ofi,
        OFIL1:          &ofi,
    }
}
```

Use distinct values for `bid_depth_l1` (1.5) and `ask_depth_l1` (2.0) so a swap bug would fail the assertion.

For the nil-fields test, construct a `accumulator.Bar` with all pointer fields left nil (zero value of the struct).

### No config changes

No new config fields needed. `CANDLE_STREAM_MAXLEN` (already in config as `CandleStreamMaxLen`) is used for the ob_features MAXLEN — same as the candle partial+close stream. The `Publisher` is already constructed with `int64(cfg.CandleStreamMaxLen)` as `maxLen` in Phase 1 of main.go — no changes to Phase 1 wiring needed.

### No consumer changes

No changes to `consumer.go` — ob_features is published from `accWriter.Flush()` in the same goroutine as candle streams. The 250ms partial-publish path does NOT publish ob_features.

### Existing publisher_l2_test.go — backward-compatible

`setupMiniredis(t)` and `makeBar(tf, openTs, isComplete)` are already defined. New tests add `makeOBBar(tsMs)` — no signature conflict. Tests in the same file share the `redis_test` package, so no import needed.

---

## Tasks / Subtasks

- [ ] Add `PublishOBFeatures(ctx, bar accumulator.Bar)` and `obFields()` helper to `internal/writer/redis/publisher.go` (AC 1–2, AC 5)
- [ ] Update `accWriter.Flush()` in `cmd/candle/main.go` to call `publisher.PublishOBFeatures(ctx, bar)` at step 5 (AC 3)
- [ ] Add L2 tests to `internal/writer/redis/publisher_l2_test.go` (AC 6)
- [ ] `go build ./...` and `make test-l1` pass (AC 7)
- [ ] `make test-l2` passes with all new L2 tests (AC 7)

---

## Dev Agent Record

### Completion Notes

_To be filled in by dev agent_

### File List

- `candle-service/internal/writer/redis/publisher.go` — modified (PublishOBFeatures, obFields, accumulator import)
- `candle-service/internal/writer/redis/publisher_l2_test.go` — modified (new OB feature test functions)
- `candle-service/cmd/candle/main.go` — modified (step 5 in Flush: PublishOBFeatures)

### Change Log

_To be filled in by dev agent_
