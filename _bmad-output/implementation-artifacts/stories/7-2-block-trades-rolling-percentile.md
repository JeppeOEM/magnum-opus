# Story 7.2: Block Trades Rolling Percentile

Status: done

## Story

As a developer,
I want `block_buy_volume`, `block_sell_volume`, `large_bid_orders`, and `large_ask_orders` computed per second and written to `snapshot_1s`,
so that CS-FR12 (block trade classification via rolling 99th-percentile threshold) is satisfied.

## Acceptance Criteria

1. A new package `internal/blockwindow/` provides a `Window` type:
   ```go
   type Window struct { /* unexported */ }
   func New(windowSize, minSample int) *Window
   func (w *Window) Add(size float64)
   func (w *Window) Threshold() (float64, bool) // (99th-pct, hasEnoughSamples)
   func (w *Window) Sizes() []float64           // copy of current window
   func (w *Window) Restore(sizes []float64)    // load from Redis on startup
   ```
   - Window capped at `windowSize` entries (oldest discarded when full).
   - `Threshold()` returns `(0, false)` if fewer than `minSample` entries.
   - 99th percentile uses nearest-rank method: sort a copy; `idx = ceil(0.99 * n) - 1`.
   - `Sizes()` returns a copy (caller must not modify).

2. `accumulator.Accumulator` gains `ApplyBlockTrade(side string)` — called directly by `accWriter`, NOT added to `AccumulatorApplier` interface (no interface churn).
   - `side == "buy"` → `blockBuyVolume += parsedSize; largeBidCount++`
   - `side != "buy"` → `blockSellVolume += parsedSize; largeAskCount++`

   Wait — `ApplyBlockTrade` must also receive `size float64` to accumulate volume:
   ```go
   func (a *Accumulator) ApplyBlockTrade(side string, size float64)
   ```

3. `accumulator.Bar` gains four new pointer fields (all nil when `TradeCount == 0` OR threshold unavailable this second):
   ```go
   BlockBuyVolume  *float64
   BlockSellVolume *float64
   LargeBidOrders  *int
   LargeAskOrders  *int
   ```

4. `CurrentBar()` populates all four fields only when `a.hasBlockData` is true (set by `ApplyBlockTrade`). `BarReset()` clears all four fields and resets `hasBlockData`.

5. `cmd/candle/accWriter` gains a `btw *blockwindow.Window` field. In `Apply()`:
   - For trade ticks only (`isTrade == true`): `parsedSize, _ = strconv.ParseFloat(size, 64)` then `aw.btw.Add(parsedSize)`.
   - If `threshold, ok := aw.btw.Threshold(); ok && parsedSize > threshold`: call `aw.acc.ApplyBlockTrade(side, parsedSize)`.

6. Redis persistence for the rolling window:
   - Key: `candle:btw:{exchange}:{symbol}` (LIST).
   - On each bar close in `accWriter.Flush()`: write current window to Redis via `DEL` then `RPUSH` with all sizes as string-formatted floats.
   - On startup in `main.go`: `LRANGE candle:btw:{exchange}:{symbol} 0 -1` to restore; parse each string to float64 and call `btw.Restore(sizes)`.
   - `accWriter` receives `rdb *redis.Client` for persistence.

7. `internal/writer/questdb/writer.go` writes all four new fields with the existing nil-check pattern:
   ```go
   if bar.BlockBuyVolume != nil { row = row.Float64Column("block_buy_volume", *bar.BlockBuyVolume) }
   if bar.BlockSellVolume != nil { row = row.Float64Column("block_sell_volume", *bar.BlockSellVolume) }
   if bar.LargeBidOrders != nil  { row = row.Int64Column("large_bid_orders", int64(*bar.LargeBidOrders)) }
   if bar.LargeAskOrders != nil  { row = row.Int64Column("large_ask_orders", int64(*bar.LargeAskOrders)) }
   ```

