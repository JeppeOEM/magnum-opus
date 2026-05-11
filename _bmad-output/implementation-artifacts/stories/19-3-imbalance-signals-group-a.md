# Story 19.3: Imbalance Signals (Signal Group A)

Status: done

## Story

As a developer,
I want the candle service to emit per-bar imbalance signal fields from the footprint map,
So that the dashboard and bots can identify price levels where aggressive buying or selling dominates.

**Pre-conditions:** Story 19.1 complete (FootprintCell in features package, footprintMap in accumulator).

## Acceptance Criteria

### AC 1 — ImbalanceSignals struct and ComputeImbalance pure function

1. File `candle-service/internal/features/imbalance.go` contains:
   ```go
   type ImbalanceSignals struct {
       BuyCount  int
       SellCount int
       StackBuy  int
       StackSell int
       Ratio     float64
   }
   func ComputeImbalance(footprint map[string]FootprintCell) ImbalanceSignals
   ```
2. Algorithm:
   - Parse all price-string keys to float64; skip parse errors. Sort valid keys ascending by price.
   - `BuyCount` = count of cells where `buyVol > 3 × sellVol` AND `sellVol > 0`.
   - `SellCount` = count of cells where `sellVol > 3 × buyVol` AND `buyVol > 0`.
   - `StackBuy` = length of longest consecutive run of buy-imbalanced cells (by sorted-price position).
   - `StackSell` = length of longest consecutive run of sell-imbalanced cells.
   - `Ratio` = `(BuyCount - SellCount) / len(validPriceLevels)`; 0.0 if empty.
3. Empty footprint returns zero-value `ImbalanceSignals{}`.

### AC 2 — New Bar fields

