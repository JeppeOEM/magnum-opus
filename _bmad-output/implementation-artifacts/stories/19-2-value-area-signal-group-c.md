# Story 19.2: Value Area (Signal Group C)

Status: done

## Story

As a developer,
I want the candle service to compute POC and Value Area from the footprint map on each bar close,
So that dashboard and bot consumers have `poc_price`, `value_area_high`, `value_area_low`, `poc_volume` per second.

**Pre-conditions:** Story 19.1 complete — `FootprintCell`, `footprintMap`, `FootprintJSON` and `SellVolume` all exist in the accumulator.

## Acceptance Criteria

### AC 1 — FootprintCell relocated to features package

1. `FootprintCell` is removed from `candle-service/internal/accumulator/accumulator.go` and defined in the `features` package (new file `candle-service/internal/features/footprint.go`):
   ```go
   type FootprintCell struct {
       BuyVol  float64
       SellVol float64
   }
   ```
2. All references in `accumulator/accumulator.go` are updated from `FootprintCell` to `features.FootprintCell` (the package already imports `features`).
3. `accumulator_test.go` is updated from `FootprintCell` to `features.FootprintCell` — add `"github.com/mrqdt/magnum-opus/candle-service/internal/features"` import if not already present.
4. No import cycle is introduced — `features` does not import `accumulator`.

### AC 2 — ComputeValueArea pure function

5. File `candle-service/internal/features/footprint.go` contains:
   ```go
   // ComputeValueArea computes POC and 70% Value Area from a per-bar footprint map.
   // Returns ok=false when the footprint is empty. Single-level footprints return
   // pocPrice==vahPrice==valPrice with ok=true.
   func ComputeValueArea(footprint map[string]FootprintCell) (pocPrice, vahPrice, valPrice, pocVolume float64, ok bool)
   ```
6. Algorithm:
   - Parse all price-string keys to float64 (skip any that fail to parse).
   - If after parsing there are no valid levels, return `ok=false`.
   - POC = price level with highest `buyVol + sellVol`; ties → higher price wins.
   - `pocVolume` = total volume at POC.
   - Single-level case: `pocPrice = vahPrice = valPrice`; `ok = true`.
   - Multi-level: sort levels by price ascending. Starting with the POC level in the value area, iteratively add the adjacent level (above or below) with higher total volume; ties go to the higher-price side. Continue until accumulated volume ≥ 70% of total footprint volume OR all levels exhausted.
   - `vahPrice` = highest price in value area; `valPrice` = lowest.
7. `totalVol` is computed as sum of all valid levels' `buyVol + sellVol`.
8. Empty footprint → `ok = false` with all floats zero.

### AC 3 — New Bar fields

9. `accumulator.Bar` gains four new pointer fields after `FootprintJSON *string` (in the Trade flow section):
   ```go
   POCPrice      *float64
   ValueAreaHigh *float64
   ValueAreaLow  *float64
   POCVolume     *float64
   ```
10. In `CurrentBar()`, when `len(a.footprintMap) > 0` (same guard as FootprintJSON):
    - Call `features.ComputeValueArea(a.footprintMap)`.
    - If `ok`, set `bar.POCPrice`, `bar.ValueAreaHigh`, `bar.ValueAreaLow`, `bar.POCVolume` using `ptr(...)`.
11. When `tradeCount == 0` or footprintMap is empty, all four fields remain `nil`.

### AC 4 — Pubsub publisher: 4 new fields

12. In `candle-service/internal/writer/pubsub/publisher.go::Publish1sBar()`, add after the `FootprintJSON` block:
    ```go
    if bar.POCPrice != nil {
        payload["poc_price"] = *bar.POCPrice
        payload["value_area_high"] = *bar.ValueAreaHigh
        payload["value_area_low"] = *bar.ValueAreaLow
        payload["poc_volume"] = *bar.POCVolume
    }
    ```
13. All four fields are gated on `POCPrice != nil` — if POCPrice is nil then the other three are also nil (they are always set together from `ComputeValueArea`). Omit all four when nil.

### AC 5 — QuestDB migration

14. File `candle-service/migrations/004_value_area.sql` exists with:
    ```sql
    -- 004_value_area.sql
    -- Adds POC and Value Area columns to snapshot_1s.
    ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS poc_price DOUBLE;
    ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS value_area_high DOUBLE;
    ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS value_area_low DOUBLE;
    ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS poc_volume DOUBLE;
    ```
