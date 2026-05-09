# Story 9.3: Flush Failure Alerting

Status: done

## Story

As the operations team,
I want flush failures to publish an alert entry to `alerts:flush_failure` in Redis,
So that downstream monitoring can react immediately to data-loss risk rather than waiting for a manifest audit.

## Acceptance Criteria

### AC 1 — Alert publish on flush failure

1. When `flushDate(ctx, date)` returns a non-nil error, the flusher publishes to Redis stream `alerts:flush_failure` via `XADD alerts:flush_failure * <fields>` with exactly these fields:

   | Field | Value |
   |---|---|
   | `ts` | current Unix milliseconds as string |
   | `date` | failed date as `"YYYY-MM-DD"` |
   | `error_msg` | `err.Error()` truncated to 512 chars |
   | `b2_path` | the intended B2 path (e.g. `snapshot_1s/date=2026-05-07/data.parquet`) |

2. The alert publish uses `MAXLEN ~ 1000` (approximate, no hard cap on the stream itself needed — use `redis.XAddArgs{MaxLen: 1000, Approx: true}`).
3. The alert publish is attempted with a **new 10-second timeout context** (`context.WithTimeout(context.Background(), 10*time.Second)`) — never the cancelled upload context.

### AC 2 — Fallback counter when alert publish fails

4. If the XADD to `alerts:flush_failure` itself fails (Redis unavailable, etc.), increment the `candle_flush_alert_failure_total` Prometheus counter. This counter is already registered in `internal/metrics` — the flusher receives it via a `alertFailureFn func()` callback.
5. Do NOT panic, return an error from the alert path, or retry the XADD. Log `ERROR` with `exchange`, `date`, `alert_error` and swallow.

### AC 3 — Prometheus counters wired for flush success/failure

6. On each successful `flushDate` call: increment `candle_flush_success_total` (already in metrics).
7. On each failed `flushDate` call: increment `candle_flush_failure_total` (already in metrics).
8. Both counters are received by the flusher as callbacks: `successFn func()` and `failureFn func()` — same pattern as cascade/redis failure counters.

### AC 4 — Flush order: manifest row before alert

9. Within a failed flush, the `flush_manifest` row (success=false) is written **before** the alert is published. Rationale: if the manifest write also fails, the alert still fires — but if the alert fires before the manifest write and the manifest write fails, the operator has an alert with no manifest evidence to query.

### AC 5 — Flusher constructor updated

10. `flusher.New` gains two additional callback parameters:
    - `successFn func()` — called after each successful flush
    - `failureFn func()` — called after each failed flush
    - `alertFailureFn func()` — called when alert XADD fails
11. When any callback is nil, skip the call (nil guard before invocation).
12. `main.go` wires all three callbacks from `m.FlushSuccessTotal.Inc`, `m.FlushFailureTotal.Inc`, `m.FlushAlertFailureTotal.Inc`.

### AC 6 — L1 tests

13. `flusher/flusher_test.go` gains at least 2 new L1 tests that do not require real Redis or QuestDB — use a mock flush function that simulates failure:
    - **`TestAlertFieldsOnFailure`**: given a flush failure with error `"upload timeout"` and date `2026-05-06`, assert the alert fields map contains `date="2026-05-06"`, `error_msg` starts with `"upload timeout"`, `b2_path="snapshot_1s/date=2026-05-06/data.parquet"`, and `ts` is a non-empty string.
    - **`TestManifestBeforeAlert`**: given a flush failure, assert (via call order tracking) that the manifest write callback is invoked before the alert publish callback.

### AC 7 — Build and test gates

14. `go build ./...` compiles clean.
15. `make test-l1` passes with all new L1 tests.

---

## Dev Notes

### Alert publish pattern

The alert publish is fire-and-forget (no retry). The single attempt with a 10-second context is sufficient. The `alertFailureFn` counter is the durable evidence that alerting is degraded.

```go
func (f *Flusher) publishFlushAlert(date time.Time, b2Path string, flushErr error) {
    alertCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
    defer cancel()

    msg := flushErr.Error()
    if len(msg) > 512 {
        msg = msg[:512]
    }

    err := f.rdb.XAdd(alertCtx, &redis.XAddArgs{
        Stream: "alerts:flush_failure",
        MaxLen: 1000,
        Approx: true,
        Values: map[string]any{
            "ts":        strconv.FormatInt(time.Now().UnixMilli(), 10),
            "date":      date.Format("2006-01-02"),
            "error_msg": msg,
            "b2_path":   b2Path,
        },
    }).Err()
    if err != nil {
        slog.ErrorContext(alertCtx, "flush alert publish failed",
            "date", date.Format("2006-01-02"), "alert_error", err)
        if f.alertFailureFn != nil {
            f.alertFailureFn()
        }
    }
}
```