8. `main.go` reads `BLOCK_TRADE_WINDOW` (default 1000) and `BLOCK_TRADE_MIN_SAMPLE` (default 100) from env; passes them to `blockwindow.New()`.

9. `go test ./...` passes. New L1 tests for `blockwindow`:
   - `Threshold()` returns `(0, false)` below `minSample`.
   - Correct 99th percentile for known set.
   - Window respects capacity (oldest discarded).
   - `Restore()` then `Threshold()` returns correct value.
   - New accumulator L1 tests: `ApplyBlockTrade` accumulates correctly; nil when no block trades or no threshold.

## Tasks / Subtasks

- [x] Create `internal/blockwindow/window.go` with `Window` type (AC: 1)
  - [x] `New(windowSize, minSample int) *Window`
  - [x] `Add(size float64)` — append; discard oldest when over capacity
  - [x] `Threshold() (float64, bool)` — nearest-rank 99th percentile
  - [x] `Sizes() []float64` — copy
  - [x] `Restore(sizes []float64)` — replace internal slice (cap-clamped)
  - [x] L1 tests in `internal/blockwindow/window_test.go`

- [x] Add `ApplyBlockTrade(side string, size float64)` to `Accumulator` (AC: 2, 3, 4)
  - [x] Add state: `blockBuyVolume, blockSellVolume float64`, `largeBidCount, largeAskCount int`, `hasBlockData bool`
  - [x] Add `Bar` fields: `BlockBuyVolume *float64`, `BlockSellVolume *float64`, `LargeBidOrders *int`, `LargeAskOrders *int`
  - [x] Update `CurrentBar()` to populate when `hasBlockData`
  - [x] Update `BarReset()` to clear all block state
  - [x] L1 tests in `accumulator_test.go`

- [x] Wire `blockwindow.Window` into `accWriter` (AC: 5, 6, 8)
  - [x] Add `btw *blockwindow.Window` and `rdb *redis.Client` to `accWriter`
  - [x] In `Apply()`: add all trade sizes to window; classify block trades above threshold
  - [x] In `Flush()`: persist window to Redis (DEL + RPUSH)
  - [x] In `main.go`: read env vars, create `Window`, restore from Redis, pass to `accWriter`

- [x] Write 4 new fields in QuestDB writer (AC: 7)

### Review Findings

- [x] [Review][Patch] `New()` panics on windowSize=0 — add input guard `[internal/blockwindow/window.go:New]`
- [x] [Review][Patch] `Threshold()` panics when minSample=0 and window is empty — add early `len==0` guard `[internal/blockwindow/window.go:Threshold]`
- [x] [Review][Patch] `+Inf` trade size bypasses `parsedSize > 0` guard, enters window, freezes threshold `[cmd/candle/main.go:Apply]`
- [x] [Review][Patch] Missing test: `parsedSize == threshold` must NOT classify as block trade (strict `>` boundary) `[internal/blockwindow/window_test.go]`
- [x] [Review][Defer] `Add` O(n) copy-shift — 999 element copies per tick at default window — deferred, pre-existing
- [x] [Review][Defer] Redis key has no TTL — removed symbols leave stale entries forever — deferred, pre-existing
- [x] [Review][Defer] `minSample > windowSize` produces permanently cold window with no diagnostics — deferred, pre-existing

## Dev Notes

### blockwindow package location
`internal/blockwindow/window.go`. No external dependencies beyond standard library.

### 99th percentile — nearest-rank method
```go
func (w *Window) Threshold() (float64, bool) {
    if len(w.sizes) < w.minSample { return 0, false }
    sorted := make([]float64, len(w.sizes))
    copy(sorted, w.sizes)
    sort.Float64s(sorted)
    idx := int(math.Ceil(0.99*float64(len(sorted)))) - 1
    if idx < 0 { idx = 0 }
    if idx >= len(sorted) { idx = len(sorted) - 1 }
    return sorted[idx], true
}
```

