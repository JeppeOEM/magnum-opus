# Story 2.3: KuCoin Token Auto-Renewal

**Status:** review
**Epic:** 2 — Exchange Feed Connectivity
**Story ID:** 2.3
**Story Key:** `2-3-kucoin-token-auto-renewal`

---

## Story

As the service,
I want the KuCoin WebSocket token to renew automatically before the 24-hour expiry window,
so that the feed never silently drops due to token expiry during sustained operation.

---

## Acceptance Criteria

1. **Given** a KuCoin token was obtained at startup
   **When** 23.5 hours have elapsed (tested via injected `Clock` advancing with `MockClock`)
   **Then** a fresh token is fetched from the KuCoin REST API before the current token expires

2. **Given** the token renewal request fails
   **When** all retries via `internal/backoff/` are exhausted (`renewalMaxAttempts`)
   **Then** the adapter closes the existing connection and re-authenticates from scratch (new token fetch → new WebSocket connection via `fireTrigger()`)
   **And** no gap marker is emitted if the subsequent snapshot/delta merge succeeds

3. **Given** the renewal goroutine
   **Then** it uses the injected `Clock` interface — never `time.Now()` directly
   **And** it has `case <-ctx.Done(): return` so it exits cleanly on SIGTERM

4. **Given** token renewal is in progress (new token fetched, not yet applied)
   **When** a WebSocket read or write occurs concurrently on the existing connection
   **Then** the existing token remains valid until the new token is applied — the renewal is atomic
   **And** no goroutine reads a partially-updated token value (`tokMu sync.RWMutex` guards all reads and writes)

---

## Tasks / Subtasks

- [x] Fix `fetchToken` double `clock.Now()` call (AC: 3)
  - [x] Capture `now := clock.Now()` once at top of `fetchToken`; use `now` for both HMAC timestamp and `fetchedAt` field — eliminates the deferred clock-drift bug noted in 2.2 review

- [x] Add renewal fields to `Adapter` struct (AC: 1, 2)
  - [x] Add `renewalCheckInterval time.Duration` — polling cadence of the renewal loop (default `1 * time.Minute`; set to `1 * time.Millisecond` in tests)
  - [x] Add `renewalLeadTime time.Duration` — how far before expiry to renew (default `30 * time.Minute`, yielding renewal at 23.5h)
  - [x] Add `renewalMaxAttempts int` — retry cap before firing reconnect trigger (default `5`)
  - [x] Update `New()` to initialise all three fields with the defaults above
  - [x] Update the `wg` comment in the struct from `// tracks runLoop only` to `// tracks runLoop and tokenRenewalLoop`

- [x] Implement `tokenRenewalLoop` (AC: 1, 2, 3, 4)
  - [x] Add `tokenRenewalLoop(ctx context.Context)` to `token.go` as a method on `*Adapter`
  - [x] Poll with `time.NewTicker(a.renewalCheckInterval)`; on each tick check `a.clk.Now().After(a.tok.expiresAt().Add(-a.renewalLeadTime))`; read `tok` under `a.tokMu.RLock()`
  - [x] If renewal is due: attempt `fetchToken` up to `renewalMaxAttempts` times with `backoff.Duration(attempt, a.clk)` sleep between attempts; sleep respects `ctx.Done()`
  - [x] On success: write new token under `a.tokMu.Lock()`
  - [x] On exhaustion: call `a.fireTrigger()` and return from the loop (runLoop handles reconnect from scratch)
  - [x] `select` at top of loop must include `case <-ctx.Done(): return` (AC: 3)
  - [x] Never call `time.Now()` — use `a.clk.Now()` exclusively (AC: 3)

- [x] Start renewal goroutine in `Connect()` (AC: 1, 3)
  - [x] In `Connect()`, after the existing `a.wg.Add(1) / go a.runLoop(...)` block, add `a.wg.Add(1)` and `go func() { defer a.wg.Done(); a.tokenRenewalLoop(adapterCtx) }()`
  - [x] `Close()` already cancels `adapterCtx` and calls `a.wg.Wait()` — no change needed

- [x] Write L3 tests for token renewal (AC: 1, 2, 3, 4)
  - [x] Add tests to `kucoin_test.go` (already `//go:build l3`, `package kucoin`)
  - [x] **`TestTokenRenewal_HappyPath`**: set `renewalCheckInterval = 1ms`, `renewalLeadTime = 30min`; mock server serves two successful token responses; advance MockClock by 23.5h; use `require.Eventually` polling `a.tokMu.RLock() / a.tok.fetchedAt` to confirm a new token was stored
  - [x] **`TestTokenRenewal_RetryThenSucceed`**: configure mock server to fail 1 time then succeed; advance clock past renewal threshold; verify `a.tok` updated after retry
  - [x] **`TestTokenRenewal_ExhaustRetries`**: configure mock server to always fail; `renewalMaxAttempts = 1` (no inter-attempt sleep); advance clock past threshold; verify token request count rises above initial+maxAttempts (showing reconnect started)
  - [x] **`TestTokenRenewal_ContextCancel`**: confirm renewal goroutine exits cleanly when ctx is cancelled before threshold — `Close()` returns within 500ms

---

## Dev Notes

### Architecture Constraints (MUST follow)

