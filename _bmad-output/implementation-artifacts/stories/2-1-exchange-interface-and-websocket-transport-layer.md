# Story 2.1: Exchange Interface & WebSocket Transport Layer

**Status:** ready-for-dev
**Epic:** 2 — Exchange Feed Connectivity
**Story ID:** 2.1
**Story Key:** `2-1-exchange-interface-and-websocket-transport-layer`

---

## Story

As the service,
I want a shared WebSocket transport layer that handles connection lifecycle, framing, and reconnect triggering,
so that exchange-specific adapters focus on protocol semantics rather than connection management.

---

## Acceptance Criteria

1. **Given** `internal/exchange/exchange.go`
   **Then** it defines the `Exchange` interface only — no implementations
   **And** interface names follow noun/noun+er pattern (`Exchange`, not `IExchange`)
   **And** the interface includes methods: `Name()`, `Connect()`, `Subscribe()`, `Ticks()`, `Signals()`, `Close()`

2. **Given** `internal/exchange/transport/conn.go`
   **Then** it uses `nhooyr.io/websocket` exclusively — `gorilla/websocket` must not appear anywhere
   **And** read/write use `wsjson.Read(ctx, conn, &v)` / `wsjson.Write(ctx, conn, v)`
   **And** graceful teardown cancels context first, then calls `conn.Close(StatusNormalClosure, "")`

3. **Given** a WebSocket connection that has been silently dropped
   **When** the transport's keepalive detects no pong within the timeout window
   **Then** a reconnect event is triggered within 5 seconds (NFR6)
   **And** the reconnect event propagates via a buffered channel (minimum buffer size 1) — **not unbuffered**
   **And** if the buffered channel is full (pending reconnect already queued), the send is skipped — not blocked

4. **Given** `ctx` is cancelled
   **When** the transport goroutine receives `ctx.Done()`
   **Then** it exits cleanly without blocking
   **And** every `for-select` loop has `case <-ctx.Done(): return` as a peer case

---

## Tasks / Subtasks

- [ ] Commit current working-tree changes as clean baseline (AC: all)
  - [ ] Run `git add aggregator/` and commit the Clock injection fix (kucoin.go, token.go), exchange.go comment, go.mod comment
  - [ ] Verify `git status` is clean in `aggregator/` before proceeding

- [ ] Verify Exchange interface (AC: 1)
  - [ ] Read `aggregator/internal/exchange/exchange.go` and confirm: interface-only file, no concrete types
  - [ ] Confirm method set: `Name() string`, `Connect(ctx) error`, `Subscribe(symbols []string, feeds []FeedType) error`, `Ticks() <-chan Tick`, `Signals() <-chan Signal`, `Close() error`
  - [ ] Confirm no `I`-prefix naming anywhere in the file
  - [ ] Add compile-time assertion `var _ exchange.Exchange = (*Adapter)(nil)` to `aggregator/internal/exchange/kucoin/kucoin.go` (in the var block at top of file, not in a test)

- [ ] Verify transport implementation (AC: 2)
  - [ ] Read `aggregator/internal/exchange/transport/conn.go` and confirm nhooyr.io/websocket exclusively
  - [ ] Confirm `wsjson.Read` and `wsjson.Write` are used (not direct websocket.Read/Write)
  - [ ] Confirm `Close()` cancels ctx before calling `conn.Close(StatusNormalClosure, "")` — not the reverse
  - [ ] Confirm `reconnectCh` is `make(chan struct{}, 1)` — buffered size 1

