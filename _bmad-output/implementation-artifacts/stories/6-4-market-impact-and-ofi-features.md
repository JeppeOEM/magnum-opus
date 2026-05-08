# Story 6.4: Market Impact and OFI Features

Status: done

## Story

As a developer,
I want market impact features (`depth_to_1pct_bid`, `depth_to_1pct_ask`) computed per second and written to `snapshot_1s`, and OFI feature correctness confirmed,
so that the Epic 6 "Done when" criterion is fully met: all OB-derived fields are populated.

## Acceptance Criteria

1. `internal/features/depth.go` gains two pure functions:
   ```go
   // DepthTo1PctBid returns the cumulative bid volume available within 1% of the best bid price.
   // Returns 0 if bids is empty or bestBid is 0.
   func DepthTo1PctBid(bids map[string]string, bestBid float64) float64

   // DepthTo1PctAsk returns the cumulative ask volume available within 1% of the best ask price.
   // Returns 0 if asks is empty or bestAsk is 0.
   func DepthTo1PctAsk(asks map[string]string, bestAsk float64) float64
   ```
   - `DepthTo1PctBid`: sum all bid volume at prices >= `bestBid * 0.99`
   - `DepthTo1PctAsk`: sum all ask volume at prices <= `bestAsk * 1.01`
   - Parse errors on price/size: skip the level silently
   - Zero or negative size: skip silently (same as `parseLevels`)

2. `DepthSnapshot` struct in `internal/features/depth.go` gains two new fields:
   ```go
   DepthTo1PctBid float64
   DepthTo1PctAsk float64
   ```
   `ComputeDepthSnapshot()` populates these fields after computing depth tiers, using the best price (first element of sorted bid/ask levels respectively). If a side has no valid levels, the corresponding `DepthTo1Pct` field is 0.

3. `accumulator.Bar` gains two new pointer fields:
   ```go
   DepthTo1PctBid *float64 // nil if !hasCloseDepth
   DepthTo1PctAsk *float64 // nil if !hasCloseDepth
   ```
   These are bar-close fields (computed from close depth snapshot, same as `WeightedBidPrice`).

4. `CurrentBar()` populates `DepthTo1PctBid` and `DepthTo1PctAsk` from `a.closeDepth` when `a.hasCloseDepth`.

5. `internal/writer/questdb/writer.go` writes `depth_to_1pct_bid` and `depth_to_1pct_ask` using the existing nil-check pattern (in the book shape section alongside weighted prices).

6. OFI state confirmed: `ofi` and `ofi_l1` both already written with the same `bar.OFI` value (added in Story 6-2 review fix). No code change needed; this AC is a verification checkpoint only.

7. `go test ./...` passes with zero failures. New L1 tests cover:
   - `DepthTo1PctBid` with a multi-level book: only volume within 1% threshold counted
   - `DepthTo1PctAsk` with a multi-level book: only volume within 1% threshold counted
   - Both functions return 0 for empty/nil input
   - `ComputeDepthSnapshot` populates `DepthTo1PctBid`/`DepthTo1PctAsk` correctly
   - `DepthTo1PctBid`/`DepthTo1PctAsk` are nil in `Bar` when `!hasCloseDepth`

## Tasks / Subtasks

- [x] Add `DepthTo1PctBid` and `DepthTo1PctAsk` functions to `internal/features/depth.go` (AC: 1)
  - [x] `DepthTo1PctBid(bids map[string]string, bestBid float64) float64`
  - [x] `DepthTo1PctAsk(asks map[string]string, bestAsk float64) float64`
  - [x] L1 tests for both functions

- [x] Add `DepthTo1PctBid`/`DepthTo1PctAsk` fields to `DepthSnapshot` and wire `ComputeDepthSnapshot` (AC: 2)
  - [x] Add two fields to `DepthSnapshot` struct
  - [x] Compute them inside `ComputeDepthSnapshot()` after sorting levels
  - [x] L1 test: `ComputeDepthSnapshot` populates them correctly

- [x] Add `DepthTo1PctBid`/`DepthTo1PctAsk` to `accumulator.Bar` and `CurrentBar()` (AC: 3, 4)
  - [x] Add two new `*float64` fields to `Bar`
  - [x] Populate in `CurrentBar()` from `closeDepth` when `hasCloseDepth`

- [x] Write `depth_to_1pct_bid` and `depth_to_1pct_ask` in QuestDB writer (AC: 5)
  - [x] Add 2 nil-checked `Float64Column` calls in `writeBar()`