- **Clock interface** — `Clock` is defined in `kucoin.go` and implemented by `testutil.MockClock`. The renewal loop MUST use `a.clk.Now()`, never `time.Now()`. The `Makefile` has a `check-nodirect-time` grep target that fails on any `time.Now()` or `time.Sleep()` in non-test files.
- **`tokMu sync.RWMutex`** — already declared on `Adapter` (line 85 in `kucoin.go`). Use `RLock` for reads in the renewal check and `Lock` for writes after a successful fetch. `runLoop` already uses it for reconnect token writes.
- **`fireTrigger()`** — already implemented in `kucoin.go`; sends to the buffered `reconnectTrigger chan struct{}` (cap 1, non-blocking). Calling it from `tokenRenewalLoop` on retry exhaustion causes `runLoop` to reconnect from scratch (new `fetchToken` + new `transport.Dial`).
- **`internal/backoff/`** — use `backoff.Duration(attempt, clock)` from `aggregator/internal/backoff`. It returns a `time.Duration`; the caller must sleep using `select { case <-time.After(d): case <-ctx.Done(): return }`. Never call `backoff.Duration` with a nil clock.
- **`a.wg` tracks both goroutines** — after this story `wg` guards `runLoop` + `tokenRenewalLoop`. `Close()` → `a.wg.Wait()` already handles this correctly; just add `a.wg.Add(1)` before launching the renewal goroutine.

### File Locations

- **Production changes:** `kucoin/kucoin.go` (struct fields, `New()`, `Connect()`) and `kucoin/token.go` (`tokenRenewalLoop`, `fetchToken` fix)
- **Test changes:** `kucoin/kucoin_test.go` (4 new test functions, possible mock server extension for multiple token responses)
- **No new files needed** — all changes are additions to existing files

### `fetchToken` double-clock fix

`token.go:62` uses `clock.Now()` for the HMAC timestamp, and `token.go:117` uses `clock.Now()` again for `fetchedAt`. With `MockClock` these are two separate reads that can diverge if the clock is advanced between them. Fix: capture `now := clock.Now()` at line 61 (before the HMAC computation) and replace both call sites with `now`.

### Renewal loop polling approach (why not `time.NewTimer`)

The renewal loop uses a short-interval ticker (`renewalCheckInterval`) and checks `a.clk.Now()` each tick. This makes the loop testable without a real timer: set `renewalCheckInterval = 1ms` and advance `MockClock` by 23.5h; the next tick will observe the threshold crossed and trigger renewal. A `time.NewTimer` keyed off the actual wall clock cannot be advanced by `MockClock`.

### Mock server multi-token support

`mockWSServer.handleTokenRequest` currently returns a single hardcoded token. For Story 2.3 tests, add a `tokenResponses []string` queue field (protected by `mu`): each call to `handleTokenRequest` shifts the first response off the queue; if the queue is empty, return HTTP 500. `newTestAdapter` default behaviour is unchanged (queue has one entry).

### Previous story learnings (from 2.2)

- `newWithAPIBase` is the correct constructor for tests — allows pointing at a local mock server.
- `testutil.NewMockClock(time.Now())` is already wired in `newTestAdapter`; tests can call `clk.Advance(d)` after obtaining the clock via `a.clk.(*testutil.MockClock)`.
- `require.Eventually` with a 2s deadline and 5ms poll interval is the established pattern for async assertions in the L3 suite.
- `t.Cleanup(a.Close)` is already registered in `newTestAdapter` — renewal goroutine will be stopped by the cleanup via `wg.Wait()`.

### References

- Story 2.2 implementation: `kucoin/kucoin.go`, `kucoin/token.go`, `kucoin/kucoin_test.go`
- `internal/backoff/backoff.go` — `Duration(attempt int, clock Clock) time.Duration`
- `internal/testutil/clock.go` — `MockClock.Advance(d)`, `MockClock.Now()`
- Epic 2.3 ACs: `_bmad-output/planning-artifacts/epics.md` §Story 2.3 (lines 451–475)
- Deferred item (2.2 review): `deferred-work.md` — "fetchToken calls clock.Now() twice with no atomicity"

---

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- Fixed `fetchToken` double `clock.Now()` call: captured `now := clock.Now()` once, used for both HMAC timestamp and `fetchedAt` (deferred item from 2.2 review).
- Added `renewalCheckInterval` (1min), `renewalLeadTime` (30min), `renewalMaxAttempts` (5) to Adapter struct with defaults in `New()`.
- Implemented `tokenRenewalLoop` in `token.go`: polling ticker, `a.clk.Now()` for expiry check, `backoff.Duration` with `time.After` for inter-attempt sleep, `fireTrigger()` on exhaustion.
- Started renewal goroutine in `Connect()` via `a.wg.Add(1)` + anonymous goroutine; `Close()` already covers it via `a.wg.Wait()`.
- Extended `mockWSServer` with `tokenFails int` / `tokenRequestCount int` for test control; modified `handleToken` to decrement/check `tokenFails`.
- Added 4 L3 tests: HappyPath (MockClock advance triggers renewal), RetryThenSucceed (1 failure then success), ExhaustRetries (always-fail → reconnect), ContextCancel (Close within 500ms).
- All 16 L3 tests pass (12 existing + 4 new); `make test-l1` green; `go vet` clean.
- Known limitation: after a renewal-exhaustion-triggered reconnect, the `tokenRenewalLoop` goroutine has exited. The fresh token from reconnect has a 24h window but no running renewal goroutine. Deferred to a future story.

### File List

- `aggregator/internal/exchange/kucoin/token.go`
- `aggregator/internal/exchange/kucoin/kucoin.go`
- `aggregator/internal/exchange/kucoin/kucoin_test.go`

## Change Log

- 2026-05-06: Implemented Story 2.3 — KuCoin token auto-renewal. Fixed fetchToken clock bug, added tokenRenewalLoop with backoff retries, 4 L3 tests green.
