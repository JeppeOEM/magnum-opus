# Story 7.3: Trade Distribution and Volatility

Status: done

## Story

As a developer,
I want trade distribution and volatility features computed per second and written to `snapshot_1s`,
so that CS-FR13 and CS-FR14 are satisfied, and `twap` is corrected to true time-weighted average price.

## Acceptance Criteria

### Interface change (prerequisite for all ACs below)

1. `AccumulatorApplier.Apply()` in `internal/consumer/consumer.go` gains `tsMs int64` after `side string`:
   ```go
   Apply(price, size string, isTrade bool, side string, tsMs int64,
         prevBid, prevBidSz, prevAsk, prevAskSz,
         currBid, currBidSz, currAsk, currAskSz string)
   ```
   Consumer passes `parsed.Tick.TsMs`. Update `accWriter.Apply()` and `Accumulator.Apply()` with the same parameter. Update `stubAcc` in `consumer_l2_test.go`.

### TWAP fix (CS-FR13 prerequisite — twap was wrongly computing VWAP)

2. Remove `twapNumer float64` from `Accumulator` (it duplicated `quoteVolSum`). Replace with true time-weighted TWAP state:
   - Add `twapNumer float64` (redefined: `Σ(price × Δt)`)
   - Add `twapDenom float64` (`Σ(Δt)`)
   - Add `lastTradeTsMs int64`
   On each trade tick (after we have `tsMs` from AC 1):
   ```
   if tradeCount > 0:   // there was a previous trade
       Δt = tsMs - lastTradeTsMs
       twapNumer += previousPrice × Δt    // previousPrice = current a.close (before update)
       twapDenom += Δt
   lastTradeTsMs = tsMs
   ```
   In `CurrentBar(tsSecMs int64, ...)`: finalize with last trade's hold time to bar end:
   ```
   if tradeCount > 0:
       barEndMs = tsSecMs + 1000
       Δt_final = barEndMs - lastTradeTsMs
       finalNum = twapNumer + a.close × Δt_final
       finalDen = twapDenom + Δt_final
       if finalDen > 0: bar.TWAP = ptr(finalNum / finalDen)
   ```
   `BarReset()` clears `twapNumer`, `twapDenom`, `lastTradeTsMs`.

### Trade distribution (CS-FR13)

3. `accumulator.Bar` gains seven new pointer fields (all nil when `TradeCount == 0`):
   ```go
   MaxTradeSize         *float64
   FirstTradeOffsetMs   *int
   LastTradeOffsetMs    *int
   TradeClustering      *float64 // nil when TradeCount < 2
   MaxConsecutiveRun    *int
   ```
   Note: `LargeBidOrders` and `LargeAskOrders` are handled by story 7-2 via `ApplyBlockTrade`.

