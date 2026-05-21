---
id: 35-4
title: 5-level depth snapshot extension (L3, L4, L5)
epic: 35
status: ready-for-dev
---

# Story 35-4: 5-Level Depth Snapshot Extension (L3, L4, L5)

## Context

`DepthSnapshot` currently captures cumulative depth at L1, L2, top10, and total. Adding L3, L4, L5 gives the ML model fine-grained order book shape as features. The slope of the depth profile (how quickly size builds above/below best quote) is a strong indicator of support/resistance quality.

This is a pure data extension — the computation pattern in `accumDepth()` already handles the level counting; we just capture 3 more breakpoints. 12 new columns total (L3/L4/L5 × bid/ask × open/close).

## What to build

### `candle-service/internal/features/depth.go`

Extend `DepthSnapshot` struct (after `BidL2, AskL2`):

```go
BidL3, AskL3 float64
BidL4, AskL4 float64
BidL5, AskL5 float64
```

Extend `accumDepth()` signature and body to return l3, l4, l5:

```go
func accumDepth(levels []priceLevel) (l1, l2, l3, l4, l5, top10, total, weighted float64) {
    var priceVolumeSum float64
    for i, lv := range levels {
        total += lv.size
        priceVolumeSum += lv.price * lv.size
        switch i {
        case 0:
            l1 = total
        case 1:
            l2 = total
        case 2:
            l3 = total
        case 3:
            l4 = total
        case 4:
            l5 = total
        case 9:
            top10 = total
        }
    }
    n := len(levels)
    if n < 2  { l2 = total }
    if n < 3  { l3 = total }
    if n < 4  { l4 = total }
    if n < 5  { l5 = total }
    if n < 10 { top10 = total }
    if total > 0 {
        weighted = priceVolumeSum / total
    }
    return
}
```

Update `ComputeDepthSnapshot()` to capture the new return values:

```go
d.BidL1, d.BidL2, d.BidL3, d.BidL4, d.BidL5, d.BidTop10, d.BidTotal, d.WeightedBidPrice = accumDepth(bidLvl)
d.AskL1, d.AskL2, d.AskL3, d.AskL4, d.AskL5, d.AskTop10, d.AskTotal, d.WeightedAskPrice = accumDepth(askLvl)
```

### `candle-service/internal/accumulator/accumulator.go`

Add 12 fields to `Bar` struct (after `AskDepthL2Close *float64`, before `BidDepthTop10Open`):

```go
// OB depth at open — L3/L4/L5
BidDepthL3Open *float64
AskDepthL3Open *float64
BidDepthL4Open *float64
AskDepthL4Open *float64
BidDepthL5Open *float64
AskDepthL5Open *float64

// OB depth at close — L3/L4/L5
BidDepthL3Close *float64
AskDepthL3Close *float64
BidDepthL4Close *float64
AskDepthL4Close *float64
BidDepthL5Close *float64
AskDepthL5Close *float64
```

In `CurrentBar()`, in the `hasOpenDepth` block (where L1/L2 open fields are set), add:

```go
bar.BidDepthL3Open = ptr(a.openDepth.BidL3)
bar.AskDepthL3Open = ptr(a.openDepth.AskL3)
bar.BidDepthL4Open = ptr(a.openDepth.BidL4)
bar.AskDepthL4Open = ptr(a.openDepth.AskL4)
bar.BidDepthL5Open = ptr(a.openDepth.BidL5)
bar.AskDepthL5Open = ptr(a.openDepth.AskL5)
```

In the `hasCloseDepth` block (where L1/L2 close fields are set), add:

```go
bar.BidDepthL3Close = ptr(a.closeDepth.BidL3)
bar.AskDepthL3Close = ptr(a.closeDepth.AskL3)
bar.BidDepthL4Close = ptr(a.closeDepth.BidL4)
bar.AskDepthL4Close = ptr(a.closeDepth.AskL4)
bar.BidDepthL5Close = ptr(a.closeDepth.BidL5)
bar.AskDepthL5Close = ptr(a.closeDepth.AskL5)
```

### `candle-service/migrations/030_depth_l3_l4_l5.sql`

```sql
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS bid_depth_l3_open  DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS ask_depth_l3_open  DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS bid_depth_l4_open  DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS ask_depth_l4_open  DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS bid_depth_l5_open  DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS ask_depth_l5_open  DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS bid_depth_l3_close DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS ask_depth_l3_close DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS bid_depth_l4_close DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS ask_depth_l4_close DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS bid_depth_l5_close DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS ask_depth_l5_close DOUBLE;
```

