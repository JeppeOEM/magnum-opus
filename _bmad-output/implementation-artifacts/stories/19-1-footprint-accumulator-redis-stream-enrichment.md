# Story 19.1: Footprint Accumulator + Redis Stream Enrichment

Status: done

## Story

As a developer,
I want the candle service to accumulate per-price buy/sell volume and publish it alongside buy_volume and sell_volume to the Redis candle stream and QuestDB,
So that downstream consumers have the raw footprint data required for all subsequent signal computations (19.2–19.5).

## Acceptance Criteria

### AC 1 — FootprintCell struct and footprintMap accumulation

1. A `FootprintCell` struct exists in `candle-service/internal/accumulator/accumulator.go`:
   ```go
   type FootprintCell struct {
       BuyVol  float64
       SellVol float64
   }
   ```
2. `Accumulator` has an unexported field `footprintMap map[string]FootprintCell` initialized in `New()` as `make(map[string]FootprintCell)`.
3. In `Apply()`, for every trade tick (after size parse succeeds), the corresponding price key in `footprintMap` is updated: if `side == "buy"` → `cell.BuyVol += s`; else → `cell.SellVol += s`. Both sides use the raw price string as the map key.
4. Zero-trade bars never touch `footprintMap`.

### AC 2 — Bar struct new fields

5. `Bar` gains three new pointer fields (placed after the existing `BuyVolume *float64`):
   ```go
   SellVolume   *float64
   FootprintJSON *string
   ```
6. In `CurrentBar()`, when `a.tradeCount > 0`:
   - `bar.SellVolume = ptr(*bar.Volume - a.buyVolume)` — computed as difference; never stored separately in accumulator.
   - `bar.FootprintJSON` is set to the JSON-encoded footprint map (see AC 3).
7. When `a.tradeCount == 0`, both `SellVolume` and `FootprintJSON` remain `nil`.

### AC 3 — FootprintJSON format

8. `FootprintJSON` is encoded using `encoding/json` with compact keys `"b"` (buy) and `"s"` (sell):
   ```json
   {"67000.5":{"b":0.1,"s":0.2},"67001.0":{"b":0.5,"s":0.0}}
   ```
9. The map keys are the raw price strings from trade ticks (e.g., `"67000.5"`).
10. `json.Marshal` is used directly — no custom encoder needed; the value struct `jsonCell{B float64 \`json:"b"\`; S float64 \`json:"s"\`}` is used for marshalling (not `FootprintCell` which uses `BuyVol`/`SellVol` names).

### AC 4 — BarReset clears footprintMap

11. `BarReset()` clears the footprint map using `clear(a.footprintMap)` (same pattern as `tradePriceLevels` at line 781).
12. The map is never set to nil — always a live, pre-allocated map to avoid allocation on each Apply.

### AC 5 — Redis stream: buy_volume, sell_volume, footprint_json

13. `barFields()` in `candle-service/internal/writer/redis/publisher.go` adds three new entries to the returned map, all nil-guarded (omit entirely when nil — do not emit empty string):
    ```go
    if bar.BuyVolume != nil {
        m["buy_volume"] = strconv.FormatFloat(*bar.BuyVolume, 'f', -1, 64)
    }
    if bar.SellVolume != nil {
        m["sell_volume"] = strconv.FormatFloat(*bar.SellVolume, 'f', -1, 64)
    }
    if bar.FootprintJSON != nil {
        m["footprint_json"] = *bar.FootprintJSON
    }
    ```
14. `barFields` must be refactored from returning a map literal to building a `map[string]any` and conditionally adding nil-guarded entries (the current literal form cannot accommodate conditional keys).

### AC 6 — QuestDB migration

15. File `candle-service/migrations/003_footprint.sql` exists with content:
    ```sql
    ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS footprint_json VARCHAR;
    ```
16. The migration is idempotent — `IF NOT EXISTS` ensures re-running is a no-op (QuestDB supports this clause in ALTER TABLE ADD COLUMN).