15. Migration is idempotent; `IF NOT EXISTS` ensures re-run is a no-op.

### AC 6 — QuestDB ILP write

16. In `candle-service/internal/writer/questdb/writer.go::writeBar()`, after the `FootprintJSON` write, add:
    ```go
    if bar.POCPrice != nil {
        row = row.Float64Column("poc_price", *bar.POCPrice)
        row = row.Float64Column("value_area_high", *bar.ValueAreaHigh)
        row = row.Float64Column("value_area_low", *bar.ValueAreaLow)
        row = row.Float64Column("poc_volume", *bar.POCVolume)
    }
    ```

### AC 7 — Flusher (Parquet cold storage)

17. In `candle-service/internal/flusher/row.go`, add after `FootprintJSON *string`:
    ```go
    POCPrice      *float64 `parquet:"poc_price"`
    ValueAreaHigh *float64 `parquet:"value_area_high"`
    ValueAreaLow  *float64 `parquet:"value_area_low"`
    POCVolume     *float64 `parquet:"poc_volume"`
    ```
18. In `candle-service/internal/flusher/flusher.go`, add after the `FootprintJSON` parse line:
    ```go
    row.POCPrice = parseFloat(get("poc_price"))
    row.ValueAreaHigh = parseFloat(get("value_area_high"))
    row.ValueAreaLow = parseFloat(get("value_area_low"))
    row.POCVolume = parseFloat(get("poc_volume"))
    ```

### AC 8 — L1 tests

19. File `candle-service/internal/features/footprint_test.go` contains L1 tests for `ComputeValueArea` covering:
    - **Empty footprint**: `ok == false`
    - **Single level**: `pocPrice == vahPrice == valPrice`, `ok == true`
    - **Two levels — asymmetric vol**: POC is the higher-volume level; value area stays at POC if POC alone already ≥ 70%
    - **Standard distribution** (3+ levels): POC is identified correctly, value area expansion is correct, accumulated vol ≥ 70% of total
    - **Tie-breaking**: two equal-volume levels → higher price becomes POC
    - **70% boundary precision**: value area stops adding once threshold is crossed (not strictly equal)
    - **Uniform distribution** (all levels same vol): POC = highest price; expansion is deterministic
20. All tests pass `go test ./...` from `candle-service/`.

## Tasks / Subtasks

- [x] Task 1: Relocate FootprintCell to features package
  - [x] 1.1 Create `candle-service/internal/features/footprint.go` with `FootprintCell` struct
  - [x] 1.2 Remove `FootprintCell` from `accumulator/accumulator.go`; replace all uses with `features.FootprintCell`
  - [x] 1.3 Update `accumulator_test.go` to use `features.FootprintCell` (test file already imported features; no direct FootprintCell references)
  - [x] 1.4 Run `go build ./...` — confirm no import cycles and no compile errors

- [x] Task 2: Implement ComputeValueArea pure function
  - [x] 2.1 Add `ComputeValueArea` to `features/footprint.go`
  - [x] 2.2 Write L1 tests in `features/footprint_test.go` (8 cases covering all ACs)
  - [x] 2.3 Implement algorithm; tests pass (green phase)

- [x] Task 3: Add Bar fields and wire in CurrentBar
  - [x] 3.1 Add `POCPrice`, `ValueAreaHigh`, `ValueAreaLow`, `POCVolume *float64` to `accumulator.Bar` struct
  - [x] 3.2 In `CurrentBar()`, call `ComputeValueArea` when footprintMap is non-empty and set the four fields
  - [x] 3.3 Run accumulator tests — no regressions

- [x] Task 4: Pubsub publisher
  - [x] 4.1 Add the four value-area fields to `Publish1sBar()` payload (gated on POCPrice != nil)
  - [x] 4.2 Added `TestPublisher_ValueAreaFields_Present` and `TestPublisher_ValueAreaFields_AbsentWhenNoPOC`

- [x] Task 5: QuestDB migration + ILP write + flusher
  - [x] 5.1 Create `candle-service/migrations/004_value_area.sql`
  - [x] 5.2 Add ILP write for 4 new columns in `questdb/writer.go`
  - [x] 5.3 Add `Snapshot1sRow` fields in `flusher/row.go`
  - [x] 5.4 Add parse lines in `flusher/flusher.go`

- [x] Task 6: Full test suite
  - [x] 6.1 `cd candle-service && go test ./...` — all 16 packages pass, zero failures

## Dev Notes

### Import structure: why FootprintCell must move to features