- [x] Verify OFI correctness (AC: 6)
  - [x] Confirm `ofi` and `ofi_l1` both written in `writeBar()` — no code change needed

## Dev Notes

### DepthTo1PctBid / DepthTo1PctAsk semantics

For bid side (market impact of selling):
```
threshold = bestBid * 0.99
DepthTo1PctBid = Σ size for all bids where price >= threshold
```

For ask side (market impact of buying):
```
threshold = bestAsk * 1.01
DepthTo1PctAsk = Σ size for all asks where price <= threshold
```

This represents the total volume available within a 1% price move from the best quote — a proxy for how much can be traded before the price moves 1%.

### Where bestBid/bestAsk come from in ComputeDepthSnapshot

After sorting bid levels descending: `bestBid = bidLvl[0].price` (if non-empty).
After sorting ask levels ascending: `bestAsk = askLvl[0].price` (if non-empty).

The standalone `DepthTo1PctBid(bids, bestBid)` and `DepthTo1PctAsk(asks, bestAsk)` functions take the best price as an explicit parameter for testability and reuse outside `ComputeDepthSnapshot`.

Inside `ComputeDepthSnapshot`, after sorting:
```go
if len(bidLvl) > 0 {
    d.DepthTo1PctBid = DepthTo1PctBid(bids, bidLvl[0].price)
}
if len(askLvl) > 0 {
    d.DepthTo1PctAsk = DepthTo1PctAsk(asks, askLvl[0].price)
}
```

Note: use the original `bids`/`asks` maps (not `bidLvl`/`askLvl` which are already sorted slices) since the functions parse maps directly.

### CurrentBar() additions

In the `if a.hasCloseDepth` block (alongside existing WeightedBidPrice/WeightedAskPrice):
```go
if a.closeDepth.DepthTo1PctBid > 0 || /* always write */ true {
    bar.DepthTo1PctBid = ptr(a.closeDepth.DepthTo1PctBid)
    bar.DepthTo1PctAsk = ptr(a.closeDepth.DepthTo1PctAsk)
}
```
Actually simpler: always populate both when `hasCloseDepth`, even if value is 0:
```go
if a.hasCloseDepth {
    // ... existing 16 fields ...
    bar.DepthTo1PctBid = ptr(a.closeDepth.DepthTo1PctBid)
    bar.DepthTo1PctAsk = ptr(a.closeDepth.DepthTo1PctAsk)
}
```

### OFI verification

In `internal/writer/questdb/writer.go`, the OFI section now reads:
```go
row = row.Float64Column("ofi", bar.OFI).
    Float64Column("ofi_l1", bar.OFI)
```
Both columns write `bar.OFI`. This is correct per the architecture decision. No change needed.

## Dev Agent Record

### Completion Notes
All ACs satisfied. `DepthTo1PctBid`/`DepthTo1PctAsk` added as standalone functions and struct fields in `depth.go`. `ComputeDepthSnapshot()` populates both from the sorted best prices. `Bar` gains two new close-only pointer fields populated in `CurrentBar()` from `closeDepth`. Writer writes `depth_to_1pct_bid` and `depth_to_1pct_ask`. OFI/OFI_L1 confirmed both written. Also applied Story 6-3 review fixes: `accumDepth` uses explicit count instead of `l2==0` sentinel; `Flush()` guards `SetCloseDepth` on non-empty OB; test field values made distinct to expose swap bugs. All tests pass with `-race`.

### File List
- `internal/features/depth.go` — added `DepthTo1PctBid`/`DepthTo1PctAsk` fields to `DepthSnapshot`, standalone functions, wired in `ComputeDepthSnapshot`; fixed `accumDepth` sentinel
- `internal/features/depth_test.go` — added 8 new L1 tests (DepthTo1PctBid, DepthTo1PctAsk, ComputeDepthSnapshot 1pct fields)
- `internal/accumulator/accumulator.go` — added `DepthTo1PctBid`/`DepthTo1PctAsk` `*float64` to `Bar`; `CurrentBar()` populates from `closeDepth`
- `internal/accumulator/accumulator_test.go` — fixed distinct field values in SetOpenDepth test; added 2 new 1pct tests
- `internal/writer/questdb/writer.go` — added 2 nil-checked `Float64Column` writes
- `cmd/candle/main.go` — `Flush()` guards `SetCloseDepth` on non-empty OB

### Change Log
- Added `DepthTo1PctBid`/`DepthTo1PctAsk` features and wired through accumulator → writer (2026-05-08)
- Applied Story 6-3 review fixes: accumDepth sentinel, Flush guard, distinct test values (2026-05-08)
