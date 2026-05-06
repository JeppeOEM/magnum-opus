# Story 2.4: Bybit Connection Multiplexer

**Status:** review
**Epic:** 2 — Exchange Feed Connectivity
**Story ID:** 2.4
**Story Key:** `2-4-bybit-connection-multiplexer`

---

## Story

As the service,
I want a Bybit connection multiplexer that distributes up to 200 symbols across ~20 concurrent WebSocket connections,
so that Bybit's 10-topics-per-connection limit is respected without violating exchange TOS.

---

## Acceptance Criteria

1. **Given** 200 Bybit symbols are configured
   **When** the multiplexer initializes
   **Then** it creates `ceil(200/10) = 20` WebSocket connections
   **And** each connection carries exactly ≤10 symbol subscriptions

2. **Given** the multiplexer is running with 20 connections
   **When** one connection is dropped (its `Conn.Reconnect()` channel fires)
   **Then** only the symbols assigned to that connection re-subscribe
   **And** symbols on the other 19 connections are unaffected — no duplicate subscriptions produced

3. **Given** subscription confirmation is expected per topic
   **When** the multiplexer sends subscriptions
   **Then** it waits for per-topic confirmation before marking that symbol active
   **And** an unconfirmed subscription after 30 seconds is logged at ERROR and its subscription retried

4. **Given** two or more connections drop simultaneously
   **When** the mux handles concurrent failure
   **Then** all affected symbols receive `NeedsSnapshot` signals — no symbol is silently dropped from the routing table
   **And** symbols on surviving connections are completely unaffected — no duplicate subscriptions, no missing symbols
   **And** this concurrent-failure scenario is an explicit L2 test case

5. **Given** `mux.go`
   **Then** it is a pure routing concern — transport connections are provided by an injected `ConnFactory`, never dialed internally
   **And** routing logic is covered by L2 tests using injected fake connections (no live connections required)

---

## Tasks / Subtasks

- [x] Create package directory and `Conn` interface (AC: 5)
  - [x] Create `aggregator/internal/exchange/bybit/mux/` directory
  - [x] In `mux.go`, declare `package mux` and define the `Conn` interface consumed by the mux:
    ```go
    type Conn interface {
        Read(ctx context.Context, v any) error
        Write(ctx context.Context, v any) error
        Reconnect() <-chan struct{}
        Close()
    }
    ```
    This interface is defined here (consuming package), not in `transport/`. `*transport.Conn` satisfies it via structural typing.
  - [x] Define `ConnFactory` type: `type ConnFactory func(ctx context.Context) (Conn, error)` — the factory is called once per slot on connect and again on each reconnect; the URL is captured in the closure by the caller (Story 2.5)

- [x] Implement `Mux` struct and `New()` constructor (AC: 1, 5)
  - [x] Define `Mux` struct:
    - `maxTopics int` — max symbols per connection (default 10; settable for tests)
    - `factory ConnFactory` — injected connection factory
    - `syms []string` — raw Bybit symbols (e.g. `"BTCUSDT"`)
    - `feeds []exchange.FeedType` — feed types to subscribe (e.g. `FeedTypeOrderBook`)
    - `slots []*slot` — one per connection; populated in `Connect()`
    - `ticks chan exchange.Tick` — output, buffered 4096
    - `signals chan exchange.Signal` — output, buffered 256
    - `cancel context.CancelFunc`
    - `wg sync.WaitGroup` — covers all slot goroutines
    - `confirmTimeout time.Duration` — default 30s; override in tests
  - [x] `New(syms []string, feeds []exchange.FeedType, factory ConnFactory) *Mux` — initialises with defaults; does NOT dial yet
  - [x] `Ticks() <-chan exchange.Tick` and `Signals() <-chan exchange.Signal` accessors
  - [x] Define unexported `slot` struct:
    - `idx int` — slot index
    - `syms []string` — symbols owned by this slot (immutable after assignment)
    - `conn Conn` — current connection (replaced on reconnect); protected by `connMu sync.Mutex`
    - `mu sync.Mutex` — guards `confirmed` and `pendingAcks`
    - `confirmed map[string]bool` — raw symbol → confirmed for current connection
    - `pendingAcks map[string]string` — msgID → raw symbol (one symbol per Bybit subscribe call)
    - `confirmTimeout time.Duration` — copied from Mux at slot creation