4. Accumulator state additions for trade distribution:
   - `maxTradeSize float64`
   - `firstTradeTsMs int64` (set on first trade)
   - `tradeTimestamps []int64` (append each trade's tsMs; for Gini computation)
   - `consecutiveRunLen int`, `consecutiveRunSide string`, `maxConsecutiveRun int`
   On each trade:
   - `maxTradeSize = max(maxTradeSize, parsedSize)`
   - On first trade: `firstTradeTsMs = tsMs`
   - `tradeTimestamps = append(tradeTimestamps, tsMs)`
   - Consecutive run: if `side == consecutiveRunSide { consecutiveRunLen++ } else { consecutiveRunLen = 1; consecutiveRunSide = side }`; then `maxConsecutiveRun = max(maxConsecutiveRun, consecutiveRunLen)`.
   
   **`max_consecutive_run` is runs of same TRADE SIGN (side="buy" or side="sell"), NOT price direction.** Per data contract: "longest buy or sell run by trade sign."

5. `CurrentBar()` populates trade distribution fields when `tradeCount > 0`. Trade clustering (`TradeClustering`) requires `tradeCount >= 2` — nil otherwise. Gini formula on inter-trade intervals:
   ```
   intervals = [ts[1]-ts[0], ts[2]-ts[1], ..., ts[n-1]-ts[n-2]]
   sort intervals ascending
   G = Σ((2i - n - 1) × x[i]) / (n × Σ(x[i]))   // 0-indexed i
   ```
   Returns nil if all intervals are 0 (degenerate).

6. `BarReset()` clears all distribution state, including `tradeTimestamps = tradeTimestamps[:0]` (reuse backing array).

### Volatility (CS-FR14)

7. `accumulator.Bar` gains four new pointer fields:
   ```go
   RealizedVol       *float64 // nil when < 2 mid-price observations
   RealizedSkewness  *float64 // nil when < 3 mid-price observations or std dev == 0
   UptickCount       *int     // nil when TradeCount == 0
   DowntickCount     *int     // nil when TradeCount == 0
   ```

8. Accumulator state additions for volatility:
   - Online Welford moments for mid-price log-returns (updated on every tick with valid mid-price where the previous mid-price is also known):
     - `nMidReturns int`, `midReturnMean float64`, `midReturnM2 float64`, `midReturnM3 float64`
     - `lastMidPrice float64`, `hasLastMid bool`
   - On each tick where `hasMid == true` (valid currQuote bid+ask):
     ```
     if hasLastMid && lastMidPrice > 0:
         r = ln(mid / lastMidPrice)
         // Welford update for mean, M2, M3
         nMidReturns++
         delta = r - midReturnMean
         midReturnMean += delta / nMidReturns
         delta2 = r - midReturnMean
         midReturnM2 += delta × delta2
         midReturnM3 += delta × delta2 × (r - midReturnMean) // simplified 3rd moment update
     lastMidPrice = mid
     hasLastMid = true
     ```
     **Note:** `hasLastMid` and `lastMidPrice` survive `BarReset()` (same carry-forward semantics as `lastKnownBid`). `nMidReturns`, `midReturnMean`, `midReturnM2`, `midReturnM3` are reset by `BarReset()`.
   - Uptick/downtick: compare each trade price to previous trade price (uses `a.close` before update):
     - `uptickCount int`, `downtickCount int`, `lastTradePrice float64` (for comparison; separate from TWAP tracking)
     - Actually `lastTradeTsMs` is already tracked for TWAP; `a.close` is the last trade price before the current tick updates it. So: if `tradeCount > 0 && parsedPrice > a.close: uptickCount++` else if `parsedPrice < a.close: downtickCount++`.

9. `CurrentBar()` populates:
   - `RealizedVol`: `sqrt(M2 / (n-1))` when `nMidReturns >= 2`; nil otherwise.
   - `RealizedSkewness`: `(M3/n) / (M2/n)^(3/2)` (standardized 3rd moment) when `nMidReturns >= 3` and `M2/n > 0`; nil otherwise. Per data contract and project-context.md: null if std dev == 0.
   - `UptickCount`, `DowntickCount`: ptr when `tradeCount > 0`.

10. `BarReset()` clears volatility state. `Reset()` additionally clears `lastMidPrice`/`hasLastMid` (same pattern as `lastKnownBid`).

### Writer

11. `internal/writer/questdb/writer.go` writes all new fields with nil-check pattern. Column names match DDL exactly:
    `max_trade_size`, `first_trade_offset_ms`, `last_trade_offset_ms`, `trade_clustering`, `max_consecutive_run`, `realized_vol`, `realized_skewness`, `uptick_count`, `downtick_count`.
    `first_trade_offset_ms` and `last_trade_offset_ms` are INT in DDL — write as `Int64Column`.

12. `go test ./...` passes. New L1 tests cover:
    - True TWAP: single trade, multiple trades, verify time-weighting (not volume-weighting).
    - TWAP nil when no trades.
    - max_trade_size correct.
    - first/last_trade_offset_ms: correct ms offset from tsSecMs.
    - max_consecutive_run: all buy, mixed, alternating. Confirms it counts TRADE SIGN (side field), not price direction.
    - trade_clustering: nil for single trade; correct Gini for known interval set.
    - realized_vol: nil for < 2 mid observations; correct for known return series.
    - realized_skewness: nil for < 3; nil for zero-variance case.
    - uptick/downtick counts: correct on known price sequence.

## Tasks / Subtasks

- [x] Add `tsMs int64` to `AccumulatorApplier.Apply()` interface and all call sites (AC: 1)
  - [x] Update interface in `internal/consumer/consumer.go`
  - [x] Update `accWriter.Apply()` in `cmd/candle/main.go`
  - [x] Update `Accumulator.Apply()` in `internal/accumulator/accumulator.go`
  - [x] Update `stubAcc.Apply()` in `internal/consumer/consumer_l2_test.go`
  - [x] Update all `acc.Apply(...)` call sites in `accumulator_test.go`

- [x] Fix `twap` to true time-weighted; remove redundant `twapNumer` (AC: 2)
  - [x] Replace old `twapNumer` (=quoteVolSum) with TWAP-specific state fields
  - [x] Update `Apply()` TWAP accumulation
  - [x] Update `CurrentBar()` TWAP finalization using `tsSecMs + 1000`
  - [x] Update `BarReset()`
  - [x] L1 tests: verify TWAP differs from VWAP when trades are unevenly spaced

- [x] Add trade distribution state and Bar fields (AC: 3, 4, 5, 6)
  - [x] Add state fields to `Accumulator`
  - [x] Add 5 `Bar` pointer fields
  - [x] Update `Apply()` for distribution tracking
  - [x] Update `CurrentBar()` including Gini computation
  - [x] Update `BarReset()`
  - [x] L1 tests

- [x] Add volatility state and Bar fields (AC: 7, 8, 9, 10)
  - [x] Add online Welford state to `Accumulator`
  - [x] Add 4 `Bar` pointer fields
  - [x] Update `Apply()` for volatility tracking
  - [x] Update `CurrentBar()`
  - [x] Update `BarReset()` / `Reset()`
  - [x] L1 tests

- [x] Write new fields in QuestDB writer (AC: 11)

## Dev Notes

### Interface blast radius for `tsMs int64`
Adding `tsMs` to `Apply()` touches: `AccumulatorApplier` interface, `accWriter.Apply()`, `Accumulator.Apply()`, `stubAcc.Apply()` in test, all existing `acc.Apply(...)` calls in `accumulator_test.go`. Use sed to update test call sites — the new parameter is last before the OB quote block, so: `Apply(..., side, tsMs, prevBid, ...)`. Consumer passes `parsed.Tick.TsMs` (already `int64`, no parsing needed).

### True TWAP computation
TWAP = Σ(price_i × Δt_i) / Σ(Δt_i) where Δt_i = time price_i was held.

In `Apply()` (trade tick branch), execute BEFORE updating `a.close`:
```go
if a.tradeCount > 0 {  // there was a previous trade
    dt := tsMs - a.lastTradeTsMs
    if dt > 0 {
        a.twapNumer += a.close * float64(dt)
        a.twapDenom += float64(dt)
    }
}
a.lastTradeTsMs = tsMs
// ... then a.close = p (existing code)
```

In `CurrentBar()`:
```go
if a.tradeCount > 0 {
    barEndMs := tsSecMs + 1000
    dt := barEndMs - a.lastTradeTsMs
    if dt > 0 {
        finalNum := a.twapNumer + a.close*float64(dt)
        finalDen := a.twapDenom + float64(dt)
        if finalDen > 0 {
            bar.TWAP = ptr(finalNum / finalDen)
        }
    } else if a.twapDenom > 0 {
        bar.TWAP = ptr(a.twapNumer / a.twapDenom)
    }
}
```

Single-trade bar: `twapNumer=0, twapDenom=0` going into CurrentBar. `barEndMs - lastTradeTsMs = dt_final`. TWAP = `a.close × dt_final / dt_final = a.close`. Correct — single trade TWAP is that trade's price.

### max_consecutive_run — TRADE SIGN, not price direction
Use `side` field ("buy"/"sell"). NOT uptick/downtick. From data contract: "longest buy or sell run by trade sign."
```go
if a.tradeCount == 0 {
    a.consecutiveRunSide = side
    a.consecutiveRunLen = 1
} else if side == a.consecutiveRunSide {
    a.consecutiveRunLen++
} else {
    a.consecutiveRunLen = 1
    a.consecutiveRunSide = side
}
if a.consecutiveRunLen > a.maxConsecutiveRun {
    a.maxConsecutiveRun = a.consecutiveRunLen
}
```

### realized_vol uses MID-PRICE returns, not trade-price
Per data contract: "std dev of intra-second mid-price returns." The mid-price is computed on every tick (both OB delta and trade) where a valid quote exists. Log return = `ln(mid / prevMid)`. Updates happen in the OB-quote block of `Apply()`, not the trade-tick-only block.

`hasLastMid` and `lastMidPrice` survive `BarReset()` (carry-forward), same contract as `lastKnownBid`. `Reset()` clears them.

### Welford 3rd moment
Standard online skewness update (Chan et al. algorithm):
```go
delta := r - a.midReturnMean
a.nMidReturns++
a.midReturnMean += delta / float64(a.nMidReturns)
delta2 := r - a.midReturnMean
a.midReturnM2 += delta * delta2
a.midReturnM3 += delta * delta2 * (r - a.midReturnMean)
```

Skewness = `sqrt(float64(n)) × M3 / pow(M2, 1.5)` when n >= 3 and M2 > 0.
`math.Sqrt(float64(a.nMidReturns)) * a.midReturnM3 / math.Pow(a.midReturnM2, 1.5)`

### Uptick/downtick logic
In trade-tick branch of Apply(), before updating `a.close`:
```go
if a.tradeCount > 0 {
    if parsedPrice > a.close {
        a.uptickCount++
    } else if parsedPrice < a.close {
        a.downtickCount++
    }
    // price == prev: neither (zero tick)
}
```

### trade_clustering Gini nil guard
If all inter-trade intervals are 0 (multiple trades with same timestamp — possible in replay): sum = 0, return nil.

### first/last_trade_offset_ms
`tsSecMs` is passed to `CurrentBar()`. `first_trade_offset_ms = firstTradeTsMs - tsSecMs`. `last_trade_offset_ms = lastTradeTsMs - tsSecMs`. Both stored as int64; DDL type is INT — write as `Int64Column`. Values should be in [0, 999].

### Distinct test values
All accumulator tests must use distinct values for every asserted Bar field (lesson from Epic 6 retro).

## Dev Agent Record

### Completion Notes
Implemented true time-weighted TWAP (replacing VWAP duplicate). Added tsMs int64 to AccumulatorApplier.Apply() interface and all call sites. Added trade distribution fields: max_trade_size, first/last_trade_offset_ms, trade_clustering (Gini coefficient of inter-trade intervals), max_consecutive_run (by trade sign). Added volatility fields: realized_vol (Welford online stddev of mid-price log-returns), realized_skewness (standardized 3rd moment), uptick_count, downtick_count. lastMidPrice/hasLastMid carry forward across BarReset (same as lastKnownBid). All 9 new QuestDB columns written with nil-check pattern.

### File List
- `candle-service/internal/consumer/consumer.go` (modified — AccumulatorApplier.Apply interface + call site)
- `candle-service/internal/consumer/consumer_l2_test.go` (modified — stubAcc.Apply)
- `candle-service/internal/accumulator/accumulator.go` (modified — TWAP fix, trade distribution, volatility)
- `candle-service/internal/accumulator/accumulator_test.go` (modified — all Apply call sites + new tests)
- `candle-service/cmd/candle/main.go` (modified — accWriter.Apply)
- `candle-service/internal/writer/questdb/writer.go` (modified — 9 new columns)

### Change Log
- Added tsMs int64 to AccumulatorApplier interface and all call sites (2026-05-08)
- Fixed TWAP: replaced VWAP-duplicate with true time-weighted computation (2026-05-08)
- Added trade distribution state and Bar fields with Gini clustering (2026-05-08)
- Added Welford volatility (realized_vol, realized_skewness, uptick/downtick) (2026-05-08)
- Added 9 new QuestDB writer columns (2026-05-08)
