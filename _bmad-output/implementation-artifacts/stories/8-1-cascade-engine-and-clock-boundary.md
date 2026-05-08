# Story 8.1: Cascade Engine & Clock Boundary

Status: done

## Story

As the candle service,
I want a pure cascade engine that folds closed 1-second bars into higher-timeframe OHLCV accumulators and detects UTC bar-close boundaries for 1m/5m/15m/1h/4h/1d/1w,
so that higher-timeframe bars can be published and reconstructed correctly across service restarts without replaying individual 1s rows from QuestDB.

## Acceptance Criteria

### AC 1 — `internal/cascade/` package compiles clean with zero IO

1. `internal/cascade/` is a new pure package with zero imports from `consumer/`, `writer/`, `health/`, `metrics/`, `migrator/`, or any package that performs IO.
2. The package defines `type Clock interface { Now() time.Time }` — same structural interface pattern as `accumulator.Clock`. No import of accumulator's Clock (define independently).
3. `go test ./internal/cascade/...` passes with no race detector warnings.

### AC 2 — Timeframe constants and boundary detection

4. The following `TF` constants are defined: `TF1m TF = "1m"`, `TF5m = "5m"`, `TF15m = "15m"`, `TF1h = "1h"`, `TF4h = "4h"`, `TF1d = "1d"`, `TF1w = "1w"`. `AllTFs []TF` lists them in ascending duration order.

5. `IsBarClose(tsSecMs int64, tf TF) bool` returns true when the second represented by tsSecMs is the LAST second of a TF bar (i.e., the NEXT second starts a new bar). Algorithm: `nextSecMs = tsSecMs + 1000`. For 1m–4h: `time.Unix(nextSecMs/1000, 0).UTC().Truncate(tfDuration) > time.Unix(tsSecMs/1000, 0).UTC().Truncate(tfDuration)`. For 1d: same with 24h truncation. For 1w: `nextT := time.Unix(nextSecMs/1000, 0).UTC(); nextT.Weekday() == time.Monday && nextT.Hour() == 0 && nextT.Minute() == 0 && nextT.Second() == 0`. **Never use `time.Truncate` for 1w** — it gives wrong results for week boundaries.