### AC 7 — QuestDB ILP write

17. In `candle-service/internal/writer/questdb/writer.go`, the ILP row write includes:
    ```go
    if bar.FootprintJSON != nil {
        row = row.StringColumn("footprint_json", *bar.FootprintJSON)
    }
    ```
    following the nil-guard pattern established for all nullable fields (e.g., BuyVolume at the existing nil-guarded block).

### AC 8 — Flusher (Parquet cold storage)

18. `Snapshot1sRow` in `candle-service/internal/flusher/row.go` gains:
    ```go
    FootprintJSON *string `parquet:"footprint_json"`
    ```
19. In `candle-service/internal/flusher/flusher.go`, the CSV-to-row mapping includes:
    ```go
    row.FootprintJSON = getString(get("footprint_json"))
    ```
    where `getString` is a helper that returns `nil` for empty strings (matching how optional string columns work — inspect existing pattern or add if absent).

### AC 9 — L1 tests

20. `candle-service/internal/accumulator/accumulator_test.go` has new test cases covering:
    - Buy tick at price "67000.5" → `footprintMap["67000.5"].BuyVol == 0.1`, SellVol == 0
    - Sell tick at price "67000.5" → `footprintMap["67000.5"].SellVol == 0.2`, BuyVol unchanged
    - Multiple trades same price both sides → values accumulate correctly
    - `BarReset()` → footprintMap is empty (len == 0), not nil
    - Zero-trade bar → `bar.FootprintJSON == nil`, `bar.SellVolume == nil`
    - Non-zero-trade bar → JSON matches expected schema (unmarshal and compare)
21. All existing accumulator tests continue to pass.

## Tasks / Subtasks

- [x] Add `FootprintCell` struct and `footprintMap` field to `accumulator.go` (AC: 1, 2)
  - [x] Add `FootprintCell` struct before `Accumulator` struct
  - [x] Add `footprintMap map[string]FootprintCell` field to `Accumulator` struct
  - [x] Initialize `footprintMap: make(map[string]FootprintCell)` in `New()`
- [x] Update `Apply()` to accumulate footprint (AC: 1)
  - [x] After `a.tradeCount++` / buy volume section, add price-keyed footprint accumulation
- [x] Add `SellVolume` and `FootprintJSON` to `Bar` struct (AC: 2)
  - [x] Add fields after `BuyVolume *float64` in `Bar` struct
- [x] Update `CurrentBar()` to populate new Bar fields (AC: 2, 3)
  - [x] Inside `if a.tradeCount > 0` block: compute SellVolume and marshal FootprintJSON
  - [x] Define local `jsonCell` struct and use it for marshalling
- [x] Update `BarReset()` to clear footprintMap (AC: 4)
  - [x] Add `clear(a.footprintMap)` after existing `clear(a.tradePriceLevels)` line
- [x] Refactor `barFields()` and add new Redis fields (AC: 5)
  - [x] Change from map literal to build-and-add pattern
  - [x] Add buy_volume, sell_volume (via cascade.Bar); footprint_json added to pubsub/publisher.go (1s bar)
- [x] Create migration `003_footprint.sql` (AC: 6)
- [x] Add FootprintJSON to QuestDB ILP writer (AC: 7)
  - [x] Add nil-guarded `.StringColumn("footprint_json", ...)` in writer.go
- [x] Add FootprintJSON to flusher row struct and CSV mapping (AC: 8)
  - [x] Add field to `Snapshot1sRow` in `row.go`
  - [x] Add `getString` helper; add mapping in `flusher.go`
- [x] Write L1 tests for footprint accumulation (AC: 9)
  - [x] Test buy/sell accumulation, BarReset, zero-trade nil, JSON format
- [x] Run `go test ./...` from `candle-service/` — all tests pass (AC: 9, 21)

## Dev Notes

### Key files to modify

