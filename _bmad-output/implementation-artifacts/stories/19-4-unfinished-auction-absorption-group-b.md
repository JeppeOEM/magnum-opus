# Story 19.4: Unfinished Auction + Single Prints + Absorption (Group B)

Status: done

## Story

As a developer,
I want the candle service to emit unfinished auction, single print, and absorption fields per bar,
So that the dashboard can mark unfilled price levels and absorption candles directly from candle stream data.

**Pre-conditions:** Story 19.1 complete (footprint + OHLCV fields on Bar).

## Definitions

- **Single print**: a price level within `[bar.Low, bar.High]` where total footprint volume (`buyVol + sellVol`) is < 10% of the mean per-level volume for that bar.
- **Unfinished top**: bar's High price level has `sellVol == 0` (only buyers at extreme).
- **Unfinished bottom**: bar's Low price level has `buyVol == 0` (only sellers at extreme).
- **Absorption**: `tradeCount >= 5` AND `buyVol/volume > 0.6` OR `buyVol/volume < 0.4` AND `abs(close - open) / close < 0.0005`.

## Acceptance Criteria

### AC 1 — ComputeAuctionSignals pure function

1. File `candle-service/internal/features/auction.go` contains:
   ```go
   type AuctionSignals struct {
       SinglePrintCount      int
       SinglePrintLevelsJSON string  // JSON array of price strings; "[]" if none
       UnfinishedTop         bool
       UnfinishedBottom      bool
   }
   func ComputeAuctionSignals(footprint map[string]FootprintCell, high, low float64) AuctionSignals
   ```
2. Algorithm:
   - Parse all footprint keys to float64; skip failures. Compute mean per-level volume = totalVol / n.
   - `SinglePrintCount` = count of levels where `(buyVol + sellVol) < 0.10 × meanVol` AND `level price` is in [low, high].
   - `SinglePrintLevelsJSON` = JSON array of the price strings for those levels, sorted ascending; `"[]"` if none.
   - `UnfinishedTop` = true iff the footprint contains an entry whose parsed price == high AND that entry's `SellVol == 0`.
   - `UnfinishedBottom` = true iff the footprint contains an entry whose parsed price == low AND that entry's `BuyVol == 0`.
3. Empty footprint or n==0 → zero-value AuctionSignals (SinglePrintCount=0, JSON="[]", booleans false).

### AC 2 — DetectAbsorption pure function

4. File `candle-service/internal/features/auction.go` also contains:
   ```go
   func DetectAbsorption(buyVol, totalVol, open, close float64, tradeCount int) bool
   ```
5. Returns true iff ALL: `tradeCount >= 5` AND (`buyVol/totalVol > 0.6` OR `buyVol/totalVol < 0.4`) AND `math.Abs(close-open)/close < 0.0005`.
6. Returns false when `totalVol == 0` (prevent divide by zero).

### AC 3 — New Bar fields

7. `accumulator.Bar` gains 5 new fields after `ImbalanceRatio *float64`:
   ```go
   SinglePrintCount      *int
   SinglePrintLevelsJSON *string
   UnfinishedTop         *bool
   UnfinishedBottom      *bool
   AbsorptionDetected    *bool
   ```
8. In `CurrentBar()`:
   - Inside the existing `if len(a.footprintMap) > 0` block, AND when OHLCV fields are present (high, low, close, open non-zero):
     - Call `features.ComputeAuctionSignals(a.footprintMap, a.high, a.low)` and set 4 fields.
     - Call `features.DetectAbsorption(a.buyVolume, a.volumeSum, a.open, a.close, a.tradeCount)` and set `AbsorptionDetected`.
   - Zero-trade bar: all 5 fields nil.

### AC 4 — Pubsub publisher

9. In `publisher.go::Publish1sBar()`, add after imbalance block:
   - `SinglePrintCount` and `SinglePrintLevelsJSON` gated on `bar.SinglePrintCount != nil`.
   - `UnfinishedTop` and `UnfinishedBottom` only emitted when true (omit false — booleans as `"true"` string only when true).
   - `AbsorptionDetected` only emitted when true.

### AC 5 — QuestDB migration

10. File `candle-service/migrations/006_auction.sql`:
    ```sql
    ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS single_print_count INT;
    ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS single_print_levels_json VARCHAR;
    ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS unfinished_top BOOLEAN;
    ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS unfinished_bottom BOOLEAN;
    ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS absorption_detected BOOLEAN;
    ```

### AC 6 — QuestDB ILP write

11. In `writer.go::writeBar()`, add after imbalance block — all 5 fields gated on `SinglePrintCount != nil`:
    - `Int64Column("single_print_count", ...)`, `StringColumn("single_print_levels_json", ...)`.
    - `BoolColumn("unfinished_top", ...)`, `BoolColumn("unfinished_bottom", ...)`, `BoolColumn("absorption_detected", ...)`.

### AC 7 — Flusher

