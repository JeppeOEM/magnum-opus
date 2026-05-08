# Story 6.2: Mid-Price and Spread Features

Status: done

## Story

As a developer,
I want mid-price path and spread features computed per second and written to `snapshot_1s`,
so that `mid_price_open`, `mid_price_high`, `mid_price_low`, `vwmp`, `spread_high`, `spread_low`, `spread_mean`, and `effective_spread` are populated for every second that has at least one OB or trade tick.

## Acceptance Criteria

1. `internal/features/` gains three pure, stateless functions:
   - `MidPrice(bid, ask float64) float64` — `(bid + ask) / 2`
   - `Spread(bid, ask float64) float64` — `ask - bid`
   - `EffectiveSpreadContrib(tradePrice, midPrice float64) float64` — `2 * abs(tradePrice - midPrice)` (per-trade contribution; caller accumulates)

2. `internal/accumulator/` gains `BarReset()` — bar-flush reset. Clears OHLCV, OFI, per-bar OB tracking, mid/spread state, and gap_count. Does NOT clear `lastKnownBid`/`lastKnownAsk`/`hasLastKnownOB`. `cmd/candle/accWriter.Flush()` MUST call `BarReset()` instead of `Reset()`. `Reset()` (gap/snapshot full reset) also clears `lastKnownBid`/`lastKnownAsk`/`hasLastKnownOB`.

3. Accumulator tracks per-bar mid-price state (updated on every tick where valid BestQuote is available — both OB delta and trade ticks):
   - `midPriceOpen` — mid at first valid OB tick of the second
   - `midPriceHigh`, `midPriceLow` — running max/min of mid-price across all ticks
   - `vwmpNumer`, `vwmpDenom` — for VWMP = `Σ(mid * tradeSize) / Σ(tradeSize)` across trade ticks only. If no trade ticks, `VWMP` is nil.

4. Accumulator tracks per-bar spread state (updated on every tick where valid BestQuote is available):
   - `spreadHigh`, `spreadLow` — running max/min of spread
   - `spreadSum`, `spreadCount` — for spread_mean = `spreadSum / spreadCount`
   - `effectiveSpreadSum`, `effectiveSpreadCount` — for effective_spread = `effectiveSpreadSum / effectiveSpreadCount`. Accumulated on trade ticks only using current mid at trade time. If no trade ticks, `EffectiveSpread` is nil.

5. `accumulator.Bar` gains new pointer fields (nil if no ticks this second):
   `MidPriceOpen`, `MidPriceHigh`, `MidPriceLow`, `VWMP`, `SpreadHigh`, `SpreadLow`, `SpreadMean`, `EffectiveSpread *float64`

6. `internal/writer/questdb/writer.go` writes all 8 new fields to `snapshot_1s` using the existing nil-check pattern.

7. `internal/consumer/consumer.go` EventGap handler calls `c.acc.IncrementGap()` (bug fix — currently missing; gap_count is always 0).

8. `go test ./...` passes with zero failures. New L1 tests cover: mid-price open/high/low tracking, VWMP with single and multiple trades, spread high/low/mean, effective_spread, BarReset() preserves lastKnownOB, Reset() clears lastKnownOB.

## Tasks / Subtasks

- [x] Add pure functions to `internal/features/features.go` (AC: 1)
  - [x] `MidPrice(bid, ask float64) float64`
  - [x] `Spread(bid, ask float64) float64`
  - [x] `EffectiveSpreadContrib(tradePrice, midPrice float64) float64`
  - [x] L1 tests for all three functions

- [x] Add `BarReset()` and `lastKnownOB` carry-forward to accumulator (AC: 2)
  - [x] Add `lastKnownBid`, `lastKnownAsk float64`, `hasLastKnownOB bool` fields to `Accumulator`
  - [x] `BarReset()`: clears OHLCV, OFI, per-bar tracking, mid/spread state, gap_count — does NOT clear `lastKnownOB`
  - [x] `Reset()`: calls `BarReset()` then also clears `lastKnownOB`
  - [x] Update `lastKnownBid`/`lastKnownAsk` in `Apply()` on every valid BestQuote
  - [x] Update `cmd/candle/accWriter.Flush()` to call `BarReset()` instead of `Reset()`

- [x] Add mid-price and spread state to accumulator (AC: 3, 4, 5)
  - [x] Add all new state fields listed in AC 3 and 4
  - [x] Update `Apply()` to compute and track mid-price and spread on every tick with valid BestQuote
  - [x] Update `Apply()` for VWMP and effective_spread accumulation on trade ticks
  - [x] Update `CurrentBar()` to populate all 8 new `Bar` fields
  - [x] Update `BarReset()` to clear all new state fields

- [x] Fix gap_count bug in consumer (AC: 7)
  - [x] Add `c.acc.IncrementGap()` to EventGap handler in `consumer.go`

- [x] Write new fields in QuestDB writer (AC: 6)
  - [x] Add all 8 nil-checked `Float64Column` calls in `writeBar()`

- [x] L1 tests (AC: 8)
  - [x] `BarReset()` preserves `lastKnownOB`, `Reset()` clears it
  - [x] mid-price open set on first tick, high/low update correctly
  - [x] VWMP: nil when no trades, correct value with one and multiple trades
  - [x] spread high/low/mean: single tick, multiple ticks
  - [x] effective_spread: nil when no trades, correct value with trade ticks
  - [x] Gap_count increments in consumer on gap events