- [x] Implement `Connect()` — partition symbols and dial all slots (AC: 1, 5)
  - [x] Partition `syms` into groups of `maxTopics` using `ceil(len/maxTopics)` math: `nSlots := (len(syms) + maxTopics - 1) / maxTopics`; last group may have fewer
  - [x] For each slot: call `factory(ctx)` to obtain `Conn`; store in slot; if any factory call fails, close already-opened connections and return error
  - [x] Create `adapterCtx, cancel := context.WithCancel(ctx)`; store `cancel` in mux
  - [x] For each slot: `wg.Add(1)`, launch `go m.runSlot(adapterCtx, s)` (covers readLoop + reconnect loop)
  - [x] Set `wg.Add(1)`, launch one goroutine per slot for `confirmWatcher(adapterCtx, s)` — **separate goroutine** so it survives across reconnects cleanly (alternative: include in `runSlot`)
  - [x] After all slots started, call `m.sendSubscriptions(adapterCtx)` to subscribe all slots
  - [x] Return nil on success

- [x] Implement per-slot goroutine: `runSlot` (AC: 2, 4)
  - [x] `runSlot(ctx context.Context, s *slot)` — this is the only goroutine that manages reconnect for a slot
  - [x] Inner loop: start a per-connection context (`connCtx, connCancel := context.WithCancel(ctx)`); launch `go m.readLoop(connCtx, s)` under a separate inner `sync.WaitGroup`; then `select`:
    - `case <-ctx.Done(): connCancel(); inner wg.Wait(); s.conn.Close(); return`
    - `case <-s.conn.Reconnect(): // connection dropped`
  - [x] On drop: `connCancel()`, wait for inner WaitGroup, close old conn, emit NeedsSnapshot for all confirmed symbols on this slot, reset confirmed/pendingAcks, call `factory(ctx)` in a backoff loop until success or ctx cancelled; on success: store new conn, reset slot ack state, call `m.sendSlotSubscriptions(ctx, s)` to re-subscribe only this slot's symbols

- [x] Implement `readLoop` per connection (AC: 2, 3)
  - [x] Reads raw JSON messages from `s.conn.Read(ctx, &msg)` until ctx cancelled or read error
  - [x] Dispatches on message type:
    - `"subscribe"` op with `success=true` → call `m.handleAck(s, msg)` to mark symbol confirmed
    - `"subscribe"` op with `success=false` → log WARN; symbol stays in pendingAcks
    - Any other message type (market data) → **ignored in Story 2.4**; Story 2.5 wires in the tick dispatch callback

- [x] Implement subscription send and ACK handling (AC: 3)
  - [x] `sendSlotSubscriptions(ctx, s)` — for each symbol in `s.syms`, for each feed type, send one Bybit subscribe message per symbol (NOT batched — Bybit acks are per-op, not per-symbol-batch):
    ```json
    {"op": "subscribe", "req_id": "<msgID>", "args": ["orderbook.1.BTCUSDT"]}
    ```
    Topic format: `"orderbook.1.SYMBOL"` for `FeedTypeOrderBook`, `"publicTrade.SYMBOL"` for `FeedTypeTrade`
    Store `s.pendingAcks[msgID] = rawSym` under `s.mu`
  - [x] `handleAck(s *slot, reqID string)` — look up `reqID` in `s.pendingAcks`; if found, mark `s.confirmed[sym] = true`, delete from pendingAcks; if not found, log WARN spurious ack
  - [x] `resetSlot(s)` — clears `s.confirmed` and `s.pendingAcks`; called before re-subscribing on reconnect

- [x] Implement `confirmWatcher` per slot (AC: 3)
  - [x] After `confirmTimeout` (default 30s), check each slot for unconfirmed symbols (those still in `pendingAcks`)
  - [x] Log ERROR with count and sample; retry subscription for unconfirmed symbols via `sendSlotSubscriptions` with only the unconfirmed subset
  - [x] Reset timer after retry; loop back to wait again (watcher runs for the lifetime of the mux context)

- [x] Implement `emitNeedsSnapshot` for a slot (AC: 2, 4)
  - [x] Called on slot drop: iterates `s.confirmed`, sends `exchange.Signal{Type: SignalNeedsSnapshot, Symbol: symbol.Normalize("bybit", raw), Reason: "disconnect"}` for every confirmed symbol
  - [x] Non-blocking send (drop with WARN if signals channel full)

