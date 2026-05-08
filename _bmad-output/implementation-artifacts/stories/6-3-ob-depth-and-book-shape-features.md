# Story 6.3: OB Depth and Book Shape Features

Status: done

## Story

As a developer,
I want OB depth (16 columns) and book shape (2 columns) computed per second and written to `snapshot_1s`,
so that `bid_depth_l1_open`, `ask_depth_l1_open`, `bid_depth_l2_open`, `ask_depth_l2_open`, `bid_depth_top10_open`, `ask_depth_top10_open`, `bid_depth_total_open`, `ask_depth_total_open`, `bid_depth_l1_close`, `ask_depth_l1_close`, `bid_depth_l2_close`, `ask_depth_l2_close`, `bid_depth_top10_close`, `ask_depth_top10_close`, `bid_depth_total_close`, `ask_depth_total_close`, `weighted_bid_price`, and `weighted_ask_price` are populated for every second that has at least one OB tick.

## Acceptance Criteria

1. `internal/features/` gains a pure `DepthSnapshot` struct and `ComputeDepthSnapshot()` function:
   ```go
   type DepthSnapshot struct {
       BidL1, AskL1         float64
       BidL2, AskL2         float64
       BidTop10, AskTop10   float64
       BidTotal, AskTotal   float64
       WeightedBidPrice     float64
       WeightedAskPrice     float64
       HasBidVolume         bool // false → WeightedBidPrice is zero/invalid
       HasAskVolume         bool // false → WeightedAskPrice is zero/invalid
   }

   func ComputeDepthSnapshot(bids, asks map[string]string) DepthSnapshot
   ```
   - `BidL1`/`AskL1` = best single level volume (TopNDepth equivalent: top 1)
   - `BidL2`/`AskL2` = top 2 levels cumulative volume
   - `BidTop10`/`AskTop10` = top 10 levels cumulative volume
   - `BidTotal`/`AskTotal` = all levels cumulative volume
   - `WeightedBidPrice` = Σ(price × size) / Σ(size) across all bid levels; `HasBidVolume=false` if `BidTotal==0`
   - `WeightedAskPrice` = Σ(price × size) / Σ(size) across all ask levels; `HasAskVolume=false` if `AskTotal==0`
   - Returns zero-value `DepthSnapshot` (all fields zero, HasBidVolume=false, HasAskVolume=false) if either map is nil/empty

2. `accumulator.Accumulator` gains `SetOpenDepth(d DepthSnapshot)` and `SetCloseDepth(d DepthSnapshot)` methods, plus state fields `openDepth DepthSnapshot`, `closeDepth DepthSnapshot`, `hasOpenDepth bool`, `hasCloseDepth bool`.

3. `accumulator.Bar` gains 18 new pointer fields (nil if no OB data this second):
   - `BidDepthL1Open`, `AskDepthL1Open`, `BidDepthL2Open`, `AskDepthL2Open`, `BidDepthTop10Open`, `AskDepthTop10Open`, `BidDepthTotalOpen`, `AskDepthTotalOpen *float64`
   - `BidDepthL1Close`, `AskDepthL1Close`, `BidDepthL2Close`, `AskDepthL2Close`, `BidDepthTop10Close`, `AskDepthTop10Close`, `BidDepthTotalClose`, `AskDepthTotalClose *float64`
   - `WeightedBidPrice`, `WeightedAskPrice *float64` — nil if `!HasBidVolume` / `!HasAskVolume`

4. `CurrentBar()` populates all 18 new fields from `hasOpenDepth`/`hasCloseDepth` state.

5. `BarReset()` clears `openDepth`, `closeDepth`, `hasOpenDepth`, `hasCloseDepth`.

6. `cmd/candle/accWriter` gains `ob *orderbook.OrderBook` field and an internal `openDepthSet bool` flag. Protocol:
   - In `Apply()`: if `!aw.openDepthSet` AND `currBid != "" && currAsk != ""` (valid OB quote), call `aw.acc.SetOpenDepth(features.ComputeDepthSnapshot(aw.ob.AllBids(), aw.ob.AllAsks()))` and set `aw.openDepthSet = true`.
   - In `Flush()`: call `aw.acc.SetCloseDepth(features.ComputeDepthSnapshot(aw.ob.AllBids(), aw.ob.AllAsks()))` BEFORE `CurrentBar()`, then `BarReset()`, then reset `aw.openDepthSet = false`.
   - In main.go: `aw := &accWriter{acc: acc, w: w, ob: ob, ctx: ctx}`.

