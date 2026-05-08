# Story 7.5: Quality Fields and Null Rows

Status: done

## Story

As a developer,
I want quality fields (`bar_count`, `ofi`, `ofi_l1`) fixed and empty-second null rows correctly written with OB carry-forward,
so that CS-FR17 and CS-FR18 are satisfied and `bar_count` increments correctly, `ofi`/`ofi_l1` are nil when there are no ticks, and empty seconds produce a complete null row.

## Acceptance Criteria

### barCount fix (CS-FR17)

1. `BarReset()` increments `a.barCount` AFTER `CurrentBar()` has been called:
   - `BarReset()` gains `a.barCount++` at the END of the method body.
   - `accWriter.Flush()` already calls `CurrentBar()` before `BarReset()`, so the increment happens after the snapshot is taken.
   - `CurrentBar()` continues to return `a.barCount + 1` (the next bar number, 1-based).
   - Net result: first bar writes `bar_count=1`, second bar writes `bar_count=2`, etc.
   - `Reset()` (full reset on gap/snapshot) clears `a.barCount = 0`.

2. Test: three consecutive BarReset calls produce `bar_count` = 1, 2, 3 respectively from `CurrentBar()`.

### OFI nil fix (CS-FR17)

3. `Bar.OFI float64` becomes `Bar.OFI *float64`. Nil when no ticks were received in the bar (i.e., `ofiSum == 0 && tradeCount == 0 && !hasOpenQuote`).

   More precisely: `ofi` and `ofi_l1` should be non-nil whenever at least one tick (trade or OB delta) has been received. Track this with a new `hasTicks bool` field on the Accumulator:
   ```go
   hasTicks bool // set true on first Apply() call in the bar
   ```
   In `Apply()`: `a.hasTicks = true` (at the top, unconditionally).
   In `CurrentBar()`:
   ```go
   if a.hasTicks {
       bar.OFI = ptr(a.ofiSum)
       bar.OFIL1 = ptr(a.ofiSum) // same value until full-book OFI is added
   }
   ```
   Note: `ofi` and `ofi_l1` currently carry the same value (full-book OFI is deferred). Both fields remain on `Bar` as `*float64`.
   
   In `BarReset()`: `a.hasTicks = false`.

4. `internal/writer/questdb/writer.go`: change unconditional OFI write to nil-check pattern:
   ```go
   if bar.OFI != nil { row = row.Float64Column("ofi", *bar.OFI) }
   if bar.OFIL1 != nil { row = row.Float64Column("ofi_l1", *bar.OFIL1) }
   ```

### `trade_count` nil fix (CS-FR17)

5. `trade_count` is currently written unconditionally (line 165 of writer.go). It should only be written when `bar.TradeCount > 0` OR when there were ticks this second. Actually per the DDL, `trade_count INT` is nullable. Write it unconditionally for consistency with other quality fields — it's 0 for no-trade seconds and that's correct. **No change needed here** — leave `trade_count` as unconditional.

### Null-row OB carry-forward for empty seconds (CS-FR18)

6. Accumulator gains `SeedFromLastKnown()`:
   ```go
   // SeedFromLastKnown populates bestBidOpen/bestAskOpen from lastKnownBid/lastKnownAsk
   // when the bar has no open quote yet. Called by accWriter.Flush() on every bar close
   // to ensure empty-second null rows still carry the last known OB state.
   func (a *Accumulator) SeedFromLastKnown() {
       if !a.hasOpenQuote && a.hasLastKnownOB {
           a.bestBidOpen = a.lastKnownBid
           a.bestAskOpen = a.lastKnownAsk
           a.hasOpenQuote = true
           // Do NOT set hasCloseQuote — the close quote requires a tick this bar.
       }
   }
   ```