- [x] Implement `Close()` (AC: 2, 5)
  - [x] Call `m.cancel()`, then `m.wg.Wait()`; each `runSlot` is responsible for closing its own conn on ctx cancel
  - [x] After `wg.Wait()`, close `m.ticks` and `m.signals`

- [x] Write L2 tests in `mux_test.go` (AC: 1, 2, 3, 4, 5)
  - [x] `//go:build l2` at top; `package mux` (white-box — needed for `confirmTimeout` override and slot access)
  - [x] Define `fakeConn` struct: channels for `readCh chan any`, `writeCh chan any`, `reconnectCh chan struct{}`; implement `Conn` interface; `Drop()` method closes `reconnectCh`; `Push(msg any)` pushes a message onto `readCh` for readLoop to consume
  - [x] Define `fakeFactory(conns []*fakeConn) ConnFactory` — returns each conn in order; panics if called more than `len(conns)` times (factory call count assertion)
  - [x] **`TestMux_SymbolPartitioning`**: create mux with 200 symbols, `maxTopics=10`; call `Connect()`; verify 20 factory calls; verify each fakeConn received exactly ≤10 `"subscribe"` writes; verify no symbol appears on more than one conn
  - [x] **`TestMux_SingleConnectionDrop`**: 30 symbols (3 slots); ack all slot-1 syms; drop slot-1 conn; verify slot-1 syms get `SignalNeedsSnapshot`; verify slot-1 re-subscribes exactly its 10 syms
  - [x] **`TestMux_ConcurrentDrop`**: 30 symbols (3 slots); ack all; drop slot-0 and slot-2 simultaneously; verify slot-0 and slot-2 syms each get `SignalNeedsSnapshot`; no duplicate signals
  - [x] **`TestMux_UnconfirmedTimeout`**: set `confirmTimeout=50ms`; subscribe 1 symbol; do NOT send ack; wait 100ms; verify symbol still in `s.pendingAcks` (watcher retried)
  - [x] **`TestMux_SpuriousAck`**: send ack for unknown reqID; verify no panic, no symbol confirmed
  - [x] **`TestMux_Close`**: connect, close within 500ms; verify `Ticks()` channel closes

---

## Dev Notes

### File Locations (NEW — nothing to UPDATE except Makefile awareness)

- **New:** `aggregator/internal/exchange/bybit/mux/mux.go` — package `mux`
- **New:** `aggregator/internal/exchange/bybit/mux/mux_test.go` — `//go:build l2`, `package mux`
- **No changes** to `exchange/transport/conn.go`, `kucoin/`, or any existing file

### Conn Interface (Consumer Owns It)

The mux defines its own `Conn` interface — `*transport.Conn` satisfies it structurally:
```go
// transport.Conn already has: Read, Write, Reconnect(), Close()
// mux.Conn interface matches exactly — no import of transport/ needed
```
Do NOT import `exchange/transport` in `mux.go` — that would violate the interface-in-consumer-package pattern and create a dependency that makes L2 testing harder.

### Bybit Wire Protocol (subscribe/ack)

Subscribe request (one per symbol per feed type):
```json
{"op": "subscribe", "req_id": "42", "args": ["orderbook.1.BTCUSDT"]}
```

Success ack:
```json
{"success": true, "ret_msg": "subscribe", "op": "subscribe", "req_id": "42", "conn_id": "abc"}
```

Failure ack:
```json
{"success": false, "ret_msg": "error", "op": "subscribe", "req_id": "42"}
```

Topic prefix mapping:
- `FeedTypeOrderBook` → `"orderbook.1."` + rawSym
- `FeedTypeTrade` → `"publicTrade."` + rawSym

Wire message struct (for `Read()` deserialization):
```go
type wireMsg struct {
    Op     string `json:"op"`
    ReqID  string `json:"req_id"`
    Success bool  `json:"success"`
    // Data json.RawMessage for market data — ignored in this story
}
```

### Symbol Normalization

Bybit raw symbols are already uppercase-no-separator (`"BTCUSDT"`). `symbol.Normalize("bybit", "BTCUSDT")` is called when emitting Ticks and Signals, NOT when storing in the routing table (which uses raw symbols for matching against subscription acks).

### `msgID` generation

Use the same `nextMsgID()` pattern as kucoin (package-level `atomic.Int64`). Since this is a different package, define a new `msgIDCounter` in `mux.go`.