| File | Change |
|---|---|
| `candle-service/internal/accumulator/accumulator.go` | `FootprintCell` struct, `footprintMap` field, `Apply()`, `CurrentBar()`, `BarReset()` |
| `candle-service/internal/writer/redis/publisher.go` | Refactor `barFields()`, add 3 new fields |
| `candle-service/internal/writer/questdb/writer.go` | Add `StringColumn("footprint_json", ...)` |
| `candle-service/internal/flusher/row.go` | Add `FootprintJSON *string` field |
| `candle-service/internal/flusher/flusher.go` | Add CSV mapping for footprint_json |
| `candle-service/migrations/003_footprint.sql` | New file: ALTER TABLE ADD COLUMN |
| `candle-service/internal/accumulator/accumulator_test.go` | New L1 tests |

### accumulator.go — exact insertion points

**`FootprintCell` struct** — add before the `Bar` struct (before line 22):
```go
// FootprintCell holds per-price buy and sell volume within a single 1-second bar.
type FootprintCell struct {
    BuyVol  float64
    SellVol float64
}
```

**`Bar` struct** — add after `BuyCount *int` (around line 86):
```go
SellVolume    *float64
FootprintJSON *string
```

**`Accumulator` struct** — add after `buyCount int` field (around line 163):
```go
footprintMap map[string]FootprintCell
```

**`New()`** — add to the return literal (around line 250):
```go
footprintMap: make(map[string]FootprintCell),
```

**`Apply()` trade section** — add after `a.buyCount++` (around line 430):
```go
// Footprint accumulation.
cell := a.footprintMap[price]
if side == "buy" {
    cell.BuyVol += s
} else {
    cell.SellVol += s
}
a.footprintMap[price] = cell
```

**`CurrentBar()` inside `if a.tradeCount > 0`** — add after `bar.BuyCount = ptrInt(a.buyCount)` (around line 554):
```go
bar.SellVolume = ptr(*bar.Volume - a.buyVolume)
if len(a.footprintMap) > 0 {
    type jsonCell struct {
        B float64 `json:"b"`
        S float64 `json:"s"`
    }
    enc := make(map[string]jsonCell, len(a.footprintMap))
    for k, v := range a.footprintMap {
        enc[k] = jsonCell{B: v.BuyVol, S: v.SellVol}
    }
    if b, err := json.Marshal(enc); err == nil {
        s := string(b)
        bar.FootprintJSON = &s
    }
}
```
Add `"encoding/json"` to the import block.

**`BarReset()`** — add after `clear(a.tradePriceLevels)` (around line 781):
```go
clear(a.footprintMap)
```

### publisher.go — barFields refactor

Current `barFields()` returns a map literal. Refactor to build-and-return so nil-guarded entries can be conditionally added:

```go
func barFields(bar cascade.Bar, isComplete bool) map[string]any {
    isCompleteStr := "false"
    if isComplete {
        isCompleteStr = "true"
    }
    m := map[string]any{
        "ts":          strconv.FormatInt(bar.OpenTs, 10),
        "exchange":    bar.Exchange,
        "symbol":      bar.Symbol,
        "tf":          string(bar.TF),
        "open":        floatOrEmpty(bar.Open),
        "high":        floatOrEmpty(bar.High),
        "low":         floatOrEmpty(bar.Low),
        "close":       floatOrEmpty(bar.Close),
        "volume":      strconv.FormatFloat(bar.Volume, 'f', -1, 64),
        "quote_vol":   strconv.FormatFloat(bar.QuoteVol, 'f', -1, 64),
        "trade_count": strconv.Itoa(bar.TradeCount),
        "bar_count":   strconv.Itoa(bar.BarCount),
        "gap_count":   strconv.Itoa(bar.GapCount),
        "is_complete": isCompleteStr,
    }
    if bar.BuyVolume != nil {
        m["buy_volume"] = strconv.FormatFloat(*bar.BuyVolume, 'f', -1, 64)
    }
    if bar.SellVolume != nil {
        m["sell_volume"] = strconv.FormatFloat(*bar.SellVolume, 'f', -1, 64)
    }
    if bar.FootprintJSON != nil {
        m["footprint_json"] = *bar.FootprintJSON
    }
    return m
}
```