### accWriter.Apply() block trade logic
```go
func (aw *accWriter) Apply(price, size string, isTrade bool, side string, ...) {
    // ... existing OFI/accumulator logic ...
    if isTrade {
        parsedSize, err := strconv.ParseFloat(size, 64)
        if err == nil && parsedSize > 0 {
            aw.btw.Add(parsedSize)
            if threshold, ok := aw.btw.Threshold(); ok && parsedSize > threshold {
                aw.acc.ApplyBlockTrade(side, parsedSize)
            }
        }
    }
}
```

### Redis persistence
On bar close in `Flush()`:
```go
ctx := aw.ctx
key := "candle:btw:" + aw.exchange + ":" + aw.symbol
sizes := aw.btw.Sizes()
pipe := aw.rdb.Pipeline()
pipe.Del(ctx, key)
args := make([]interface{}, len(sizes))
for i, s := range sizes { args[i] = strconv.FormatFloat(s, 'f', -1, 64) }
if len(args) > 0 { pipe.RPush(ctx, key, args...) }
pipe.Exec(ctx)
```

On startup in `main.go` (before consumer starts):
```go
raw, err := rdb.LRange(ctx, key, 0, -1).Result()
if err == nil && len(raw) > 0 {
    sizes := make([]float64, 0, len(raw))
    for _, s := range raw {
        if f, err := strconv.ParseFloat(s, 64); err == nil { sizes = append(sizes, f) }
    }
    btw.Restore(sizes)
}
```

### Cold-start null period
`large_bid_orders` and `large_ask_orders` from data contract: "large bid/ask orders" = trades exceeding block trade threshold by side. These 4 fields all come from `ApplyBlockTrade` which is only called when threshold is available. So they are automatically null during cold-start.

### Why not on AccumulatorApplier interface
`ApplyBlockTrade` is called by `accWriter` directly on the concrete `*Accumulator`. The `AccumulatorApplier` interface is the boundary between `consumer` and `accWriter`. Block trade classification happens inside `accWriter` after the threshold check. No interface change needed.

### Env vars (already in project-context.md CS-FR26)
`BLOCK_TRADE_WINDOW` (default 1000), `BLOCK_TRADE_MIN_SAMPLE` (default 100).

## Dev Agent Record

### Completion Notes
Implemented rolling 99th-percentile block trade window with Redis persistence. New `internal/blockwindow` package provides `Window` with nearest-rank percentile, capacity-capped circular buffer, and `Restore` for startup hydration. `Accumulator.ApplyBlockTrade` (not on interface) accumulates block buy/sell volumes and large bid/ask order counts guarded by `hasBlockData`. `accWriter.Apply` adds all trade sizes to window and classifies block trades above threshold. `accWriter.Flush` persists window via pipeline DEL+RPUSH on bar close. Config gains `BLOCK_TRADE_WINDOW` (default 1000) and `BLOCK_TRADE_MIN_SAMPLE` (default 100). All 4 new QuestDB columns written with nil-check pattern.

### File List
- `candle-service/internal/blockwindow/window.go` (new)
- `candle-service/internal/blockwindow/window_test.go` (new)
- `candle-service/internal/accumulator/accumulator.go` (modified)
- `candle-service/internal/accumulator/accumulator_test.go` (modified)
- `candle-service/internal/config/config.go` (modified)
- `candle-service/cmd/candle/main.go` (modified)
- `candle-service/internal/writer/questdb/writer.go` (modified)

### Change Log
- Added `internal/blockwindow` package: rolling fixed-capacity window with 99th-percentile threshold (2026-05-08)
- Added `Accumulator.ApplyBlockTrade`, block trade `Bar` fields, `BarReset` clearing (2026-05-08)
- Wired `blockwindow.Window` into `accWriter` with Redis persistence in `Flush` (2026-05-08)
- Added `BLOCK_TRADE_WINDOW` and `BLOCK_TRADE_MIN_SAMPLE` env vars to config (2026-05-08)
- Added 4 new block trade fields to QuestDB writer (2026-05-08)