7. `internal/writer/questdb/writer.go` writes all 18 new fields using the existing nil-check pattern. Column names match the DDL exactly.

8. `go test ./...` passes with zero failures. New L1 tests cover:
   - `ComputeDepthSnapshot` with a known multi-level book: correct L1/L2/Top10/Total and weighted prices
   - `ComputeDepthSnapshot` with empty maps returns zero-value with `HasBidVolume=false`
   - `ComputeDepthSnapshot` where book is deep but one side has zero volume returns correct values and `HasXVolume=false`
   - `SetOpenDepth` / `SetCloseDepth` / `CurrentBar()` — open and close depth fields populated correctly; nil when not set
   - `BarReset()` clears open/close depth state

## Tasks / Subtasks

- [x] Add `DepthSnapshot` struct and `ComputeDepthSnapshot()` to `internal/features/` (AC: 1)
  - [x] Define `DepthSnapshot` struct
  - [x] Implement `ComputeDepthSnapshot(bids, asks map[string]string) DepthSnapshot`
  - [x] Handle empty/nil maps (return zero-value)
  - [x] L1 tests for ComputeDepthSnapshot

- [x] Add depth state to accumulator (AC: 2, 3, 4, 5)
  - [x] Add `openDepth`, `closeDepth DepthSnapshot` + `hasOpenDepth`, `hasCloseDepth bool` fields
  - [x] Add `SetOpenDepth(d features.DepthSnapshot)` method
  - [x] Add `SetCloseDepth(d features.DepthSnapshot)` method
  - [x] Add 18 new pointer fields to `Bar`
  - [x] Update `CurrentBar()` to populate all 18 new fields
  - [x] Update `BarReset()` to clear open/close depth state
  - [x] L1 tests: SetOpenDepth/SetCloseDepth/CurrentBar, BarReset clears depth

- [x] Wire depth snapshot into `accWriter` (AC: 6)
  - [x] Add `ob *orderbook.OrderBook` and `openDepthSet bool` fields to `accWriter`
  - [x] Update `Apply()` to call `SetOpenDepth` on first valid OB tick
  - [x] Update `Flush()` to call `SetCloseDepth` before `CurrentBar()`, reset `openDepthSet = false` after `BarReset()`
  - [x] Update `main.go` to pass `ob` to `accWriter`

- [x] Write 18 new fields in QuestDB writer (AC: 7)
  - [x] Add 18 nil-checked `Float64Column` calls in `writeBar()`

## Dev Notes

### ComputeDepthSnapshot algorithm

Sort bid levels by price descending (best bid first), ask levels by price ascending (best ask first). Sum cumulative volumes for L1 (top 1), L2 (top 2), top10 (top 10), total (all). Weighted price = Σ(price × size) / BidTotal.

```go
func ComputeDepthSnapshot(bids, asks map[string]string) DepthSnapshot {
    var d DepthSnapshot
    bidLevels := sortedLevels(bids, true)  // descending
    askLevels := sortedLevels(asks, false) // ascending
    d.BidL1, d.BidL2, d.BidTop10, d.BidTotal, d.WeightedBidPrice = accumDepth(bidLevels)
    d.HasBidVolume = d.BidTotal > 0
    d.AskL1, d.AskL2, d.AskTop10, d.AskTotal, d.WeightedAskPrice = accumDepth(askLevels)
    d.HasAskVolume = d.AskTotal > 0
    if !d.HasBidVolume { d.WeightedBidPrice = 0 }
    if !d.HasAskVolume { d.WeightedAskPrice = 0 }
    return d
}
```

`sortedLevels` converts map[string]string to `[]priceLevel` (parsed float64 pairs) and sorts. `accumDepth` iterates in order, accumulating cumulative sums at 1, 2, 10, and total break points.

Parse errors on price/size: skip the level silently (same pattern as OB state machine).

### accWriter.Apply() open depth detection

```go
func (aw *accWriter) Apply(price, size string, isTrade bool, ..., currBid, currBidSz, currAsk, currAskSz string) {
    prev := features.BestQuote{...}
    curr := features.BestQuote{...}
    aw.acc.Apply(price, size, isTrade, prev, curr)
    if !aw.openDepthSet && currBid != "" && currAsk != "" {
        aw.acc.SetOpenDepth(features.ComputeDepthSnapshot(aw.ob.AllBids(), aw.ob.AllAsks()))
        aw.openDepthSet = true
    }
}
```