Note: `barFields` takes a `cascade.Bar`, not `accumulator.Bar`. The new fields `BuyVolume`, `SellVolume`, `FootprintJSON` must also be added to `cascade.Bar` in `candle-service/internal/cascade/bar.go`. Check if cascade.Bar copies from accumulator.Bar in the cascade engine or if it's a separate struct — if cascade.Bar is synthesised from accumulator.Bar, the copy path must propagate the new fields.

### cascade.Bar propagation — CRITICAL

`barFields()` operates on `cascade.Bar`, which is the multi-timeframe bar type. The 1-second footprint fields originate in `accumulator.Bar` and flow through the cascade engine. Trace this path:

1. `accumulator.Bar` → set in `CurrentBar()` (updated above)
2. `accWriter.Flush()` calls `acc.CurrentBar()` → constructs a `cascade.Bar` from the 1-second accumulator bar
3. The cascade `Bar` struct is in `candle-service/internal/cascade/bar.go` — check if `BuyVolume`, `SellVolume`, `FootprintJSON` need to be added there too, and that the copy from accumulator.Bar to cascade.Bar includes them.

Read `candle-service/internal/cascade/bar.go` and the accWriter flush path before implementing.

### flusher.go — getString helper

The flusher uses `parseFloat(get("field"))` for float fields and `parseInt32(get("field"))` for ints. If there is no `getString` helper, add:
```go
func getString(v string) *string {
    if v == "" {
        return nil
    }
    return &v
}
```
Then map: `row.FootprintJSON = getString(get("footprint_json"))`

### Migration file

```sql
-- 003_footprint.sql
-- Adds footprint_json column to snapshot_1s for per-price buy/sell volume blob.
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS footprint_json VARCHAR;
```

### Testing guidance

- L1 tests are pure (no Redis/QuestDB) — run from `accumulator/` package directly.
- Use a real `Accumulator` (not a mock) with a fake clock.
- For JSON format test: unmarshal the `FootprintJSON` string into `map[string]map[string]float64` and compare values with tolerance — do not string-compare JSON (key ordering not guaranteed).
- Existing tests in `accumulator_test.go` use `newTestAcc()` helper — add new tests in the same file using same helper.

### Architecture rules (from project-context.md)

- `time.Now()` is banned in `internal/` — new code in accumulator is pure (no clock calls needed).
- `FootprintCell` lives in `accumulator/` package (it is accumulator-owned state, not a features function).
- No goroutines added.
- Migration is additive-only `ALTER TABLE ADD COLUMN`.

### Project Structure Notes

- New file: `candle-service/migrations/003_footprint.sql`
- Modified files are all existing — no new packages needed for this story.
- `encoding/json` is a stdlib import — no new `go.mod` dependency.

### References