12. `flusher/row.go` gains after imbalance fields:
    ```go
    SinglePrintCount      *int32  `parquet:"single_print_count"`
    SinglePrintLevelsJSON *string `parquet:"single_print_levels_json"`
    UnfinishedTop         *bool   `parquet:"unfinished_top"`
    UnfinishedBottom      *bool   `parquet:"unfinished_bottom"`
    AbsorptionDetected    *bool   `parquet:"absorption_detected"`
    ```
13. `flusher/flusher.go` gains 5 parse lines using `parseInt32`, `getString`, `parseBool`.

### AC 8 — L1 tests

14. `candle-service/internal/features/auction_test.go` covers:
    - **Empty footprint**: zero-value AuctionSignals.
    - **Single print detection**: bar with one high-volume level and one low-volume level (< 10% of mean) → SinglePrintCount=1.
    - **Unfinished top**: footprint entry at high has SellVol==0 → UnfinishedTop=true.
    - **Unfinished bottom**: footprint entry at low has BuyVol==0 → UnfinishedBottom=true.
    - **High/low not in footprint**: UnfinishedTop=false, UnfinishedBottom=false.
    - **Absorption boundary**: tradeCount=4 → false; skew=0.59 → false; skew=0.61 → true; tradeCount=5 + price barely moved → true.
    - **Absorption zero totalVol**: false.

## Tasks / Subtasks

- [ ] Task 1: ComputeAuctionSignals + DetectAbsorption
  - [ ] 1.1 Create `features/auction.go`
  - [ ] 1.2 Write L1 tests in `features/auction_test.go`
  - [ ] 1.3 Implement; tests pass

- [ ] Task 2: Bar fields + CurrentBar wiring
  - [ ] 2.1 Add 5 pointer fields to `accumulator.Bar`
  - [ ] 2.2 Wire in `CurrentBar()` inside `if len(footprintMap) > 0` block

- [ ] Task 3: Write paths
  - [ ] 3.1 Pubsub publisher (booleans only when true)
  - [ ] 3.2 Migration `006_auction.sql`
  - [ ] 3.3 QuestDB writer (all 5 gated on SinglePrintCount != nil)
  - [ ] 3.4 `flusher/row.go`, `flusher/flusher.go`

- [ ] Task 4: Full test suite passes

## Dev Notes

### Price matching for UnfinishedTop/Bottom

Match footprint key to high/low by parsing the key to float64 and comparing with tolerance (float comparison). Use `math.Abs(parsed - high) < 1e-9` to avoid float precision issues. The entry must actually exist in the footprint — if high is not in the footprint at all, UnfinishedTop=false.

### SinglePrintLevelsJSON

Use `encoding/json` to marshal `[]string` of sorted price strings. The "[]" sentinel is returned when count==0 — never nil/empty string. In publisher: only include `single_print_levels_json` when `SinglePrintLevelsJSON != nil && *bar.SinglePrintLevelsJSON != "[]"` or always include it when SinglePrintCount != nil.

### Booleans in pubsub

Per AC 4: omit-false pattern. Only emit `unfinished_top`, `unfinished_bottom`, `absorption_detected` when true. This differs from integer fields.

### Absorption formula detail

`abs(close-open)/close < 0.0005` — use `math.Abs`. Guard: `close > 0` (prices are always positive for crypto so this is safe, but guard against zero to prevent division by zero).

### flusher parseBool

Check if `parseBool` helper exists in `flusher/flusher.go`. If not, add it: parse "true"→true, anything else/empty→nil.

### File changes summary

| File | Change |
|---|---|
| `features/auction.go` | NEW |
| `features/auction_test.go` | NEW |
| `accumulator/accumulator.go` | Add 5 Bar fields; wire in CurrentBar |
| `writer/pubsub/publisher.go` | Add 5 fields (booleans omit-false) |
| `migrations/006_auction.sql` | NEW |
| `writer/questdb/writer.go` | Add 5 ILP writes |
| `flusher/row.go` | Add 5 parquet fields |
| `flusher/flusher.go` | Add 5 parse lines |

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

### Senior Developer Review (AI)

**Outcome:** Changes Requested (patches applied)
**Date:** 2026-05-11

#### Review Findings

- [x] [Review][Patch] QuestDB writer `SinglePrintCount != nil` gate expanded to combined AND guard for all 5 fields — consistent with 19-3 imbalance pattern [`writer/questdb/writer.go`]
- [x] [Review][Patch] `TestAccumulator_Auction_FieldsSetOnBar` — added concrete value assertions (not just non-nil): `SinglePrintCount==0` and `SinglePrintLevelsJSON=="[]"` for single price-level bar [`accumulator/accumulator_test.go`]
- [x] [Review][Patch] `TestAccumulator_Auction_ClearedByBarReset` — added 3 missing nil assertions for `SinglePrintLevelsJSON`, `UnfinishedTop`, `UnfinishedBottom` after BarReset [`accumulator/accumulator_test.go`]

### File List