### Reconnect backoff

Use `backoff.Duration(attempt, clock)` from `internal/backoff/`. The mux does not inject a Clock (it only uses backoff for reconnect sleep, not time.Now for expiry checks). For the sleep: `time.After(backoff.Duration(attempt, realClock{}))` where `realClock{}` is a zero-value type implementing `Now() time.Time` via `time.Now()`. **Exception to time.Now() ban**: the mux does not need deterministic clock injection for reconnect backoff since the backoff duration itself is not tested. Use `type wallClock struct{}; func (wallClock) Now() time.Time { return time.Now() }` defined locally in mux.go — the Makefile's `check-nodirect-time` grep only bans calls to `time.Now()` in non-test files, but the architecture doc says to inject Clock for testability. Given that reconnect backoff is not in the ACs being tested, a local wallClock struct is acceptable. **Alternative**: skip backoff in the initial implementation and use a fixed 1s sleep via `time.After(time.Second)` — simpler and avoids the Clock question.

### Why L2 (not L3)

The AC explicitly states "covered by L2 tests using injected fakes (no live connections required)". L2 tests use `//go:build l2` and mock interfaces — no real WS connections. The `fakeConn` struct replaces `*transport.Conn` entirely, allowing precise control of reconnect signals and message injection.

### What Story 2.5 adds

Story 2.5 (Bybit WebSocket Feed Adapter) will:
- Wire the mux with real `transport.Dial()` calls (the factory becomes `func(ctx) (Conn, error) { return transport.Dial(ctx, bybitURL, opts) }`)
- Add the `readLoop` dispatch path for market data (currently ignored in Story 2.4)
- Add Bybit-specific ping/pong handling (`{"op":"ping"}` every ≤10s)
- Add `parser.go` for L2/trade message parsing

The mux readLoop should call a `dispatchMsg(s *slot, msg wireMsg)` helper that handles acks locally and passes market data to a `m.onMsg` callback (initially a no-op `func(wireMsg)`). Story 2.5 replaces the no-op with the tick-producing dispatch.

### Previous story patterns (from 2.2/2.3)

- `atomic.Int64` for `msgIDCounter` (same pattern as kucoin's `nextMsgID()`)
- `sync.WaitGroup` + `context.WithCancel` for goroutine lifecycle
- Non-blocking channel sends with `select { case ch <- v: default: slog.Warn(...) }`
- `t.Cleanup(a.Close)` equivalent: `t.Cleanup(func() { m.Close() })`
- `require.Eventually` with 2s deadline and 5ms poll for async assertions

### References

- Epic 2.4 ACs: `_bmad-output/planning-artifacts/epics.md` §Story 2.4 (lines 477–509)
- Architecture directory layout: `_bmad-output/planning-artifacts/architecture.md` lines 461–469
- `exchange/exchange.go` — `Exchange`, `FeedType`, `Tick`, `Signal` types
- `exchange/transport/conn.go` — concrete `*Conn` struct (mux.Conn satisfied structurally)
- `internal/symbol/symbol.go` — `Normalize("bybit", raw)`
- `internal/backoff/backoff.go` — `Duration(attempt, clock)` for reconnect sleep

---

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- Implemented `mux.go`: `Conn` interface (consumer-owned), `ConnFactory`, `slot` struct, `Mux` struct, `Connect()`, `Close()`, `runSlot()`, `readLoop()`, `dispatch()`, `handleAck()`, `confirmWatcher()`, `emitNeedsSnapshot()`, `sendSubscriptions/SlotSubscriptions/SymbolSubscriptions()`, `topicPrefix()`.
- Dropped `backoff.Duration` + `wallClock` in favour of a fixed `time.After(time.Second)` for reconnect sleep — avoids `time.Now()` ban in `./internal/` without adding test complexity (backoff duration is not under test).
- 6 L2 tests cover: symbol partitioning, single drop, concurrent drop, unconfirmed timeout retry, spurious ack safety, and close correctness.
- Key test detail: `ackAll` pushes acks asynchronously into `readCh`; tests use `require.Eventually` on `slot.confirmed` length before triggering `Drop()` to avoid a race between the readLoop and the reconnect signal.

### File List

- `aggregator/internal/exchange/bybit/mux/mux.go` (new)
- `aggregator/internal/exchange/bybit/mux/mux_test.go` (new)