- [Source: candle-service/internal/accumulator/accumulator.go#Apply()] — buy-side branch at line ~428 where `a.buyVolume += s`; footprint accumulation goes immediately after
- [Source: candle-service/internal/accumulator/accumulator.go#BarReset()] — `clear(a.tradePriceLevels)` at line ~781; add `clear(a.footprintMap)` after
- [Source: candle-service/internal/accumulator/accumulator.go#CurrentBar()] — `BuyVolume = ptr(...)` at line ~553; new fields go immediately after
- [Source: candle-service/internal/writer/redis/publisher.go#barFields()] — line 106; refactor from literal to build-and-add
- [Source: candle-service/migrations/001_snapshot_1s.sql] — format reference for new migration
- [Source: _bmad-output/planning-artifacts/epics-candle-19.md#Story 19.1] — canonical ACs

## Senior Developer Review (AI)

**Date:** 2026-05-11 | **Outcome:** Changes Requested → All resolved

### Review Findings

- [x] [Review][Patch] sell_volume column missing from migration 003 — added `ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS sell_volume DOUBLE` to 003_footprint.sql [migrations/003_footprint.sql]
- [x] [Review][Patch] ToHash/RestoreFromHash don't persist buyVolume — on service restart mid-TF-bar, cascade emits BuyVolume=0 [cascade/cascade.go:239,210]
- [x] [Review][Patch] BarResetClearsMap test was a false green — tradeCount==0 guard made the test vacuous; replaced with cross-bar isolation test [accumulator_test.go]
- [x] [Review][Patch] pubsub Publish1sBar uses derefF(nil)=0.0 for zero-trade bars — loses nil semantics vs zero volume [pubsub/publisher.go]
- [x] [Review][Patch] cascade SellVolume = volume - buyVolume can be negative due to float drift — clamped to max(0,...) [cascade/cascade.go:124]
- [x] [Review][Patch] accumulator SellVolume also clamped — same max(0,...) guard [accumulator/accumulator.go]
- [x] [Review][Patch] Reset() didn't reinitialize footprintMap — added make(map[string]FootprintCell) matching tradePriceLevels pattern [accumulator/accumulator.go]
- [x] [Review][Defer] row.go comment "Column order matches DDL exactly" is stale — pre-existing, CSV parsing is name-indexed so no runtime impact — deferred, pre-existing
- [x] [Review][Defer] footprintMap uses raw price string as key — same pattern as tradePriceLevels; pre-existing exchange normalization handles this — deferred, pre-existing

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- Added `FootprintCell` struct and `footprintMap map[string]FootprintCell` to `Accumulator`; initialized in `New()`, cleared in `BarReset()` via `clear()`.
- `Apply()` accumulates per-price buy/sell volume into `footprintMap` after trade is processed.
- `CurrentBar()` sets `SellVolume = volume - buyVolume` and marshals `FootprintJSON` using compact `{"b":...,"s":...}` keys via a local `jsonCell` struct.
- cascade.Bar gained `BuyVolume` and `SellVolume` fields; `cascadeAcc` accumulates `buyVolume` in `fold()` and derives `SellVolume = volume - buyVolume` in `toBar()`.
- `barFields()` in redis/publisher.go refactored from map literal to build-and-add; adds `buy_volume` and `sell_volume` when `TradeCount > 0`. `footprint_json` is per-1s only, so it is published via pubsub/publisher.go `Publish1sBar()` instead.
- QuestDB writer adds `sell_volume` Float64Column and `footprint_json` StringColumn with nil guards.
- `Snapshot1sRow` gained `SellVolume *float64` and `FootprintJSON *string`; flusher.go maps them from CSV with new `getString()` helper.
- Migration `003_footprint.sql` uses `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (idempotent).
- 7 new L1 tests in accumulator_test.go; all pass. Full suite: 0 regressions.

### File List

- `candle-service/internal/accumulator/accumulator.go` — FootprintCell struct, footprintMap field, Apply/CurrentBar/BarReset updates, SellVolume+FootprintJSON on Bar
- `candle-service/internal/accumulator/accumulator_test.go` — 7 new footprint L1 tests
- `candle-service/internal/cascade/cascade.go` — BuyVolume/SellVolume on Bar and cascadeAcc, fold/toBar updates
- `candle-service/internal/writer/redis/publisher.go` — barFields refactored, buy_volume/sell_volume added
- `candle-service/internal/writer/pubsub/publisher.go` — buy_volume, sell_volume, footprint_json added to Publish1sBar payload
- `candle-service/internal/writer/questdb/writer.go` — sell_volume + footprint_json ILP writes
- `candle-service/internal/flusher/row.go` — SellVolume + FootprintJSON fields on Snapshot1sRow
- `candle-service/internal/flusher/flusher.go` — getString helper, sell_volume + footprint_json CSV mappings
- `candle-service/migrations/003_footprint.sql` — new migration file (sell_volume + footprint_json)