Call site inside `flushDate` on error path (after manifest row):
```go
f.writeManifestRow(ctx, date, rowCount, b2Path, false, err.Error(), elapsed)
f.publishFlushAlert(date, b2Path, err)
if f.failureFn != nil {
    f.failureFn()
}
```

### Why fresh context for alert

The upload context (4h timeout) is either expired or cancelled when the failure path runs. Any XADD using that context would fail immediately. Always use `context.Background()` with a short timeout for the alert publish.

### Manifest row and alert — two-phase failure

Two failure scenarios to reason about:
1. **Upload fails, manifest write succeeds, alert succeeds**: operator sees alert + manifest entry. ✅
2. **Upload fails, manifest write fails, alert succeeds**: operator sees alert but no manifest entry — audit gap, but operator is informed. ✅
3. **Upload fails, manifest write succeeds, alert fails**: operator has no real-time alert but manifest entry exists for next audit. Counter incremented. ✅
4. **Upload fails, manifest write fails, alert fails**: no real-time signal, counter incremented. ✅

In all four cases, `candle_flush_failure_total` is incremented. Scenario 4 is the worst case (Redis and QuestDB both down). The Prometheus counter is the last line of defense.

### L1 test structure for call ordering

To test manifest-before-alert ordering without real Redis:
```go
var calls []string
manifestFn := func() { calls = append(calls, "manifest") }
alertFn    := func() { calls = append(calls, "alert") }
// inject into Flusher via test-only hooks or function fields
// assert calls[0] == "manifest" && calls[1] == "alert"
```

Use function fields on `Flusher` (already present via callbacks) or extract `flushDate` into an injectable sequence. The exact mechanism is the developer's choice — the test must confirm order.

### counters already in metrics package

`internal/metrics/metrics.go` already defines and registers all three flush counters:
- `FlushSuccessTotal prometheus.Counter` (name: `candle_flush_success_total`)
- `FlushFailureTotal prometheus.Counter` (name: `candle_flush_failure_total`)
- `FlushAlertFailureTotal prometheus.Counter` (name: `candle_flush_alert_failure_total`)

Wire in `main.go`:
```go
flushCfg := flusher.Config{ /* ... */ }
f := flusher.New(flushCfg, rdb, cfg.QuestDBHTTPAddr, logger, wallClock{},
    m.FlushSuccessTotal.Inc,
    m.FlushFailureTotal.Inc,
    m.FlushAlertFailureTotal.Inc,
)
```

---

## Tasks / Subtasks

- [x] Update `flusher.New` signature with `successFn`, `failureFn`, `alertFailureFn` callbacks (AC 5)
- [x] Implement `publishFlushAlert` in `internal/flusher/flusher.go` (AC 1–2)
- [x] Increment counters at correct call sites in `flushDate` (AC 3–4)
- [x] Wire callbacks in `cmd/candle/main.go` (AC 5)
- [x] Add L1 tests: `TestAlertFieldsOnFailure`, `TestManifestBeforeAlert` (AC 6)
- [x] `go build ./...` and `make test-l1` pass (AC 7)

### Review Findings

3 reviewers (Blind Hunter, Edge Case Hunter, Acceptance Auditor). 3 patches applied:
- UTF-8 rune-safe truncation: `[]rune(msg)[:512]` instead of `msg[:512]` in `buildAlertValues`
- `ts` assertion upgraded to exact equality: `assert.Equal(t, "1746489600000", ts)` (AC 6 violation)
- Type assertion ok-flag added for `error_msg` and `ts` fields in test

Skipped (beyond spec scope): panic recovery in `runFailureSequence`; `TestManifestBeforeAlert` `flushDate` integration (L1 by design).

---

## Dev Agent Record

### Completion Notes

All 7 ACs satisfied. Tasks 1–4 (New signature, publishFlushAlert, counter wiring, main.go) were pre-implemented in story 9-2. Story 9-3 contributed:
- Extracted `buildAlertValues(date, b2Path, err, nowMs) map[string]any` — pure function for alert field construction
- Extracted `runFailureSequence(manifestFn, alertFn, failureFn func())` — pure function encoding the manifest-before-alert ordering contract (AC 4)
- Refactored all three `flushDate` error paths to use `runFailureSequence`
- Added `TestAlertFieldsOnFailure` and `TestManifestBeforeAlert` L1 tests; both pass without real Redis or QuestDB

### File List

- `candle-service/internal/flusher/flusher.go` — `buildAlertValues`, `runFailureSequence` added; `publishFlushAlert` refactored; `flushDate` error paths refactored
- `candle-service/internal/flusher/flusher_test.go` — `TestAlertFieldsOnFailure`, `TestManifestBeforeAlert` added; `errors` import added

### Change Log

- 2026-05-09: Extracted `buildAlertValues` and `runFailureSequence` pure functions; refactored `flushDate` error paths; added 2 L1 tests for alert fields and ordering
- 2026-05-09: Code review patches — rune-safe truncation in `buildAlertValues`; exact `ts` assertion; ok-flag guards in test
