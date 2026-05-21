---
id: 35-3
title: Cancel bias and trade aggressiveness
epic: 35
status: ready-for-dev
---

# Story 35-3: Cancel Bias and Trade Aggressiveness

## Context

Two spoofing/pressure features computable for free from existing accumulator counters:

**cancel_bias** — directional imbalance of order cancellations. `(bidCancels - askCancels) / (total + 1)`. Range [-1, 1]. Positive = more bid cancels (spoofed bid support). Negative = more ask cancels (spoofed ask resistance). The `+1` denominator prevents division by zero when no cancels occurred.

**trade_aggressiveness** — how hard the market is hitting relative to available liquidity. `volumeSum / (bidDepthL1_close + 1e-9)`. High values mean aggressive buying is depleting the bid stack (paradoxically, trade volume vs *bid* depth is the conventional measure — aggressive *buyers* consume ask liquidity but the ratio vs bid depth signals book depletion). Alternatively use total L1: `volumeSum / (bidL1 + askL1 + 1e-9)`.

Neither requires new accumulator running state — both computed at `CurrentBar()` time from existing counters.

## What to build

### `candle-service/internal/accumulator/accumulator.go`

Add to `Bar` struct (after `MicropriceMidDelta *float64`):

```go
CancelBias          *float64 // nil if no OB activity (hasOBActivity=false)
TradeAggressiveness *float64 // nil if no close depth (hasCloseDepth=false)
```

In `CurrentBar()`, after the microprice block:

```go
// Cancel bias — free from existing counters
if a.hasOBActivity {
    total := float64(a.bidCancelCount + a.askCancelCount)
    cb := (float64(a.bidCancelCount) - float64(a.askCancelCount)) / (total + 1.0)
    bar.CancelBias = ptr(cb)
}

// Trade aggressiveness — volume pressure vs available bid liquidity
if a.hasCloseDepth && a.closeDepth.BidL1 > 0 {
    bar.TradeAggressiveness = ptr(a.volumeSum / (a.closeDepth.BidL1 + 1e-9))
} else if a.hasCloseDepth {
    // depth exists but BidL1 = 0 — still emit 0 rather than nil
    bar.TradeAggressiveness = ptr(0.0)
}
```

### `candle-service/migrations/029_cancel_bias_trade_aggressiveness.sql`

```sql
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS cancel_bias DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS trade_aggressiveness DOUBLE;
```

### `candle-service/internal/writer/questdb/writer.go`

After the microprice block:

```go
if bar.CancelBias != nil {
    row = row.Float64Column("cancel_bias", *bar.CancelBias)
}
if bar.TradeAggressiveness != nil {
    row = row.Float64Column("trade_aggressiveness", *bar.TradeAggressiveness)
}
```

### Tests — `candle-service/internal/accumulator/accumulator_test.go`

```go
func TestCancelBias_SymmetricCancels(t *testing.T) {
    acc := newAccWithOBActivity(3, 3) // 3 bid cancels, 3 ask cancels
    bar := acc.CurrentBar(1000, false)
    require.NotNil(t, bar.CancelBias)
    // (3-3)/(3+3+1) = 0
    assert.InDelta(t, 0.0, *bar.CancelBias, 1e-9)
}

func TestCancelBias_BidHeavy(t *testing.T) {
    acc := newAccWithOBActivity(6, 2)
    bar := acc.CurrentBar(1000, false)
    // (6-2)/(6+2+1) = 4/9 ≈ 0.444
    assert.InDelta(t, 4.0/9.0, *bar.CancelBias, 1e-9)
}

func TestCancelBias_NoCancels_Zero(t *testing.T) {
    acc := newAccWithOBActivity(0, 0)
    bar := acc.CurrentBar(1000, false)
    require.NotNil(t, bar.CancelBias) // hasOBActivity=true from arrivals
    assert.InDelta(t, 0.0, *bar.CancelBias, 1e-9) // (0-0)/(0+0+1) = 0
}

func TestCancelBias_NilWhenNoOBActivity(t *testing.T) {
    acc := New("bybit", "BTCUSDT", fixedClock{})
    bar := acc.CurrentBar(1000, false)
    assert.Nil(t, bar.CancelBias)
}

func TestTradeAggressiveness_NilWhenNoCloseDepth(t *testing.T) {
    acc := New("bybit", "BTCUSDT", fixedClock{})
    bar := acc.CurrentBar(1000, false)
    assert.Nil(t, bar.TradeAggressiveness)
}
```

## Acceptance Criteria

1. `cancel_bias = (bidCancelCount - askCancelCount) / (bidCancelCount + askCancelCount + 1)`.
2. `cancel_bias` is 0.0 when both cancel counts are 0 (denominator = 1, numerator = 0).
3. `CancelBias` is nil when `hasOBActivity = false`.
4. `trade_aggressiveness = volumeSum / (closeDepth.BidL1 + 1e-9)`.
5. `TradeAggressiveness` is nil when `hasCloseDepth = false`.
6. `TradeAggressiveness` is 0.0 when `closeDepth.BidL1 = 0` but depth snapshot exists.
7. Migration `029_cancel_bias_trade_aggressiveness.sql` adds both columns.
8. ILP writer emits both columns with nil guards.
9. All 5 L1 tests pass.

## Dev Notes

- No new struct fields in `Accumulator` — purely derived at `CurrentBar()` time.
- `bidCancelCount`, `askCancelCount`, `hasOBActivity`, `volumeSum`, `closeDepth` are all existing accumulator fields.
- `TradeAggressiveness` uses bid L1 depth (not total) — this measures how much volume was traded relative to the best bid level's size, capturing aggressive selling behaviour.