7. `accWriter.Flush()` in `cmd/candle/main.go` calls `aw.acc.SeedFromLastKnown()` BEFORE `CurrentBar()`:
   ```go
   func (aw *accWriter) Flush(isPartial bool) error {
       bids, asks := aw.ob.AllBids(), aw.ob.AllAsks()
       if len(bids) > 0 || len(asks) > 0 {
           aw.acc.SetCloseDepth(features.ComputeDepthSnapshot(bids, asks))
       }
       aw.acc.SeedFromLastKnown()  // carry-forward for empty-second null rows
       tsSecMs := time.Now().Truncate(time.Second).UnixMilli()
       bar := aw.acc.CurrentBar(tsSecMs, isPartial)
       if err := aw.w.WriteBar(aw.ctx, bar); err != nil {
           return err
       }
       aw.acc.BarReset()
       aw.openDepthSet = false
       return nil
   }
   ```

8. Test: after a bar with valid OB ticks followed by an empty bar (no ticks):
   - Empty bar's `BestBidOpen` == last bar's `BestBid` (carry-forward works).
   - Empty bar's `BestBid` (close) == nil (no ticks, no close quote).
   - Empty bar's `OFI` == nil.
   - Empty bar's `BestBidChanges` == nil (no hasQuoteActivity).

### Null-row close depth carry-forward (CS-FR18)

9. For empty-second bars, `SetCloseDepth` already runs if `len(bids) > 0 || len(asks) > 0` (existing code in accWriter.Flush). This means the close depth IS populated for empty bars when the OB is warm. This is correct behavior — depth is the current OB state, not a tick-by-tick snapshot. No change needed.

### gap_count and bar_count writer cleanup (CS-FR17)

10. `bar_count` in the writer: currently written unconditionally as `Int64Column("bar_count", int64(bar.BarCount))`. This is correct — always write it. No change needed to writer for bar_count.

11. `gap_count` in the writer: currently written unconditionally. Also correct — 0 is a valid value. No change needed.

### `go test ./...` (AC: 12)

12. `go test ./...` passes. New L1 tests:
    - barCount increments across three consecutive bars (AC 1–2).
    - `OFI` is nil for empty bar (no Apply calls); non-nil (zero) when only OB delta ticks.
    - `OFI` is non-nil for trade-only bars.
    - `SeedFromLastKnown`: no-op when `hasOpenQuote` already true; no-op when `!hasLastKnownOB`; populates `bestBidOpen`/`bestAskOpen` correctly when called on empty bar after a warm bar.
    - `BestBidOpen` carry-forward scenario: warm bar sets lastKnownBid → empty bar's SeedFromLastKnown → empty bar BestBidOpen == lastKnownBid.
    - `BestBid` (close) nil on empty bar.
    - `Reset()` clears `barCount` to 0 (first bar after gap starts at 1 again).

## Tasks / Subtasks

- [x] Fix `barCount` — increment in `BarReset()`, clear in `Reset()` (AC: 1, 2)
  - [x] Add `a.barCount++` at end of `BarReset()` in `internal/accumulator/accumulator.go`
  - [x] Add `a.barCount = 0` to `Reset()` (already implicitly 0 via BarReset, but `Reset()` calls `BarReset()` — the increment would fire; add explicit `a.barCount = 0` AFTER `a.BarReset()` in `Reset()`)
  - [x] L1 tests

- [x] Fix `Bar.OFI` and `Bar.OFIL1` to `*float64`; add `hasTicks` guard (AC: 3, 4)
  - [x] Rename `Bar.OFI float64` → `Bar.OFI *float64` in `internal/accumulator/accumulator.go`
  - [x] Add `Bar.OFIL1 *float64` (rename existing implicit L1 field or add new field)
  - [x] Add `hasTicks bool` to Accumulator state
  - [x] Set `a.hasTicks = true` in `Apply()`
  - [x] Update `CurrentBar()` to set OFI fields conditionally
  - [x] Clear `hasTicks` in `BarReset()`
  - [x] Update `internal/writer/questdb/writer.go` OFI writes to nil-check pattern
  - [x] Fix any accumulator_test.go references to `bar.OFI` (now pointer, deref with `*`)
  - [x] L1 tests