`accumulator` already imports `features` (for `BestQuote`, `DepthSnapshot`, `OFIDelta`, etc.). If `ComputeValueArea` in the `features` package accepts `map[string]accumulator.FootprintCell`, that creates an import cycle: `features → accumulator → features`. The fix is to define `FootprintCell` in `features` and have `accumulator` use `features.FootprintCell` — no cycle, since `accumulator` already imports `features`.

### ComputeValueArea algorithm detail

```
1. Parse all price strings to float64. Skip parse errors.
2. Compute totalVol = sum of (cell.BuyVol + cell.SellVol) across valid levels.
3. If no valid levels or totalVol == 0: return ok=false.
4. Find pocIdx: index of level with max (BuyVol+SellVol), ties → higher price.
5. If len(levels) == 1: return that price for poc/vah/val, pocVol, ok=true.
6. Sort levels by price ascending (use sort.Slice on a []struct{price, vol float64}).
7. target := totalVol * 0.70
8. accumulated := levels[pocIdx].vol; lo := pocIdx; hi := pocIdx
9. Loop while accumulated < target AND (lo > 0 OR hi < len-1):
   - canLo := lo > 0; canHi := hi < len-1
   - If both: pick side with higher vol (levels[hi+1].vol >= levels[lo-1].vol → expand hi)
   - Else: expand whichever is available
   - Increment hi or decrement lo; add its vol to accumulated.
10. Return levels[pocIdx].price, levels[hi].price, levels[lo].price, levels[pocIdx].vol, true.
```

Tie in expansion: `>=` means "prefer expanding upward (higher price) on equal volume" — consistent with the POC tie-break rule.

### Which publisher gets these fields

`poc_price`, `value_area_high`, `value_area_low`, `poc_volume` are computed from the **1-second footprintMap**. They are semantically per-1s only. They go into:
- `pubsub/publisher.go::Publish1sBar()` (takes `accumulator.Bar`) — the 1s JSON pub/sub channel used by the frontend
- `questdb/writer.go::writeBar()` (takes `accumulator.Bar`) — QuestDB persistence
- `flusher/` — Parquet cold storage

They do **NOT** go into `redis/publisher.go::barFields()` (which takes `cascade.Bar` for 1m/5m/... timeframes). Cascade bars aggregate multiple 1s bars; POC from a 5m cascade bar would need recomputing from an aggregated footprint that doesn't exist — so it's omitted.

### Existing files to update (summary)

| File | Change |
|---|---|
| `features/footprint.go` | NEW — `FootprintCell` struct + `ComputeValueArea` |
| `features/footprint_test.go` | NEW — L1 tests for ComputeValueArea |
| `accumulator/accumulator.go` | REMOVE `FootprintCell` def; `s/FootprintCell/features.FootprintCell/g`; add 4 Bar fields; wire CurrentBar |
| `accumulator/accumulator_test.go` | Update `FootprintCell` refs to `features.FootprintCell` |
| `writer/pubsub/publisher.go` | Add 4 fields to payload |
| `writer/questdb/writer.go` | Add 4 ILP column writes |
| `migrations/004_value_area.sql` | NEW — 4 ADD COLUMN statements |
| `flusher/row.go` | Add 4 `*float64` fields |
| `flusher/flusher.go` | Add 4 parseFloat calls |

### Nil-safety invariant

`ComputeValueArea` returning `ok=true` guarantees that all four outputs (`pocPrice`, `vahPrice`, `valPrice`, `pocVolume`) are valid (non-zero if there was volume). In `CurrentBar()`, gate all four `bar.Foo = ptr(...)` assignments inside `if ok { ... }`. In `Publish1sBar()`, gate all four payload entries on `bar.POCPrice != nil` — since all four are always set together, this single nil check is sufficient.

### BarReset: no new accumulator state

Story 19.2 adds no new per-bar accumulator fields — it only reads `footprintMap` (already cleared in `BarReset()`) and computes the four values at `CurrentBar()` time. No changes to `BarReset()` or `Reset()` are needed.

### Previous story context (19.1 review patches applied)

- `SellVolume = max(0, volume - buyVolume)` — clamped to prevent float drift going negative
- `footprintMap` uses `clear()` in `BarReset()` and `make()` in `Reset()` — this invariant must not change
- `BarResetClearsMap` test validates cross-bar isolation of footprintMap

## Senior Developer Review (AI)

**Outcome:** Changes Requested
**Date:** 2026-05-11
**Reviewers:** Blind Hunter, Edge Case Hunter, Acceptance Auditor (parallel)

