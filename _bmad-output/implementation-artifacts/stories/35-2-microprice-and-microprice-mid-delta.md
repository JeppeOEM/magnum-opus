---
id: 35-2
title: Microprice and microprice_mid_delta
epic: 35
status: ready-for-dev
---

# Story 35-2: Microprice and microprice_mid_delta

## Context

Microprice is the depth-weighted fair value: `(best_ask × bid_l1 + best_bid × ask_l1) / (bid_l1 + ask_l1)`. When bid depth > ask depth, microprice > mid — the book is imbalanced toward buying and price is expected to rise. `microprice_mid_delta` (in bps) is the primary mean-reversion signal: when mid deviates from microprice, expect reversion back.

Both are derivable from close-of-bar state already present in the accumulator (`bestBid`, `bestAsk`, `closeDepth.BidL1`, `closeDepth.AskL1`). No new accumulator running state needed.

## What to build

### `candle-service/internal/features/features.go`

Add two pure functions after `EffectiveSpreadContrib`:

```go
// Microprice computes the depth-weighted fair value price.
// When total depth (bidL1+askL1) is zero, falls back to mid-price to avoid NaN.
func Microprice(bestBid, bestAsk, bidL1, askL1 float64) float64 {
    total := bidL1 + askL1
    if total == 0 {
        return MidPrice(bestBid, bestAsk)
    }
    return (bestAsk*bidL1 + bestBid*askL1) / total
}

// MicropriceMidDelta returns (microprice - mid) / mid × 10000 in basis points.
// Positive = microprice above mid (book weighted toward upward pressure).
// Returns 0 when mid is zero.
func MicropriceMidDelta(microprice, mid float64) float64 {
    if mid == 0 {
        return 0
    }
    return (microprice - mid) / mid * 10000.0
}
```

### `candle-service/internal/accumulator/accumulator.go`

Add to `Bar` struct (after `OFIL1 *float64`):

```go
Microprice         *float64 // nil if no close OB quote
MicropriceMidDelta *float64 // nil if no close OB quote; in basis points
```

In `CurrentBar()`, in the block guarded by `a.hasCloseQuote` (where `bar.BestBid` is set), add after setting `BestBid` and `BestAsk`:

```go
if a.hasCloseDepth {
    mp := features.Microprice(a.bestBid, a.bestAsk, a.closeDepth.BidL1, a.closeDepth.AskL1)
    mid := features.MidPrice(a.bestBid, a.bestAsk)
    bar.Microprice = ptr(mp)
    bar.MicropriceMidDelta = ptr(features.MicropriceMidDelta(mp, mid))
}
```

### `candle-service/migrations/028_microprice.sql`

```sql
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS microprice DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS microprice_mid_delta DOUBLE;
```

### `candle-service/internal/writer/questdb/writer.go`

After the `hawkes_intensity` block:

```go
if bar.Microprice != nil {
    row = row.Float64Column("microprice", *bar.Microprice)
}
if bar.MicropriceMidDelta != nil {
    row = row.Float64Column("microprice_mid_delta", *bar.MicropriceMidDelta)
}
```

### `candle-service/internal/writer/redis/publisher.go`

In `obFields()`, add to the returned map:

```go
"microprice":           floatOrEmpty(bar.Microprice),
"microprice_mid_delta": floatOrEmpty(bar.MicropriceMidDelta),
```

### Tests — `candle-service/internal/features/features_test.go`

```go
func TestMicroprice_Normal(t *testing.T) {
    // bestBid=100, bestAsk=101, bidL1=3, askL1=1
    // microprice = (101×3 + 100×1) / 4 = 403/4 = 100.75
    mp := Microprice(100, 101, 3, 1)
    assert.InDelta(t, 100.75, mp, 1e-9)
}

func TestMicroprice_ZeroDepthFallsBackToMid(t *testing.T) {
    mp := Microprice(100, 102, 0, 0)
    assert.InDelta(t, 101.0, mp, 1e-9) // mid = (100+102)/2
}

func TestMicropriceMidDelta_PositiveWhenMicropricAboveMid(t *testing.T) {
    // microprice=100.75, mid=100.5 → delta = (100.75-100.5)/100.5 * 10000 ≈ 24.9 bps
    delta := MicropriceMidDelta(100.75, 100.5)
    assert.InDelta(t, 24.875, delta, 0.01)
}

func TestMicropriceMidDelta_ZeroMidReturnsZero(t *testing.T) {
    assert.Equal(t, 0.0, MicropriceMidDelta(1.0, 0))
}

func TestMicropriceMidDelta_NegativeWhenMicropriceBelow(t *testing.T) {
    // mid=100.5, microprice=100.25 → negative bps
    delta := MicropriceMidDelta(100.25, 100.5)
    assert.True(t, delta < 0)
}
```

## Acceptance Criteria

1. `features.Microprice(bestBid, bestAsk, bidL1, askL1)` returns depth-weighted fair value.
2. When `bidL1 + askL1 = 0`, `Microprice()` returns mid-price (no NaN, no panic).
3. `features.MicropriceMidDelta(mp, mid)` returns bps; positive when microprice > mid.
4. When `mid = 0`, `MicropriceMidDelta()` returns 0.
5. `Bar.Microprice` and `Bar.MicropriceMidDelta` are set in `CurrentBar()` when `hasCloseQuote && hasCloseDepth`.
6. Both fields are nil when no close OB quote or close depth exists.
7. Migration `028_microprice.sql` adds both columns.
8. ILP writer emits both columns.
9. `obFields()` in Redis publisher includes `microprice` and `microprice_mid_delta`.
10. All 5 feature unit tests pass.

## Dev Notes

- `Microprice` weights prices by the **opposite** side's depth — if bidL1 is large, more weight on askPrice → microprice rises above mid.
- `hasCloseDepth` flag already exists on the accumulator; use it as the guard.
- The `bestBid`/`bestAsk` float64 fields already live on the accumulator struct — no string parsing needed at CurrentBar time.