### `candle-service/internal/writer/questdb/writer.go`

After the existing L2 close depth block, add:

```go
if bar.BidDepthL3Close != nil {
    row = row.Float64Column("bid_depth_l3_close", *bar.BidDepthL3Close)
    row = row.Float64Column("ask_depth_l3_close", *bar.AskDepthL3Close)
    row = row.Float64Column("bid_depth_l4_close", *bar.BidDepthL4Close)
    row = row.Float64Column("ask_depth_l4_close", *bar.AskDepthL4Close)
    row = row.Float64Column("bid_depth_l5_close", *bar.BidDepthL5Close)
    row = row.Float64Column("ask_depth_l5_close", *bar.AskDepthL5Close)
}
if bar.BidDepthL3Open != nil {
    row = row.Float64Column("bid_depth_l3_open", *bar.BidDepthL3Open)
    row = row.Float64Column("ask_depth_l3_open", *bar.AskDepthL3Open)
    row = row.Float64Column("bid_depth_l4_open", *bar.BidDepthL4Open)
    row = row.Float64Column("ask_depth_l4_open", *bar.AskDepthL4Open)
    row = row.Float64Column("bid_depth_l5_open", *bar.BidDepthL5Open)
    row = row.Float64Column("ask_depth_l5_open", *bar.AskDepthL5Open)
}
```

### Tests — `candle-service/internal/features/depth_test.go`

```go
func TestAccumDepth_FiveLevels(t *testing.T) {
    // 5 levels of size 10 each
    levels := []priceLevel{
        {100, 10}, {99, 10}, {98, 10}, {97, 10}, {96, 10},
    }
    l1, l2, l3, l4, l5, top10, total, _ := accumDepth(levels)
    assert.Equal(t, 10.0, l1)
    assert.Equal(t, 20.0, l2)
    assert.Equal(t, 30.0, l3)
    assert.Equal(t, 40.0, l4)
    assert.Equal(t, 50.0, l5)
    assert.Equal(t, 50.0, top10) // < 10 levels → cap at total
    assert.Equal(t, 50.0, total)
}

func TestAccumDepth_TwoLevels_CapsL3L4L5(t *testing.T) {
    levels := []priceLevel{{100, 5}, {99, 5}}
    _, _, l3, l4, l5, _, total, _ := accumDepth(levels)
    assert.Equal(t, total, l3)
    assert.Equal(t, total, l4)
    assert.Equal(t, total, l5)
}

func TestComputeDepthSnapshot_L3L4L5_Set(t *testing.T) {
    bids := map[string]string{"100": "1", "99": "2", "98": "3", "97": "4", "96": "5"}
    asks := map[string]string{"101": "1", "102": "2", "103": "3", "104": "4", "105": "5"}
    d := ComputeDepthSnapshot(bids, asks)
    assert.Equal(t, 6.0, d.BidL3)  // 1+2+3
    assert.Equal(t, 10.0, d.BidL4) // 1+2+3+4
    assert.Equal(t, 15.0, d.BidL5) // 1+2+3+4+5
}
```

## Acceptance Criteria

1. `DepthSnapshot` has `BidL3, AskL3, BidL4, AskL4, BidL5, AskL5 float64` fields.
2. `accumDepth()` returns l3, l4, l5 as cumulative sizes through levels 3, 4, 5.
3. When fewer than N levels exist, lN = total (same capping behaviour as L2 and top10).
4. `ComputeDepthSnapshot()` populates all 6 new fields on both bid and ask sides.
5. `Bar` has 12 new `*float64` fields for L3/L4/L5 × open/close × bid/ask.
6. `CurrentBar()` sets all 12 fields from `openDepth`/`closeDepth` when those snapshots exist.
7. Migration `030_depth_l3_l4_l5.sql` adds all 12 columns.
8. ILP writer emits all 12 columns with nil guard on the first close-L3 field.
9. All 3 depth unit tests pass.

## Dev Notes

- The `accumDepth` function signature change breaks the existing call site in `ComputeDepthSnapshot` — update both the function and all callers in the same commit.
- Only `ComputeDepthSnapshot` calls `accumDepth` — search confirms no other callers.
- The nil guard on the ILP writer can use `BidDepthL3Close` as the sentinel (all 6 close fields are set together, and all 6 open fields are set together).
- No Redis publisher changes needed — ob_features stream is for live OB display, not ML feature transmission.
