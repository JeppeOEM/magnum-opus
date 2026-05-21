---
id: 35-6
title: Buy/sell VWAP deviation from mid
epic: 35
status: ready-for-dev
---

# Story 35-6: Buy/Sell VWAP Deviation from Mid

## Context

VWAP sell-to-mid deviation is the **#3 SHAP feature** in the arxiv:2602.00776 cryptocurrency microstructure study (1-second frequency, BTC/ETH/LTC, Jan 2022 – Oct 2025). The paper's finding: "VWAP deviations display asymmetric effects coherent with short-lived pressure and microstructure reversion as depth replenishes."

When aggressive sellers are hitting at prices well *below* mid, it creates a temporary downward pressure that typically reverts. The buy-side companion is symmetric. Together they form the directional version of the effective spread.

```
buy_vwap  = Σ(tradePrice × size | side=buy) / Σ(size | side=buy)
sell_vwap = Σ(tradePrice × size | side=sell) / Σ(size | side=sell)
buy_vwap_deviation_bps  = (buy_vwap  - mid) / mid × 10000
sell_vwap_deviation_bps = (sell_vwap - mid) / mid × 10000
```

Requires 4 new accumulator running fields (2 numer + 2 denom for buy/sell).

## What to build

### `candle-service/internal/accumulator/accumulator.go`

Add 4 fields to `Accumulator` struct (after `buyVolume float64`):

```go
buyVwapNumer  float64
buyVwapDenom  float64
sellVwapNumer float64
sellVwapDenom float64
```

In `Apply()`, inside the `isTrade` block after `a.buyVolume` and `a.buyCount` updates:

```go
// VWAP per side — for buy_vwap_deviation and sell_vwap_deviation
if p, err := strconv.ParseFloat(price, 64); err == nil {
    if s, err2 := strconv.ParseFloat(size, 64); err2 == nil && s > 0 {
        if side == "buy" {
            a.buyVwapNumer += p * s
            a.buyVwapDenom += s
        } else {
            a.sellVwapNumer += p * s
            a.sellVwapDenom += s
        }
    }
}
```

> Note: `price` and `size` are already parsed earlier in `Apply()` for OHLCV — reuse those parsed values rather than re-parsing if the struct is refactored. For now, parse again in the isolated block above.

Add to `Bar` struct (after `TradeAggressiveness *float64`):

```go
BuyVwapDeviationBps  *float64 // nil if no buy trades or no close OB quote
SellVwapDeviationBps *float64 // nil if no sell trades or no close OB quote
```

In `CurrentBar()`, after the trade aggressiveness block:

```go
// Buy/sell VWAP deviation from mid
if a.hasCloseQuote {
    mid := features.MidPrice(a.bestBid, a.bestAsk)
    if mid > 0 {
        if a.buyVwapDenom > 0 {
            buyVwap := a.buyVwapNumer / a.buyVwapDenom
            bar.BuyVwapDeviationBps = ptr((buyVwap - mid) / mid * 10000.0)
        }
        if a.sellVwapDenom > 0 {
            sellVwap := a.sellVwapNumer / a.sellVwapDenom
            bar.SellVwapDeviationBps = ptr((sellVwap - mid) / mid * 10000.0)
        }
    }
}
```

In `BarReset()`, zero all four fields:

```go
a.buyVwapNumer  = 0
a.buyVwapDenom  = 0
a.sellVwapNumer = 0
a.sellVwapDenom = 0
```

### `candle-service/migrations/031_buy_sell_vwap_deviation.sql`

```sql
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS buy_vwap_deviation_bps  DOUBLE;
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS sell_vwap_deviation_bps DOUBLE;
```

### `candle-service/internal/writer/questdb/writer.go`

After the trade aggressiveness block:

```go
if bar.BuyVwapDeviationBps != nil {
    row = row.Float64Column("buy_vwap_deviation_bps", *bar.BuyVwapDeviationBps)
}
if bar.SellVwapDeviationBps != nil {
    row = row.Float64Column("sell_vwap_deviation_bps", *bar.SellVwapDeviationBps)
}
```

### Tests

```go
func TestBuyVwapDeviation_BuyTradesAboveMid(t *testing.T) {
    // mid = 100, buy trade at 100.5 → positive bps
    acc := setupWithQuote("99.5", "100.5") // mid=100
    acc.Apply("100.5", "2", true, "buy", 1000, q, q)
    bar := acc.CurrentBar(1000, false)
    require.NotNil(t, bar.BuyVwapDeviationBps)
    assert.True(t, *bar.BuyVwapDeviationBps > 0)
}

func TestSellVwapDeviation_SellTradesBelowMid(t *testing.T) {
    // mid = 100, sell trade at 99.5 → negative bps
    acc := setupWithQuote("99.5", "100.5")
    acc.Apply("99.5", "2", true, "sell", 1000, q, q)
    bar := acc.CurrentBar(1000, false)
    require.NotNil(t, bar.SellVwapDeviationBps)
    assert.True(t, *bar.SellVwapDeviationBps < 0)
}

func TestBuyVwapDeviation_NilWhenNoBuyTrades(t *testing.T) {
    acc := setupWithQuote("99.5", "100.5")
    acc.Apply("99.5", "1", true, "sell", 1000, q, q)
    bar := acc.CurrentBar(1000, false)
    assert.Nil(t, bar.BuyVwapDeviationBps)
    assert.NotNil(t, bar.SellVwapDeviationBps)
}

func TestBuyVwapDeviation_NilWhenNoCloseQuote(t *testing.T) {
    acc := New("bybit", "BTCUSDT", fixedClock{})
    acc.Apply("100", "1", true, "buy", 1000, emptyQ, emptyQ)
    bar := acc.CurrentBar(1000, false)
    assert.Nil(t, bar.BuyVwapDeviationBps)
}

func TestVwapDeviation_ResetAfterBarReset(t *testing.T) {
    acc := setupWithQuote("99.5", "100.5")
    acc.Apply("100.5", "2", true, "buy", 1000, q, q)
    _ = acc.CurrentBar(1000, false)
    acc.BarReset()
    // next bar: no trades → both nil
    bar := acc.CurrentBar(2000, false)
    assert.Nil(t, bar.BuyVwapDeviationBps)
    assert.Nil(t, bar.SellVwapDeviationBps)
}
```

## Acceptance Criteria

1. `buyVwapNumer`, `buyVwapDenom`, `sellVwapNumer`, `sellVwapDenom` added to `Accumulator` struct.
2. In `Apply()`, buy trades update buy-side numer/denom; sell trades update sell-side.
3. `BuyVwapDeviationBps = (buyVWAP - mid) / mid × 10000` in bps when `buyVwapDenom > 0` and close quote valid.
4. `SellVwapDeviationBps` is nil when no sell trades occurred this bar.
5. Both fields nil when `hasCloseQuote = false`.
6. All 4 VWAP fields zeroed in `BarReset()`.
7. Migration `031_buy_sell_vwap_deviation.sql` adds both columns.
8. ILP writer emits both with nil guards.
9. All 5 L1 tests pass.

## Dev Notes

- `side` parameter is already available in `Apply()` — it carries "buy"/"sell" for trade ticks (empty string for OB delta ticks, which skip the trade block anyway).
- The price/size parse in the trade block can be done once and reused — the accumulator already parses them for OHLCV. A refactor to avoid double-parsing is welcome but not required.
- Migration number 031 — after 030_depth_l3_l4_l5.sql.