6. L1 test table for `IsBarClose`:
   - tsSecMs = 59_000 (00:00:59 UTC) → 1m = true, others = false
   - tsSecMs = 299_000 (00:04:59 UTC) → 5m = true, 15m = false, 1m = false
   - tsSecMs = 899_000 (00:14:59 UTC) → 15m = true, 1h = false
   - tsSecMs = 3_599_000 (00:59:59 UTC) → 1h = true, 4h = false
   - tsSecMs = 14_399_000 (03:59:59 UTC) → 4h = true, 1d = false
   - tsSecMs = 86_399_000 (23:59:59 UTC Monday) → 1d = true
   - A second that is 23:59:59 UTC on Sunday → 1w = true, 1d = true
   - A second at 00:00:00 UTC Tuesday → no TF closes (not Monday, 4h doesn't close there)
   - The 1w case: use a real Monday boundary timestamp. 2026-05-04 23:59:59 UTC (Sunday evening) → 1w = true when next second is Monday 2026-05-05 00:00:00 UTC.

### AC 3 — `CascadeAcc` struct and nil-fold semantics

7. `CascadeAcc` is an unexported struct holding the 9 cascade fields for one TF:
   ```
   openTs      int64    // Unix ms of first 1s bar's tsSecMs; 0 = uninitialised
   open        *float64
   high        *float64
   low         *float64
   close_val   *float64  // named closeVal to avoid conflict with builtin close
   volume      float64
   quoteVol    float64
   tradeCount  int
   barCount    int
   gapCount    int
   ```

8. `fold(bar accumulator.Bar)` on CascadeAcc applies the nil-fold rules:
   - `openTs`: set once on first call (when `openTs == 0`), never overwritten.
   - `open`: set once on first call where `bar.Open != nil`, never overwritten.
   - `high`: if `bar.High != nil`, update to max of current and `*bar.High`; skip if nil.
   - `low`: if `bar.Low != nil`, update to min of current and `*bar.Low`; skip if nil.
   - `closeVal`: if `bar.Close != nil`, update to `*bar.Close`; if nil, carry last known (no update).
   - `volume`: `+= bar.Volume` if not nil, else +0.
   - `quoteVol`: same.
   - `tradeCount`: `+= bar.TradeCount` (TradeCount is int, never nil).
   - `barCount`: always +1.
   - `gapCount`: `+= bar.GapCount`.

9. `reset(openTs int64)` clears all fields except sets `openTs` to the new bar's start time.

10. `toBar(exchange, symbol string, tf TF, isComplete bool) Bar` produces the public `Bar` snapshot.

### AC 4 — `Engine` struct API

11. `Engine` manages 7 `CascadeAcc` instances (one per TF). Exported API:

   ```go
   func NewEngine(exchange, symbol string, clk Clock) *Engine
   
   // Fold folds a closed 1s bar into all TF cascade accumulators.
   // Returns the list of TF Bars whose boundary closed at tsSecMs.
   // Called from accWriter.Flush() with the just-written bar and its tsSecMs.
   func (e *Engine) Fold(bar accumulator.Bar, tsSecMs int64) []Bar
   
   // CurrentBar returns a read-only snapshot of the in-progress cascade bar.
   // Returns a zero Bar (OpenTs == 0) if no data has been accumulated yet.
   func (e *Engine) CurrentBar(tf TF) Bar
   
   // RestoreFromHash restores a TF accumulator from a Redis HASH field map.
   // Absent or unparseable fields are silently treated as zero/nil.
   func (e *Engine) RestoreFromHash(tf TF, fields map[string]string)
   
   // ToHash serialises the current TF accumulator to a Redis HASH field map.
   // All 9 fields are always written (missing fields on read = zero/default).
   func (e *Engine) ToHash(tf TF) map[string]string
   ```

12. `Fold` implementation: for each TF in `AllTFs`, call `acc.fold(bar)`. Then check `IsBarClose(tsSecMs, tf)`: if true, snapshot `acc.toBar(...)` with `isComplete: true`, call `acc.reset(tsSecMs + 1000)`, append to closed list. Return closed list (may be empty).

### AC 5 — HASH serialisation format

13. `ToHash` produces a `map[string]string` with exactly these keys: `open_ts`, `open`, `high`, `low`, `close`, `volume`, `quote_vol`, `trade_count`, `bar_count`, `gap_count`.
    - `open_ts`: `strconv.FormatInt(openTs, 10)`. Value is 0 if uninitialised.
    - `open`, `high`, `low`, `close`: `""` when nil; `strconv.FormatFloat(*v, 'f', -1, 64)` when non-nil.
    - `volume`, `quote_vol`: `strconv.FormatFloat(v, 'f', -1, 64)`.
    - `trade_count`, `bar_count`, `gap_count`: `strconv.Itoa(n)`.

14. `RestoreFromHash` is the inverse: parses each field, treats parse errors and empty strings as zero/nil. Unknown keys in the map are silently ignored (schema evolution safety).

15. L1 test: round-trip `ToHash` → `RestoreFromHash` preserves all 9 fields for a non-trivial CascadeAcc state.

### AC 6 — L1 test coverage for Engine.Fold

16. Table-driven L1 test for `Fold`:
    - Single 1s bar with all non-nil OHLCV fields → cascade accumulator populated correctly.
    - 1s bar with nil OHLC (no-trade second) → volume and barCount advance; OHLC unchanged.
    - Two consecutive 1s bars: bar 1 has trades, bar 2 is nil → cascade close carries bar 1's close.
    - 1m boundary: fold 59 bars (none close 1m), fold bar 60 → `Fold` returns a 1m close. 1m accumulator resets with `openTs = tsSecMs + 1000`.
    - After 1m close: `CurrentBar(TF1m)` returns a zero Bar (just reset).
    - 1w close test: construct a tsSecMs that is Sunday 23:59:59 UTC; 1w = true (and 1d = true too if it's also 23:59:59 on a day boundary — test each independently).

### AC 7 — Startup reconstruction in `cmd/candle/main.go`

17. In `main.go`, reconstruction runs synchronously BEFORE consumer goroutines are started. The reconstruction sequence for each `(exchange, symbol)`:

   a. For each TF in `AllTFs`:
      - `HGETALL candle:acc:{exchange}:{symbol}:{tf}` from Redis.
      - If result is non-empty and has `open_ts`: call `engine.RestoreFromHash(tf, fields)`.
      - If result is empty or `open_ts` is absent/zero: log INFO "cascade reconstruction: no hash for {tf}, starting empty".

   b. Run completeness check (only if HASH was restored and `openTs > 0`):
      - Determine `boundary = lastClosedBoundary(tf, now)` — the most recent closed boundary for this TF at or before `now`.
      - If `openTs >= boundary.UnixMilli()`: the current bar just started, nothing to verify. Skip check.
      - Otherwise: call `querySnapshotCount(ctx, questDBHTTPAddr, exchange, symbol, openTs, boundary.UnixMilli())`.
      - If `count < int64(float64(expectedBarCount)*0.95)`: log WARN with `tf`, `openTs`, `boundary`, `count`, `expectedBarCount`; continue (do not panic, do not emit gap marker from main — the cascade engine marks the bar incomplete by setting `gapCount += 1` in the restored accumulator, or simply log and continue with the restored state).

18. `querySnapshotCount` is a standalone function in `cmd/candle/main.go` (or a separate file `cmd/candle/reconstruction.go`):
    ```go
    func querySnapshotCount(ctx context.Context, httpAddr, exchange, symbol string, fromMs, toMs int64) (int64, error)
    ```
    Uses `http.Client.Get` with QuestDB `/exec?query=` endpoint. Query:
    ```sql
    SELECT count() FROM snapshot_1s WHERE exchange='<exchange>' AND symbol='<symbol>' AND ts_second >= <fromMs>000 AND ts_second < <toMs>000
    ```
    (QuestDB timestamps are microseconds; multiply ms by 1000.) Response JSON: `{"dataset":[[N]]}` — parse the first element of the first row as int64.

19. If QuestDB is unavailable (network error or non-200 response): log ERROR, skip completeness check for all symbols, continue with restored HASH state.

20. Consumer goroutines start only AFTER the reconstruction loop (AC 7a/7b) completes for all symbols.

### AC 8 — Cascade HASH writes in `accWriter.Flush()`

21. `accWriter` in `main.go` gains a new field `engine *cascade.Engine`.

22. In `accWriter.Flush()`, after `aw.w.WriteBar(...)` succeeds and before `aw.acc.BarReset()`, add:
    ```go
    closedBars := aw.engine.Fold(bar, tsSecMs)
    aw.persistCascadeHashes(closedBars)
    ```

23. `persistCascadeHashes(closedBars []cascade.Bar)` is a new method on `accWriter`:
    - For each TF in `cascade.AllTFs`:
      - `fields := aw.engine.ToHash(tf)` (always write all 7 TFs after each 1s close)
      - `key := "candle:acc:" + aw.exchange + ":" + aw.symbol + ":" + string(tf)`
      - Use `aw.rdb.HSet(ctx, key, fields)` with backoff retry: max 3 retries, initial 50ms, max 2s (reuse `internal/backoff/`).
      - On exhausted retries: log ERROR with exchange/symbol/tf; increment `m.CascadeStateWriteFailureTotal.WithLabelValues(...)`. Continue — never block the consumer loop.

24. `CascadeStateWriteFailureTotal` is already defined in `internal/metrics/metrics.go` and initialized in `Register()`. The `accWriter` struct receives the counter as a callback (`func(exchange, symbol, tf string)`), same pattern as `w.SetWALDropCounter`. This avoids accWriter importing metrics.

### AC 9 — `go test ./...` passes

25. `make test-l1` passes: all cascade L1 tests pass; `go vet ./...` clean.
26. `go build ./...` compiles clean with no unused imports or variables.
27. `time.Now()` and `time.Sleep()` are NOT called inside `internal/cascade/` — enforced by the existing `grep` check in the `test-l1` Makefile target (the Makefile already bans `time.Now()` outside `cmd/candle/`).

### AC 10 — Config additions

28. `internal/config/Config` gains two new fields:
    ```go
    CandleStreamMaxLen      int // CANDLE_STREAM_MAXLEN, default 10000
    CandleCloseStreamMaxLen int // CANDLE_CLOSE_STREAM_MAXLEN, default 500
    ```
    These are not used in 8-1 but must be present for 8-2 to consume without config changes.

## Senior Developer Review (AI)

**Date:** 2026-05-08
**Outcome:** Changes Requested
**High:** 1 | **Med:** 3 | **Low:** 3 | **Defer:** 5 | **Dismissed:** 12

### Action Items

- [x] [High] `http.DefaultClient` has no timeout in `querySnapshotCount` — hung QuestDB blocks startup indefinitely [`cmd/candle/main.go`]
- [x] [Med] `make test-l1` missing `-race` flag — AC 3 requires race-detector clean [`Makefile`]
- [x] [Med] `Engine.Fold` tests not table-driven; missing "two bars with nil close, carry" case — AC 16 [`internal/cascade/cascade_test.go`]
- [x] [Med] Single QuestDB error silences all remaining symbol/TF completeness checks — overly aggressive `questDBAvailable` flag [`cmd/candle/main.go`]
- [x] [Low] `persistCascadeHashes` sleeps after final (3rd) retry — wasteful dead wait, AC 23 [`cmd/candle/main.go`]
- [x] [Low] `TestFoldBoundary1w` doesn't assert `CurrentBar(TF1w).OpenTs == tsSecMs + 1000` after close [`internal/cascade/cascade_test.go`]
- [x] [Low] `TestHashRoundTrip` doesn't verify `QuoteVol` is preserved across round-trip [`internal/cascade/cascade_test.go`]

## Tasks / Subtasks

- [x] Create `internal/cascade/cascade.go` with TF constants, Clock, Bar struct, CascadeAcc, Engine (AC 1–4)
- [x] Implement `IsBarClose(tsSecMs int64, tf TF) bool` with 1w special case (AC 2)
- [x] Implement `CascadeAcc.fold()`, `reset()`, `toBar()` with nil-fold rules (AC 3)
- [x] Implement `Engine.Fold()`, `CurrentBar()`, `RestoreFromHash()`, `ToHash()` (AC 4)
- [x] Implement HASH serialization in `cascade.go` (AC 5)
- [x] L1 tests in `internal/cascade/cascade_test.go` (AC 6):
  - [x] `IsBarClose` table-driven test
  - [x] `fold` nil rules test (nil OHLC, nil volume, barCount always increments)
  - [x] `Fold` boundary tests (1m closes at second 59, 1w closes at Sunday 23:59:59)
  - [x] HASH round-trip test
- [x] Add `engine *cascade.Engine` to `accWriter` in `main.go` (AC 8)
- [x] Add `persistCascadeHashes()` to `accWriter` with backoff retry and failure counter (AC 8)
- [x] Update `accWriter.Flush()` to call `engine.Fold(bar, tsSecMs)` and `persistCascadeHashes()` (AC 8)
- [x] Wire cascade failure counter callback on engine creation (AC 8)
- [x] Add reconstruction loop in `main.go` before consumer goroutine start (AC 7)
- [x] Implement `querySnapshotCount()` helper (AC 7)
- [x] Add `CandleStreamMaxLen` and `CandleCloseStreamMaxLen` to `config.Config` (AC 10)
- [x] `go build ./...` and `make test-l1` pass (AC 9)

### Review Follow-ups (AI)

- [x] [AI-Review][High] Fix `http.DefaultClient` no-timeout in `querySnapshotCount`
- [x] [AI-Review][Med] Add `-race` to `test-l1` Makefile target
- [x] [AI-Review][Med] Add table-driven `Engine.Fold` tests with "consecutive bars carry close" case
- [x] [AI-Review][Med] Fix `questDBAvailable` overly-aggressive silencing of completeness checks
- [x] [AI-Review][Low] Eliminate sleep-after-final-retry in `persistCascadeHashes`
- [x] [AI-Review][Low] Add `CurrentBar(TF1w).OpenTs` assertion in `TestFoldBoundary1w`
- [x] [AI-Review][Low] Add `QuoteVol` assertion in `TestHashRoundTrip`

## Dev Notes

### Package location and dependency rules

`internal/cascade/` is a leaf node (zero internal imports). It MAY import `internal/accumulator` for the `accumulator.Bar` type — this is the only internal import permitted. Do NOT import `consumer/`, `writer/`, `blockwindow/`, `features/`, `metrics/`, or `health/`.

The dependency direction is: `cascade/` ← imported by `cmd/candle/main.go`. Not the reverse.

### Do NOT extend AccumulatorApplier.Apply()

`consumer.AccumulatorApplier.Apply()` currently has 14 parameters. Cascade works at bar-close granularity via `Engine.Fold(bar, tsSecMs)` called from `accWriter.Flush()`. **Do not add any new parameters to `AccumulatorApplier.Apply()` or `Accumulator.Apply()`.** This is a hard constraint from Epic 7 retro.

### CascadeAcc field naming: `closeVal` not `close`

Go identifier `close` is a builtin. Name the field `closeVal` in the unexported struct. The public `Bar.Close *float64` and HASH key `"close"` are still the user-facing name.

### IsBarClose algorithm — 4h and 1d use time.Truncate correctly

`time.Truncate` works correctly for sub-day durations (1m, 5m, 15m, 1h, 4h) because these align to UTC midnight automatically. `time.Truncate(24 * time.Hour)` also works for 1d (UTC midnight). **Only 1w is a special case** — `time.Truncate` cannot compute Monday boundaries. Use the explicit weekday check.

```go
func IsBarClose(tsSecMs int64, tf TF) bool {
    curr := time.Unix(tsSecMs/1000, 0).UTC()
    next := curr.Add(time.Second)
    switch tf {
    case TF1m:
        return next.Truncate(time.Minute).After(curr.Truncate(time.Minute))
    case TF5m:
        return next.Truncate(5*time.Minute).After(curr.Truncate(5*time.Minute))
    case TF15m:
        return next.Truncate(15*time.Minute).After(curr.Truncate(15*time.Minute))
    case TF1h:
        return next.Truncate(time.Hour).After(curr.Truncate(time.Hour))
    case TF4h:
        return next.Truncate(4*time.Hour).After(curr.Truncate(4*time.Hour))
    case TF1d:
        return next.Truncate(24*time.Hour).After(curr.Truncate(24*time.Hour))
    case TF1w:
        return next.Weekday() == time.Monday && next.Hour() == 0 && next.Minute() == 0 && next.Second() == 0
    }
    return false
}
```

### Engine.Fold implementation

```go
func (e *Engine) Fold(bar accumulator.Bar, tsSecMs int64) []Bar {
    var closed []Bar
    for _, tf := range AllTFs {
        acc := e.accs[tf]
        acc.fold(bar)
        if IsBarClose(tsSecMs, tf) {
            closed = append(closed, acc.toBar(e.exchange, e.symbol, tf, true))
            acc.reset(tsSecMs + 1000)
        }
    }
    return closed
}
```

`e.accs` is `map[TF]*CascadeAcc` initialized in `NewEngine`.

### CascadeAcc.fold nil-fold rules (exact implementation)

```go
func (a *CascadeAcc) fold(bar accumulator.Bar) {
    if a.openTs == 0 {
        a.openTs = bar.TsSecMs
    }
    if a.open == nil && bar.Open != nil {
        v := *bar.Open
        a.open = &v
    }
    if bar.High != nil {
        if a.high == nil || *bar.High > *a.high {
            v := *bar.High
            a.high = &v
        }
    }
    if bar.Low != nil {
        if a.low == nil || *bar.Low < *a.low {
            v := *bar.Low
            a.low = &v
        }
    }
    if bar.Close != nil {
        v := *bar.Close
        a.closeVal = &v
    }
    if bar.Volume != nil {
        a.volume += *bar.Volume
    }
    if bar.QuoteVolume != nil {
        a.quoteVol += *bar.QuoteVolume
    }
    a.tradeCount += bar.TradeCount
    a.barCount++
    a.gapCount += bar.GapCount
}
```

**Important:** `open/high/low/closeVal` store pointer values — always copy the float64 when assigning (never store the pointer from `bar` directly, as `bar` is a value type and its pointer fields point to stack-allocated values that go stale).

### Engine.ToHash — always write all 9 fields

Even when a field is nil or zero, write it. This ensures that a HASH created by a future version (with new fields) doesn't confuse this version's `RestoreFromHash`. All 9 fields are always present.

```go
func (e *Engine) ToHash(tf TF) map[string]string {
    a := e.accs[tf]
    m := make(map[string]string, 10)
    m["open_ts"] = strconv.FormatInt(a.openTs, 10)
    m["open"]       = floatOrEmpty(a.open)
    m["high"]       = floatOrEmpty(a.high)
    m["low"]        = floatOrEmpty(a.low)
    m["close"]      = floatOrEmpty(a.closeVal)
    m["volume"]     = strconv.FormatFloat(a.volume, 'f', -1, 64)
    m["quote_vol"]  = strconv.FormatFloat(a.quoteVol, 'f', -1, 64)
    m["trade_count"]= strconv.Itoa(a.tradeCount)
    m["bar_count"]  = strconv.Itoa(a.barCount)
    m["gap_count"]  = strconv.Itoa(a.gapCount)
    return m
}

func floatOrEmpty(f *float64) string {
    if f == nil { return "" }
    return strconv.FormatFloat(*f, 'f', -1, 64)
}
```

### lastClosedBoundary helper

Needed for the completeness check in AC 7:

```go
// lastClosedBoundary returns the most recent closed bar boundary at or before now for tf.
func lastClosedBoundary(tf cascade.TF, now time.Time) time.Time {
    switch tf {
    case cascade.TF1m:  return now.UTC().Truncate(time.Minute)
    case cascade.TF5m:  return now.UTC().Truncate(5 * time.Minute)
    case cascade.TF15m: return now.UTC().Truncate(15 * time.Minute)
    case cascade.TF1h:  return now.UTC().Truncate(time.Hour)
    case cascade.TF4h:  return now.UTC().Truncate(4 * time.Hour)
    case cascade.TF1d:  return now.UTC().Truncate(24 * time.Hour)
    case cascade.TF1w:
        // Most recent Monday 00:00:00 UTC at or before now.
        t := now.UTC()
        daysToMonday := int(t.Weekday())
        if daysToMonday == 0 { daysToMonday = 7 } // Sunday → go back 7 days
        daysToMonday = (daysToMonday - 1 + 7) % 7 // Monday = 0 days back
        monday := time.Date(t.Year(), t.Month(), t.Day()-daysToMonday, 0, 0, 0, 0, time.UTC)
        return monday
    }
    return time.Time{}
}
```

### querySnapshotCount — QuestDB timestamp microseconds

QuestDB's `TIMESTAMP` type uses microseconds since epoch. The `snapshot_1s` table's `ts_second` column is a `TIMESTAMP`. The accumulator writes it as Unix milliseconds via ILP, which QuestDB stores as microseconds by multiplying by 1000 internally.

When querying: multiply `fromMs` and `toMs` by 1000 to convert to microseconds:
```go
query := fmt.Sprintf(
    "SELECT count() FROM snapshot_1s WHERE exchange='%s' AND symbol='%s' AND ts_second >= %d AND ts_second < %d",
    exchange, symbol, fromMs*1000, toMs*1000,
)
u := "http://" + httpAddr + "/exec?query=" + url.QueryEscape(query)
```

Parse response: QuestDB returns `{"columns":[...],"dataset":[[N]],...}`. The count is `dataset[0][0]` as a float64 (JSON numbers).

### accWriter changes — cascade counter callback

Wire the failure counter via a callback to avoid importing metrics from accWriter:

```go
type accWriter struct {
    // ... existing fields ...
    engine                    *cascade.Engine
    cascadeFailureCounter     func(tf string) // called on HASH write failure
}
```

In `main.go` during symbolEntry setup:
```go
exchange, symbol := p.exchange, p.symbol
aw.cascadeFailureCounter = func(tf string) {
    m.CascadeStateWriteFailureTotal.WithLabelValues(exchange, symbol, tf).Inc()
}
```

Wait — the existing metric is `CascadeStateWriteFailureTotal` with labels `{exchange, symbol}` (2 labels). Check `metrics.go`: the metric is registered with `[]string{"exchange", "symbol"}`. Do NOT add a `tf` label — that would change the existing metric definition. The failure callback is:
```go
aw.cascadeFailureCounter = func() {
    m.CascadeStateWriteFailureTotal.WithLabelValues(exchange, symbol).Inc()
}
```

And `persistCascadeHashes` logs the tf in the ERROR log for diagnosis.

### MockClock for L1 tests

L1 tests for `IsBarClose` can use raw `int64` timestamps directly — no Clock needed (it's a pure function). Tests for `Engine.Fold` with boundary detection also just use `int64` timestamps. The `Clock` interface is defined for future use by the partial-publish ticker in story 8-2 (to let the engine know the current time for `CurrentBar` timestamps). For 8-1, `Clock` is defined but `Engine` may not use it internally — the timestamp comes from `Fold(bar, tsSecMs)` parameters, not from `clk.Now()`.

### Reconstruction order — all symbols before any consumer starts

```go
// In main():
engines := make(map[string]*cascade.Engine) // key = "exchange:symbol"
for _, p := range pairs {
    eng := cascade.NewEngine(p.exchange, p.symbol, wallClock{})
    for _, tf := range cascade.AllTFs {
        key := "candle:acc:" + p.exchange + ":" + p.symbol + ":" + string(tf)
        if fields, err := rdb.HGetAll(ctx, key).Result(); err == nil && len(fields) > 0 {
            eng.RestoreFromHash(tf, fields)
        }
    }
    engines[p.exchange+":"+p.symbol] = eng
}
// Completeness checks (skip if QuestDB unavailable — catch error from first check)
questDBAvailable := true
for _, p := range pairs {
    if !questDBAvailable { break }
    eng := engines[p.exchange+":"+p.symbol]
    for _, tf := range cascade.AllTFs {
        bar := eng.CurrentBar(tf)
        if bar.OpenTs == 0 { continue }
        boundary := lastClosedBoundary(tf, time.Now())
        if bar.OpenTs >= boundary.UnixMilli() { continue }
        expected := int64((boundary.UnixMilli() - bar.OpenTs) / 1000)
        count, err := querySnapshotCount(ctx, cfg.QuestDBHTTPAddr, p.exchange, p.symbol, bar.OpenTs, boundary.UnixMilli())
        if err != nil {
            logger.Error("cascade reconstruction: QuestDB unavailable", "error", err)
            questDBAvailable = false
            break
        }
        if count < int64(float64(expected)*0.95) {
            logger.Warn("cascade reconstruction incomplete",
                "exchange", p.exchange, "symbol", p.symbol, "tf", tf,
                "expected", expected, "actual", count)
        }
    }
}
// NOW start consumer goroutines:
for _, p := range pairs {
    eng := engines[p.exchange+":"+p.symbol]
    aw.engine = eng
    // ... wg.Add, go func ...
}
```

### Existing Flush() sequence — where Fold goes

Current `accWriter.Flush()`:
```go
func (aw *accWriter) Flush(isPartial bool) error {
    bids, asks := aw.ob.AllBids(), aw.ob.AllAsks()
    if len(bids) > 0 || len(asks) > 0 {
        aw.acc.SetCloseDepth(features.ComputeDepthSnapshot(bids, asks))
    }
    aw.acc.SeedFromLastKnown()
    tsSecMs := time.Now().Truncate(time.Second).UnixMilli()
    bar := aw.acc.CurrentBar(tsSecMs, isPartial)
    if err := aw.w.WriteBar(aw.ctx, bar); err != nil {
        return err
    }
    aw.acc.BarReset()
    aw.openDepthSet = false
    aw.persistBlockWindow()
    return nil
}
```

New version adds cascade fold AFTER WriteBar and BEFORE BarReset:
```go
    if err := aw.w.WriteBar(aw.ctx, bar); err != nil {
        return err
    }
    if !isPartial {
        // Only fold 1s bars into cascade on full bar close, not partial flushes.
        closedBars := aw.engine.Fold(bar, tsSecMs)
        aw.persistCascadeHashes(closedBars)
    }
    aw.acc.BarReset()
```

**Important:** `engine.Fold()` is called only on full bar closes (`isPartial == false`). Partial flushes (mid-second snapshot events) do NOT fold into the cascade — only complete 1s bars count.

### persistCascadeHashes — backoff retry pattern

Reuse `internal/backoff/` for the retry loop (already a dependency in the service). The backoff package provides `Duration(attempt int) time.Duration`. Each HSET call writes all 9 fields in one round trip.

```go
func (aw *accWriter) persistCascadeHashes(_ []cascade.Bar) {
    for _, tf := range cascade.AllTFs {
        fields := aw.engine.ToHash(tf)
        key := "candle:acc:" + aw.exchange + ":" + aw.symbol + ":" + string(tf)
        var lastErr error
        bo := backoff.New(50*time.Millisecond, 2, 2*time.Second)
        for attempt := 0; attempt < 3; attempt++ {
            if err := aw.rdb.HSet(aw.ctx, key, fields).Err(); err == nil {
                lastErr = nil
                break
            } else {
                lastErr = err
                time.Sleep(bo.Duration(attempt))
            }
        }
        if lastErr != nil {
            slog.ErrorContext(aw.ctx, "cascade state write failed",
                "exchange", aw.exchange, "symbol", aw.symbol, "tf", tf, "error", lastErr)
            if aw.cascadeFailureCounter != nil {
                aw.cascadeFailureCounter()
            }
        }
    }
}
```

Note: `time.Sleep` is called here (inside `cmd/candle/`, not inside `internal/cascade/`) — this is permitted. The grep ban on `time.Sleep` only applies to packages under `internal/`.

Actually — check the Makefile's grep pattern for the `time.Sleep` ban. If it bans `time.Sleep` in `cmd/candle/` too, use a different retry mechanism (e.g., context.WithTimeout + channel select). But the existing backoff usage in `accWriter.persistBlockWindow()` doesn't use sleep — it's fire-and-forget. For cascade writes, the retry with sleep is acceptable in `cmd/candle/` (it's the composition root).

The `backoff.New` API: check `internal/backoff/backoff.go` to see the actual constructor signature before implementing. The pattern from previous stories is `backoff.New(initial, multiplier, max)` but verify.

### Test file placement

`internal/cascade/cascade_test.go` uses `package cascade_test` (black-box). The `IsBarClose` function and `Engine.Fold` return value are the only observable interfaces needed for the table-driven tests.

## Dev Agent Record

### Completion Notes

All 10 ACs implemented and verified. Key decisions:

- `IsBarClose` uses `time.Truncate` for 1m–1d; explicit weekday check for 1w as specified.
- `cascadeAcc` fields use pointer copy-on-write for OHLC (never store pointer from Bar directly).
- Backoff constructor is `New(initial, max time.Duration, multiplier, jitter float64)` — signature differs from story dev notes (which said `New(50ms, 2, 2s)`); actual args are `New(50ms, 2s, 2.0, 0.2)`.
- Main loop restructured into 3 phases: build entries, reconstruction, goroutine start.
- Story spec claimed "2026-05-04 is Sunday" but it's Monday; test uses 2026-05-03 (actual Sunday).
- `make test-l1` passes; `go build ./...` clean; `go vet ./...` clean.

### File List

- `candle-service/internal/cascade/cascade.go` — new
- `candle-service/internal/cascade/cascade_test.go` — new
- `candle-service/internal/config/config.go` — modified (added CandleStreamMaxLen, CandleCloseStreamMaxLen)
- `candle-service/cmd/candle/main.go` — modified (added cascade engine, reconstruction, persistCascadeHashes, helpers)
- `candle-service/Makefile` — modified (added cascade to test-l1 target)

### Change Log

- 2026-05-08: Story implemented. Created cascade package with full Engine API, HASH serialization, IsBarClose with 1w special case. Integrated cascade fold into accWriter.Flush(), added startup reconstruction loop in main(), added querySnapshotCount and lastClosedBoundary helpers.
