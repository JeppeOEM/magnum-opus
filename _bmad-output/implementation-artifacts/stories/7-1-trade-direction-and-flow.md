# Story 7.1: Trade Direction and Flow

Status: in-progress

## Story

As a developer,
I want `buy_volume` and `buy_count` computed per second and written to `snapshot_1s`,
so that CS-FR11 (trade direction classification) is satisfied for active seconds.

## Acceptance Criteria

1. `accumulator.Accumulator.Apply()` gains a `side string` parameter inserted after `isTrade bool`. For OB delta ticks, callers pass `""`. For trade ticks, callers pass `"buy"` or `"sell"` as received from the tick stream.

2. `consumer.AccumulatorApplier` interface (in `internal/consumer/consumer.go`) gains `side string` after `isTrade bool`. The consumer passes `parsed.Tick.Side` as `side` when calling `c.acc.Apply()`.

3. `accWriter.Apply()` in `cmd/candle/main.go` gains `side string` after `isTrade bool` and passes it through to `aw.acc.Apply()`.

4. `accumulator.Bar` gains two new pointer fields:
   ```go
   BuyVolume *float64 // nil when TradeCount==0
   BuyCount  *int     // nil when TradeCount==0
   ```

5. `accumulator.Accumulator` gains state fields `buyVolume float64`, `buyCount int`. In `Apply()`, when `isTrade && side == "buy"`: `a.buyVolume += parsedSize` and `a.buyCount++`.

6. `CurrentBar()` populates `BuyVolume = ptr(a.buyVolume)` and `BuyCount = ptrInt(a.buyCount)` only when `a.tradeCount > 0`.

7. `BarReset()` clears `buyVolume` and `buyCount`.

8. `internal/writer/questdb/writer.go` writes `buy_volume` and `buy_count` with the existing nil-check pattern:
   ```go
   if bar.BuyVolume != nil {
       row = row.Float64Column("buy_volume", *bar.BuyVolume)
   }
   if bar.BuyCount != nil {
       row = row.Int64Column("buy_count", int64(*bar.BuyCount))
   }
   ```

9. `go test ./...` passes with zero failures. New L1 tests cover:
   - All trades "buy": `BuyVolume == Volume`, `BuyCount == TradeCount`
   - All trades "sell": `BuyVolume == 0`, `BuyCount == 0`
   - Mixed buy/sell: correct partial accumulation
   - OB delta ticks (isTrade=false) do not affect BuyVolume/BuyCount
   - `BuyVolume`/`BuyCount` are nil when `TradeCount == 0`
   - `BarReset()` clears buy state

## Tasks / Subtasks

- [x] Add `side string` to `AccumulatorApplier` interface and consumer call site (AC: 2)
  - [x] Add `side string` after `isTrade bool` in `AccumulatorApplier.Apply()`
  - [x] Pass `parsed.Tick.Side` in `consumer.go` call to `c.acc.Apply()`
  - [x] Update `stubAcc.Apply()` in `consumer_l2_test.go`

- [x] Add `side string` to `accWriter.Apply()` (AC: 3)

- [x] Add `side string` to `accumulator.Accumulator.Apply()` and accumulate buy state (AC: 1, 4, 5, 6, 7)
  - [x] Add `side string` parameter to `Accumulator.Apply()`
  - [x] Accumulate `buyVolume`/`buyCount` when `isTrade && side == "buy"`
  - [x] Add `BuyVolume *float64`, `BuyCount *int` to `Bar`
  - [x] Add `ptrInt` helper or use `*int` approach
  - [x] `CurrentBar()` populates both when `tradeCount > 0`
  - [x] `BarReset()` clears buy state
  - [x] Update all existing test call sites in `accumulator_test.go` to pass side

- [x] Write `buy_volume` and `buy_count` in QuestDB writer (AC: 8)

- [x] Add L1 tests (AC: 9)

## Dev Notes

### Side semantics

`side = "buy"` → aggressor is buyer (lifted the ask) → increment `buyVolume` / `buyCount`.  
`side = "sell"` or `side = ""` → either seller-aggressor or non-trade tick → skip.  
`sell_volume = volume - buy_volume` and `sell_count = trade_count - buy_count` are derivable by consumers; not stored.

### BuyCount field type

`*int` is used for `BuyCount` to match the nil-pointer convention. A helper `ptrInt(i int) *int` is added alongside the existing `ptr(f float64) *float64` in `accumulator.go`.

### Apply() call sites to update

- `internal/consumer/consumer.go` — one site in the `EventTick` handler
- `internal/consumer/consumer_l2_test.go` — `stubAcc.Apply()` signature
- `cmd/candle/main.go` — `accWriter.Apply()` signature and delegation
- `internal/accumulator/accumulator_test.go` — all `acc.Apply(...)` calls (~30 sites)

### Existing tests

All existing `acc.Apply(...)` calls pass `""` for side (OB delta) or `"buy"/"sell"` for trade ticks. The side parameter is ignored when `!isTrade`, so passing `""` for all existing non-trade calls is correct. Existing tests only need mechanical signature updates, not new logic.

### DDL column names

- `buy_volume` — DOUBLE  
- `buy_count` — INT (written via `Int64Column`)

## Dev Agent Record

### Completion Notes
All ACs satisfied. `side string` parameter threaded from consumer interface through accWriter to Accumulator.Apply(). Buy volume and count accumulated when `isTrade && side == "buy"`. Bar gains BuyVolume/BuyCount pointer fields; CurrentBar populates them when tradeCount > 0; BarReset clears them. Writer writes both columns. All existing tests updated with mechanical side-parameter additions; new tests cover all AC-9 scenarios. All tests pass with -race.

### File List
- `internal/consumer/consumer.go` — AccumulatorApplier.Apply gains side string; consumer passes parsed.Tick.Side
- `internal/consumer/consumer_l2_test.go` — stubAcc.Apply gains side string
- `cmd/candle/main.go` — accWriter.Apply gains side string, passes through
- `internal/accumulator/accumulator.go` — Apply gains side; buyVolume/buyCount state; Bar fields; ptrInt helper; CurrentBar/BarReset updated
- `internal/accumulator/accumulator_test.go` — all Apply calls updated; 6 new L1 tests
- `internal/writer/questdb/writer.go` — 2 new nil-checked column writes

### Change Log
- Added trade direction (buy_volume, buy_count) accumulation and writer output (2026-05-08)