- [ ] Verify keepalive reconnect behavior (AC: 3)
  - [ ] Read `keepalive()` function — confirm non-blocking send pattern:
    ```go
    select {
    case c.reconnectCh <- struct{}{}:
    default:
    }
    ```
  - [ ] Confirm `keepalive()` returns after firing reconnect (doesn't loop back and fire repeatedly)

- [ ] Verify ctx propagation (AC: 4)
  - [ ] Confirm `for-select` in `keepalive()` has `case <-ctx.Done(): return` as a peer `case`, not nested inside another `case`

- [ ] Verify `make test-l1` grep scope (AC: all — enforcement)
  - [ ] Read `aggregator/Makefile` grep section — confirm it runs on `./internal/` (which includes exchange/)
  - [ ] Run `cd aggregator && make test-l1` — must pass (Clock injection fix resolves any time.Now() violations)

- [ ] Activate and verify ATDD test scaffolds (AC: 1–4)
  - [ ] For each test in `aggregator/internal/exchange/transport/conn_test.go`:
    - Remove `t.Skip()`, run the test, verify it passes, re-add no t.Skip()
  - [ ] For each test in `aggregator/internal/exchange/exchange_compile_test.go`:
    - Remove `t.Skip()`, run `go test ./internal/exchange/...`, verify it passes

---

## Dev Notes

### Current Implementation State

**This is a verification and hardening pass — both files already exist and are implemented.**

| File | State | Action |
|---|---|---|
| `aggregator/internal/exchange/exchange.go` | Implemented ✓ | Verify interface shape; add compile assertion in kucoin |
| `aggregator/internal/exchange/transport/conn.go` | Implemented ✓ | Verify AC2–4; activate L3 tests |
| `aggregator/internal/exchange/kucoin/kucoin.go` | Partially implemented; clock injection uncommitted | Commit clock fix; add compile assertion |
| `aggregator/internal/exchange/kucoin/token.go` | Clock injection uncommitted | Commit with kucoin.go |

### Working-Tree Changes to Commit First

Before starting verification, commit these uncommitted changes from the working tree. They are the Clock injection fix that resolves the `make test-l1` grep violation:

**`kucoin.go` changes (uncommitted):**
- Added `Clock` interface definition to the kucoin package
- Added `clk Clock` field to `Adapter` struct
- Updated `New()` to require a `Clock` parameter (panics if nil)
- Updated `newWithAPIBase()` to accept and pass through `Clock`
- Updated `handleMarketData()` to use `a.clk.Now().UnixNano()` instead of `time.Now().UnixNano()`
- Aligned `TsLocal` to use the same `tsLocal` var for both L2 and trade events

**`token.go` changes (uncommitted):**
- `fetchToken()` now takes a `Clock` parameter
- `fetchToken()` uses `clock.Now().UnixMilli()` for HMAC timestamp instead of `time.Now()`
- `fetchToken()` uses `clock.Now()` for `tokenData.fetchedAt` instead of `time.Now()`
- `wsURL()` uses `nextMsgID()` for the connectId instead of `time.Now().UnixNano()` (cleaner; avoids the time.Now() issue and uses the existing monotonic counter)

**`exchange.go` change (uncommitted):**
- Comment on `TsLocal` field: "captured by the exchange adapter" (clarifies it's clock-injected, not raw `time.Now()`)

**`go.mod` change (uncommitted):**
- Minor comment consolidation (no functional change)

**Commit message suggestion:**
```
inject Clock into kucoin adapter to satisfy time.Now() ban

Replaces all time.Now() calls in exchange/kucoin/ with the injected
Clock interface. The composition root supplies a real wall-clock;
tests supply testutil.MockClock. make test-l1 grep was already
scoped to ./internal/ — this fix makes it pass for exchange/.
```

### Compile-Time Interface Assertion

Add this to the package-level var block in `aggregator/internal/exchange/kucoin/kucoin.go` (not in a test file — it must gate production builds):

```go
// Compile-time proof that Adapter satisfies the Exchange interface.
var _ exchange.Exchange = (*Adapter)(nil)
```

Place it after the `import` block and before the `const` block, or in its own `var ()` block. This causes `go build` to fail immediately if the Adapter stops satisfying the interface (method renamed, signature changed, etc.).

### Critical: `transport/conn.go` Teardown Order

The teardown order in `Close()` is **security-critical for goroutine lifecycle**:

```go
// CORRECT — context cancelled first so keepalive exits before we close the conn
func (c *Conn) Close() {
    c.cancel()       // 1. cancel keepalive ctx → keepalive goroutine exits
    c.wg.Wait()      // 2. wait for keepalive to fully exit
    c.conn.Close(websocket.StatusNormalClosure, "")  // 3. only then close underlying conn
}
```

Reversing this order causes a race: `conn.Close()` would race with the keepalive still running, which can produce panics on `c.conn.Ping()` after close.

### Critical: Non-Blocking Reconnect Send Pattern

The `default` branch in the reconnect send is **not optional**. Without it, the keepalive goroutine would block indefinitely if the adapter's `runLoop` is busy:

```go
// CORRECT
select {
case c.reconnectCh <- struct{}{}:
default:
    // channel full — a reconnect is already pending; this one is redundant
}
return  // keepalive exits after signaling; adapter handles the reconnect
```

If you see a blocking send (`c.reconnectCh <- struct{}{}`), it is a bug — it would deadlock when the channel is already full.

### Clock Interface Placement (Exchange Package Convention)

Each exchange package defines its own `Clock` interface — structural typing means all implementations satisfy all of them without imports:

```go
// In kucoin/kucoin.go — exchange-local, owned by the consumer
type Clock interface {
    Now() time.Time
}
```

Do **not** import `testutil.MockClock` into the production package. The composition root (`cmd/aggregator/main.go`) passes a real wall-clock:

```go
type wallClock struct{}
func (wallClock) Now() time.Time { return time.Now() }
```

This is the **only** place `time.Now()` is permitted outside `cmd/aggregator/`.

### `make test-l1` Grep Scope — Already Covers Exchange

The existing Makefile grep runs on `./internal/` which covers `exchange/`:

```makefile
@! grep -rn --include="*.go" \
    -e 'time\.Now()' \
    -e 'time\.Sleep(' \
    ./internal/ \
    | grep -v "_test.go" \
    | grep -v ':[[:space:]]*//' \
    | grep -v '/tools/'
```

**No Makefile change is needed for story 2.1.** The grep was already scoped correctly; the Clock injection fix makes exchange/ compliant with it.

### L3 Test Infrastructure (transport/conn_test.go)

The ATDD scaffold uses `net/http/httptest` + `nhooyr.io/websocket` for in-process test servers. Key patterns:

- `echoServer(t)` — accepts WS upgrade, echoes all messages, auto-closes when test ends
- `silentServer(t)` — accepts WS upgrade, never sends pong (simulates silent drop for NFR6)
- All tests use `t.Cleanup(srv.Close)` — no manual cleanup needed
- Use short `PingInterval` + `PongTimeout` in tests (500ms + 500ms) to trigger reconnect quickly without sleeping 5 real seconds

**wsURL helper** — httptest uses `http://`, WebSocket needs `ws://`:

```go
func wsURL(serverURL string) string {
    return "ws" + serverURL[len("http"):]
}
```

### ATDD Artifacts

- **Checklist:** `_bmad-output/test-artifacts/atdd-checklist-2-1-exchange-interface-and-websocket-transport-layer.md`
- **L3 tests:** `aggregator/internal/exchange/transport/conn_test.go` (6 tests, all initially skipped)
- **L1 compile tests:** `aggregator/internal/exchange/exchange_compile_test.go` (3 tests, all initially skipped)

Activate tests by removing `t.Skip()` one at a time per task.

### Forbidden Patterns (Project-Wide Rules)

- `time.Now()` outside `cmd/aggregator/` — use `Clock` interface
- `time.Sleep()` anywhere in `internal/` — use `Clock` or test timeouts
- `gorilla/websocket` — banned, use `nhooyr.io/websocket`
- `var _ exchange.Exchange = ...` in a test file — put in production file for build-time gating
- Unbuffered reconnect channel — must be `make(chan struct{}, 1)` at minimum

### References

- `aggregator/internal/exchange/exchange.go` — Exchange interface definition (read this first)
- `aggregator/internal/exchange/transport/conn.go` — Conn implementation (verify AC2–4)
- `aggregator/internal/exchange/kucoin/kucoin.go` — Clock injection (commit this first)
- `aggregator/internal/exchange/kucoin/token.go` — Clock injection in token fetch
- `aggregator/Makefile` — `test-l1` grep scope, `test-l3` target
- `_bmad-output/project-context.md` — Technology Stack & Versions (non-negotiable library rules)
- `_bmad-output/planning-artifacts/epics.md#Story 2.1` — Full AC text
- `_bmad-output/planning-artifacts/architecture.md#exchange/transport/ boundary` — transport vs reconnect boundary decision

---

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