## Dev Notes

### features/ function placement
`MidPrice`, `Spread`, `EffectiveSpreadContrib` are stateless. They go in `features/features.go` alongside `OFIDelta`. They take `float64` not `string` — the accumulator parses strings once in `Apply()` and uses floats internally.

### Apply() update pattern
On every tick where `currQuote.BidPrice != "" && currQuote.AskPrice != ""`:
```go
bid, ask := parsedBid, parsedAsk  // already parsed for OFI
mid := features.MidPrice(bid, ask)
spread := features.Spread(bid, ask)
// mid tracking
if !a.hasMidOpen { a.midPriceOpen = mid; a.hasMidOpen = true }
if mid > a.midPriceHigh || !a.hasSpread { a.midPriceHigh = mid }
if mid < a.midPriceLow  || !a.hasSpread { a.midPriceLow = mid }
// spread tracking
if spread > a.spreadHigh || !a.hasSpread { a.spreadHigh = spread }
if spread < a.spreadLow  || !a.hasSpread { a.spreadLow = spread }
a.spreadSum += spread
a.spreadCount++
a.hasSpread = true
// lastKnownOB
a.lastKnownBid = bid; a.lastKnownAsk = ask; a.hasLastKnownOB = true
```

On trade ticks only (after mid and spread computed):
```go
a.vwmpNumer += mid * tradeSize
a.vwmpDenom += tradeSize
a.effectiveSpreadSum += features.EffectiveSpreadContrib(tradePrice, mid)
a.effectiveSpreadCount++
```

### CurrentBar() nil logic
- `MidPriceOpen`, `MidPriceHigh`, `MidPriceLow` — nil if `!hasMidOpen`
- `VWMP` — nil if `vwmpDenom == 0`
- `SpreadHigh`, `SpreadLow`, `SpreadMean` — nil if `!hasSpread`
- `EffectiveSpread` — nil if `effectiveSpreadCount == 0`

### BarReset() vs Reset() contract
```go
func (a *Accumulator) BarReset() {
    // clears per-bar state only
    a.open = 0; /* ... all OHLCV ... */
    a.ofiSum = 0
    a.bestBidOpen = 0; a.bestAskOpen = 0; a.hasOpenQuote = false
    a.bestBid = 0; a.bestAsk = 0; a.hasCloseQuote = false
    a.midPriceOpen = 0; a.hasMidOpen = false
    a.midPriceHigh = 0; a.midPriceLow = 0
    a.hasSpread = false; a.spreadHigh = 0; a.spreadLow = 0
    a.spreadSum = 0; a.spreadCount = 0
    a.vwmpNumer = 0; a.vwmpDenom = 0
    a.effectiveSpreadSum = 0; a.effectiveSpreadCount = 0
    a.gapCount = 0
    // lastKnownBid/lastKnownAsk/hasLastKnownOB intentionally NOT cleared
}

func (a *Accumulator) Reset() {
    a.BarReset()
    a.lastKnownBid = 0; a.lastKnownAsk = 0; a.hasLastKnownOB = false
}
```

### gap_count bug fix
In `consumer.go` EventGap handler, add before `c.book.ApplyGap(...)`:
```go
c.acc.IncrementGap()
```
The `IncrementGap()` method already exists — it was just never called.

### writer.go additions
Follow the existing nil-check pattern exactly:
```go
if bar.MidPriceOpen != nil { row = row.Float64Column("mid_price_open", *bar.MidPriceOpen) }
// ... etc for all 8 fields
```

## Dev Agent Record

### Completion Notes
All ACs satisfied. Three pure functions added to `features/`. Accumulator gains `BarReset()`/`Reset()` split with `lastKnownOB` carry-forward, 9 new state fields, 8 new `Bar` pointer fields. `accWriter.Flush()` now calls `BarReset()`. `IncrementGap()` added to `AccumulatorApplier` interface and wired into the EventGap handler (bug fix). QuestDB writer writes all 8 new nil-checked columns. All tests pass with `-race`.

### File List
- `internal/features/features.go` — added `MidPrice`, `Spread`, `EffectiveSpreadContrib`
- `internal/features/features_test.go` — added tests for all three functions
- `internal/accumulator/accumulator.go` — added `BarReset()`, `lastKnownOB` fields, mid/spread state fields, updated `Apply()` and `CurrentBar()`
- `internal/accumulator/accumulator_test.go` — added 15 new L1 tests
- `internal/consumer/consumer.go` — added `IncrementGap()` to `AccumulatorApplier` interface; added `c.acc.IncrementGap()` call in EventGap handler
- `internal/consumer/consumer_l2_test.go` — added `IncrementGap()` to `stubAcc`; added `TestConsumer_GapEvent_IncrementsAccGapCount`
- `cmd/candle/main.go` — `accWriter.Flush()` calls `BarReset()` instead of `Reset()`; added `IncrementGap()` method to `accWriter`
- `internal/writer/questdb/writer.go` — added 8 nil-checked `Float64Column` writes

### Change Log
- Added `MidPrice`, `Spread`, `EffectiveSpreadContrib` pure functions (2026-05-08)
- Added `BarReset()` / two-level reset split with `lastKnownOB` carry-forward (2026-05-08)
- Fixed gap_count always-zero bug: `IncrementGap()` now called on EventGap (2026-05-08)
- Added 8 new mid-price and spread fields to `Bar` and QuestDB writer (2026-05-08)