4. `accumulator.Bar` gains five new pointer fields after `POCVolume *float64` (inside the // Imbalance Signals group):
   ```go
   ImbalanceBuyCount  *int
   ImbalanceSellCount *int
   ImbalanceStackBuy  *int
   ImbalanceStackSell *int
   ImbalanceRatio     *float64
   ```
5. In `CurrentBar()`, inside the existing `if len(a.footprintMap) > 0` block, call `features.ComputeImbalance(a.footprintMap)` and set all five fields via `ptrInt` / `ptr`.
6. Zero-trade bars: all five fields remain nil (footprintMap is empty → block not entered).

### AC 3 — Pubsub publisher

7. In `candle-service/internal/writer/pubsub/publisher.go::Publish1sBar()`, add after the POC block:
   ```go
   if bar.ImbalanceBuyCount != nil {
       payload["imbalance_buy_count"] = *bar.ImbalanceBuyCount
       payload["imbalance_sell_count"] = *bar.ImbalanceSellCount
       payload["imbalance_stack_buy"] = *bar.ImbalanceStackBuy
       payload["imbalance_stack_sell"] = *bar.ImbalanceStackSell
       payload["imbalance_ratio"] = *bar.ImbalanceRatio
   }
   ```

### AC 4 — QuestDB migration

8. File `candle-service/migrations/005_imbalance.sql` exists:
   ```sql
   ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS imbalance_buy_count INT;
   ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS imbalance_sell_count INT;
   ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS imbalance_stack_buy INT;
   ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS imbalance_stack_sell INT;
   ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS imbalance_ratio DOUBLE;
   ```

### AC 5 — QuestDB ILP write

9. In `questdb/writer.go::writeBar()`, add after the POC block:
   ```go
   if bar.ImbalanceBuyCount != nil {
       row = row.Int64Column("imbalance_buy_count", int64(*bar.ImbalanceBuyCount))
       row = row.Int64Column("imbalance_sell_count", int64(*bar.ImbalanceSellCount))
       row = row.Int64Column("imbalance_stack_buy", int64(*bar.ImbalanceStackBuy))
       row = row.Int64Column("imbalance_stack_sell", int64(*bar.ImbalanceStackSell))
       row = row.Float64Column("imbalance_ratio", *bar.ImbalanceRatio)
   }
   ```

### AC 6 — Flusher (Parquet cold storage)

10. In `flusher/row.go`, add after `POCVolume *float64`:
    ```go
    ImbalanceBuyCount  *int32   `parquet:"imbalance_buy_count"`
    ImbalanceSellCount *int32   `parquet:"imbalance_sell_count"`
    ImbalanceStackBuy  *int32   `parquet:"imbalance_stack_buy"`
    ImbalanceStackSell *int32   `parquet:"imbalance_stack_sell"`
    ImbalanceRatio     *float64 `parquet:"imbalance_ratio"`
    ```
11. In `flusher/flusher.go`, add after the `poc_volume` parse line:
    ```go
    row.ImbalanceBuyCount = parseInt32(get("imbalance_buy_count"))
    row.ImbalanceSellCount = parseInt32(get("imbalance_sell_count"))
    row.ImbalanceStackBuy = parseInt32(get("imbalance_stack_buy"))
    row.ImbalanceStackSell = parseInt32(get("imbalance_stack_sell"))
    row.ImbalanceRatio = parseFloat(get("imbalance_ratio"))
    ```

### AC 7 — L1 tests

12. File `candle-service/internal/features/imbalance_test.go` covers:
    - **All buy-imbalanced**: every cell has `buyVol > 3×sellVol` (and `sellVol > 0`) → `BuyCount = n`, `StackBuy = n`, `SellCount = 0`, `Ratio = 1.0`.
    - **All sell-imbalanced**: opposite → `SellCount = n`, `StackSell = n`, `Ratio = -1.0`.
    - **Mixed, non-consecutive**: alternating imbalance types → `StackBuy = 1`, `StackSell = 1`.
    - **Consecutive stack**: 3 consecutive buy-imbalanced, 1 neutral, 2 buy-imbalanced → `StackBuy = 3`.
    - **Ratio bounds**: `BuyCount=3`, `SellCount=1`, total=8 levels → `Ratio = 0.25`.
    - **Empty footprint**: returns zero-value struct.
    - **Zero-on-one-side excluded**: cells with `sellVol == 0` do NOT count as buy-imbalanced.

## Tasks / Subtasks

- [ ] Task 1: ComputeImbalance pure function
  - [ ] 1.1 Create `candle-service/internal/features/imbalance.go` with `ImbalanceSignals` struct and `ComputeImbalance`
  - [ ] 1.2 Write L1 tests in `features/imbalance_test.go` (7 cases)
  - [ ] 1.3 Implement; tests pass green

- [ ] Task 2: Bar fields + CurrentBar wiring
  - [ ] 2.1 Add 5 pointer fields to `accumulator.Bar` after `POCVolume`
  - [ ] 2.2 Call `ComputeImbalance` in `CurrentBar()` inside `if len(a.footprintMap) > 0` block
  - [ ] 2.3 `go build ./...` — no errors

- [ ] Task 3: Write paths
  - [ ] 3.1 Pubsub publisher: 5 new payload fields gated on `ImbalanceBuyCount != nil`
  - [ ] 3.2 Create `migrations/005_imbalance.sql`
  - [ ] 3.3 QuestDB writer: 5 new ILP columns gated on `ImbalanceBuyCount != nil`
  - [ ] 3.4 `flusher/row.go`: 5 new parquet fields
  - [ ] 3.5 `flusher/flusher.go`: 5 new parse lines

- [ ] Task 4: Full test suite
  - [ ] 4.1 `cd candle-service && go test ./...` — all packages pass

## Dev Notes

### ComputeImbalance implementation

- Sort price-string keys numerically (parse to float64, sort, keep string keys for map lookup).
- Use `sort.Slice` on `[]string` keyed by parsed float64 — same pattern as ComputeValueArea but retain string references for map lookup.
- Threshold: `buyVol > 3 × sellVol` (strict greater-than); `sellVol` must be `> 0` (not just non-negative).
- Consecutive run detection: single pass, track current run length; reset on non-imbalanced cell.
- `Ratio` denominator = `len(validPriceLevels)` (count of successfully parsed levels), NOT `len(footprint)` (which may include unparseable keys — unlikely in practice but safe).
- No new accumulator state — computed entirely from `footprintMap` at `CurrentBar()` time, same as `ComputeValueArea`. No `BarReset()` changes needed.

### Nil-safety invariant

All five imbalance fields are always set together (inside the same `if len(a.footprintMap) > 0` block). A single `ImbalanceBuyCount != nil` check in publisher and writer is sufficient.

### File changes summary

| File | Change |
|---|---|
| `features/imbalance.go` | NEW |
| `features/imbalance_test.go` | NEW |
| `accumulator/accumulator.go` | Add 5 Bar fields; wire ComputeImbalance in CurrentBar |
| `writer/pubsub/publisher.go` | Add 5 payload fields |
| `migrations/005_imbalance.sql` | NEW |
| `writer/questdb/writer.go` | Add 5 ILP column writes |
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

- [x] [Review][Patch] `longestRun` uses variable `max` shadowing Go 1.21 builtin — renamed to `maxLen` [`features/imbalance.go`]
- [x] [Review][Patch] Single nil check for 5 linked fields in publisher/writer — expanded to combined AND guard (consistent with 19-2 pattern) [`writer/pubsub/publisher.go`, `writer/questdb/writer.go`]
- [x] [Review][Patch] Missing accumulator-level integration tests for imbalance Bar fields — added 3 tests in `accumulator_test.go`

### File List