- [x] Add `SeedFromLastKnown()` to Accumulator (AC: 6)
  - [x] Add method to `internal/accumulator/accumulator.go`
  - [x] L1 tests

- [x] Call `SeedFromLastKnown()` in `accWriter.Flush()` (AC: 7)
  - [x] Update `cmd/candle/main.go`

- [x] Integration test for empty-second carry-forward (AC: 8)
  - [x] L1 tests in `internal/accumulator/accumulator_test.go`

## Dev Notes

### barCount: Reset() must zero AFTER BarReset() increments
`Reset()` calls `BarReset()` then adds its own cleanup. Since `BarReset()` now does `a.barCount++`, `Reset()` must explicitly zero it afterwards:
```go
func (a *Accumulator) Reset() {
    a.BarReset()
    a.barCount = 0           // undo the BarReset increment — Reset means "start over"
    a.lastKnownBid = 0
    a.lastKnownAsk = 0
    a.hasLastKnownOB = false
    // ... hasLastMid, lastMidPrice (from 7-3) ...
}
```

### Bar.OFIL1 — renaming from the writer perspective
Currently the writer writes:
```go
row = row.Float64Column("ofi", bar.OFI).Float64Column("ofi_l1", bar.OFI)
```
Both use the same `bar.OFI` value. After this story, they become separate `*float64` fields. The full-book OFI column (`ofi`) is deferred to a future epic (Epic 8 or 9). For now, both `ofi` and `ofi_l1` remain the same value (the existing `ofiSum`). Just add `Bar.OFIL1 *float64` and in `CurrentBar()` set both:
```go
if a.hasTicks {
    bar.OFI = ptr(a.ofiSum)
    bar.OFIL1 = ptr(a.ofiSum)
}
```

### SeedFromLastKnown does NOT set hasCloseQuote
The open-quote fields (`BestBidOpen`, `BestAskOpen`) represent the first quote seen THIS bar. If the bar had no ticks, the "open" is carried from the last known state — this is meaningful context for downstream consumers. The close quote (`BestBid`, `BestAsk`) should remain nil on an empty bar because no tick this second updated the close quote.

### Null rows write ALL non-nil fields — verify DDL allows NULL for all fields
The DDL has `DEDUP UPSERT KEYS(ts, exchange, symbol)` — a null row written for a second with no trades is fully valid. The writer writes all available non-nil fields; columns not written are NULL in QuestDB (ILP protocol behavior).

### `trade_count` is always written
`trade_count` is always written unconditionally (including 0 for empty bars). This is intentional — it allows downstream to distinguish "bar exists, no trades" from "bar missing." No change.

### Writer regression: existing `OFI float64` references
Anywhere in the codebase that reads `bar.OFI` directly (not via pointer) must be updated to `*bar.OFI` or a nil-check. Search: `bar\.OFI` to find all references.

## Dev Agent Record

### Completion Notes
All ACs satisfied. Key decisions:
- `BarReset()` increments `barCount` at the end; `Reset()` zeros it explicitly after calling `BarReset()` to prevent the increment from persisting after a gap/snapshot.
- `hasTicks` flag added to Accumulator — set in `Apply()` before any other work, cleared in `BarReset()`.
- `Bar.OFI` and `Bar.OFIL1` are separate `*float64` fields; both populated with `ofiSum` when hasTicks (full-book OFI deferred to Epic 8).
- `SeedFromLastKnown()` only sets `hasOpenQuote` (not `hasCloseQuote`), preserving correct nil close quote for empty bars.

### File List
- internal/accumulator/accumulator.go (modified — hasTicks, SeedFromLastKnown, barCount fix, OFI pointer change)
- internal/accumulator/accumulator_test.go (modified — OFI pointer fix + 10 new L1 tests)
- internal/writer/questdb/writer.go (modified — nil-check OFI writes, separate OFI/OFIL1)
- cmd/candle/main.go (modified — SeedFromLastKnown() call in Flush())

### Change Log
- 2026-05-08: Story 7-5 implemented — barCount fix, OFI nil fix, SeedFromLastKnown carry-forward