### accWriter.Flush() close depth

```go
func (aw *accWriter) Flush(isPartial bool) error {
    aw.acc.SetCloseDepth(features.ComputeDepthSnapshot(aw.ob.AllBids(), aw.ob.AllAsks()))
    tsSecMs := time.Now().Truncate(time.Second).UnixMilli()
    bar := aw.acc.CurrentBar(tsSecMs, isPartial)
    if err := aw.w.WriteBar(aw.ctx, bar); err != nil {
        return err
    }
    aw.acc.BarReset()
    aw.openDepthSet = false
    return nil
}
```

### CurrentBar() nil logic for depth fields
- Open depth fields: nil if `!hasOpenDepth`
- Close depth fields: nil if `!hasCloseDepth`
- `WeightedBidPrice`: nil if `!hasOpenDepth` (close) or `!openDepth.HasBidVolume`; similarly for close
- More precisely:
  ```go
  if a.hasOpenDepth {
      bar.BidDepthL1Open = ptr(a.openDepth.BidL1)
      // ... all 8 open depth fields
      if a.openDepth.HasBidVolume { ... }  // weighted price only if non-zero volume
  }
  ```
  Wait — `WeightedBidPrice` is a close-only field per the architecture ("bar-close snapshot only"). So:
  - `WeightedBidPrice` / `WeightedAskPrice` are populated from `closeDepth`, not `openDepth`.
  ```go
  if a.hasCloseDepth {
      // 8 close depth fields
      if a.closeDepth.HasBidVolume { bar.WeightedBidPrice = ptr(a.closeDepth.WeightedBidPrice) }
      if a.closeDepth.HasAskVolume { bar.WeightedAskPrice = ptr(a.closeDepth.WeightedAskPrice) }
  }
  ```

### Writer column names (exact match to DDL)
Open side: `bid_depth_l1_open`, `ask_depth_l1_open`, `bid_depth_l2_open`, `ask_depth_l2_open`, `bid_depth_top10_open`, `ask_depth_top10_open`, `bid_depth_total_open`, `ask_depth_total_open`
Close side: `bid_depth_l1_close`, `ask_depth_l1_close`, `bid_depth_l2_close`, `ask_depth_l2_close`, `bid_depth_top10_close`, `ask_depth_top10_close`, `bid_depth_total_close`, `ask_depth_total_close`
Book shape: `weighted_bid_price`, `weighted_ask_price`

### Nil vs zero depth
A depth field of zero (e.g., `BidL1=0` meaning no volume at best bid) should still be written as 0.0, not null. Only the nil pointer → NULL mapping applies when `!hasOpenDepth` / `!hasCloseDepth`. A valid depth snapshot with zero top-level volume writes 0.0.

### DepthSnapshot placement
Put `DepthSnapshot` and `ComputeDepthSnapshot` in a new file `internal/features/depth.go` to keep the features package organized. Do NOT add it to `features.go` (which has OFI/mid/spread). The test goes in `internal/features/depth_test.go`.

## Dev Agent Record

### Completion Notes
All ACs satisfied. `features.DepthSnapshot` + `ComputeDepthSnapshot()` added to `internal/features/depth.go`. Accumulator gains `SetOpenDepth`/`SetCloseDepth` + 18 new `Bar` pointer fields. `accWriter` gains `ob` reference and `openDepthSet` flag. Writer writes all 18 columns. All tests pass with `-race`.

### File List
- `internal/features/depth.go` — new file: DepthSnapshot struct, ComputeDepthSnapshot, parseLevels, accumDepth
- `internal/features/depth_test.go` — new file: 6 L1 tests
- `internal/accumulator/accumulator.go` — added 18 Bar fields, SetOpenDepth/SetCloseDepth methods, depth state fields, CurrentBar/BarReset updates
- `internal/accumulator/accumulator_test.go` — added 5 depth L1 tests
- `cmd/candle/main.go` — accWriter gains ob/openDepthSet, Apply wires SetOpenDepth, Flush wires SetCloseDepth
- `internal/writer/questdb/writer.go` — added 18 nil-checked Float64Column writes

### Change Log
- Added DepthSnapshot struct and ComputeDepthSnapshot() pure function (2026-05-08)
- Added SetOpenDepth/SetCloseDepth to accumulator and wired accWriter (2026-05-08)
- QuestDB writer now writes all 18 depth/book-shape columns (2026-05-08)