### Action Items

- [x] [Review][Patch] Nil dereference: add individual nil guards for `ValueAreaHigh`, `ValueAreaLow`, `POCVolume` in `pubsub/publisher.go` and `questdb/writer.go` — currently only `POCPrice != nil` is checked [`pubsub/publisher.go:77-81`, `questdb/writer.go:poc_price block`]
- [x] [Review][Patch] Missing accumulator-level tests for `Bar.POCPrice`, `Bar.ValueAreaHigh`, `Bar.ValueAreaLow`, `Bar.POCVolume` — no test verifies these are set/nil through `CurrentBar()` [`accumulator/accumulator_test.go`]
- [x] [Review][Patch] Missing test: non-empty footprint with all-zero volume should return `ok=false` — `ComputeValueArea` handles `totalVol == 0` but no test exercises this path [`features/footprint_test.go`]
- [x] [Review][Defer] `FootprintJSON` non-nil while `POCPrice` nil when zero-size trades accumulate — pre-existing `Apply()` design from 19-1, fixable in a future story [`accumulator/accumulator.go:Apply`] — deferred, pre-existing
- [x] [Review][Defer] `derefF(bar.SellVolume)` asymmetry vs `*bar.BuyVolume` in pubsub publisher — pre-existing 19-1 code, not introduced here [`pubsub/publisher.go:72`] — deferred, pre-existing
- [x] [Review][Defer] `Reset()` redundant `make` after `BarReset()`'s `clear` for footprintMap — pre-existing 19-1 pattern [`accumulator/accumulator.go:Reset`] — deferred, pre-existing
- [x] [Review][Defer] No test for malformed price-string keys in `ComputeValueArea` — low risk in practice, pre-existing 19-1 data-contract assumption [`features/footprint.go:35-37`] — deferred, pre-existing

## Dev Agent Record

### Debug Log
- FootprintCell relocated from accumulator → features to avoid import cycle (features imported by accumulator)
- accumulator_test.go already imported features package and had no direct FootprintCell type references — no changes needed
- ComputeValueArea implemented with ascending-sort + greedy expansion; tie-break on volume → higher price side (>= comparison)
- All 8 L1 tests passed immediately; full suite: 16 packages, 0 failures

### Completion Notes
- Moved `FootprintCell` struct to `features/footprint.go` (new file) to allow `ComputeValueArea` pure function to live in features without import cycle
- Added `ComputeValueArea(map[string]FootprintCell)` implementing POC + 70% Value Area expansion with numeric price parsing and tie-breaking
- Added 4 new `*float64` fields to `accumulator.Bar`: `POCPrice`, `ValueAreaHigh`, `ValueAreaLow`, `POCVolume`
- Wired `ComputeValueArea` call in `CurrentBar()` inside the existing `if len(a.footprintMap) > 0` block
- Updated `pubsub/publisher.go`: 4 new payload fields gated on `POCPrice != nil`
- Created `migrations/004_value_area.sql` with 4 `DOUBLE` ADD COLUMN IF NOT EXISTS statements
- Updated `questdb/writer.go`: 4 new `Float64Column` writes gated on `POCPrice != nil`
- Updated `flusher/row.go` and `flusher/flusher.go`: 4 new Parquet fields with parseFloat parse lines

## File List
- `candle-service/internal/features/footprint.go` (new)
- `candle-service/internal/features/footprint_test.go` (new)
- `candle-service/internal/accumulator/accumulator.go` (modified)
- `candle-service/internal/writer/pubsub/publisher.go` (modified)
- `candle-service/internal/writer/pubsub/publisher_test.go` (modified)
- `candle-service/internal/writer/questdb/writer.go` (modified)
- `candle-service/migrations/004_value_area.sql` (new)
- `candle-service/internal/flusher/row.go` (modified)
- `candle-service/internal/flusher/flusher.go` (modified)
- `candle-service/internal/accumulator/accumulator_test.go` (modified)

## Change Log
- 2026-05-11: Implemented Value Area (Signal Group C) — FootprintCell relocated to features, ComputeValueArea pure function added with 8 L1 tests, 4 new Bar fields wired end-to-end through pubsub, QuestDB ILP, migration, and Parquet flusher
- 2026-05-11: Addressed code review findings — 3 patch items resolved: combined nil guard in publisher+writer, 3 accumulator-level integration tests, all-zero-volume test in features/footprint_test.go
