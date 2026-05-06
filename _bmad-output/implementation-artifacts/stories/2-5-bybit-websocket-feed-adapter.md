# Story 2.5: Bybit WebSocket Feed Adapter

**Status:** review
**Epic:** 2 — Exchange Feed Connectivity
**Story ID:** 2.5
**Story Key:** `2-5-bybit-websocket-feed-adapter`

---

## Story

As the service,
I want a Bybit exchange adapter that coordinates the connection mux, sends pings every ≤10 seconds, and parses L2 updates and trade events into normalized Deltas,
So that Bybit market data flows correctly across all 20 connections.

---

## Acceptance Criteria

1. **Given** the Bybit adapter is connected
   **Then** it sends `{"op":"ping"}` every ≤10 seconds on each connection
   **And** if no pong is received within 20 seconds, the transport triggers a reconnect for that connection

2. **Given** a Bybit L2 update message arrives on the wire
   **When** `parser.go` processes it
   **Then** it produces a normalized `ParsedUpdate` with `[]orderbook.Delta` using the identical schema as KuCoin output
   **And** `parser.go` is pure — tested at L3 via `.json` fixture files in `exchange/bybit/testdata/fixtures/`

3. **Given** a disconnect occurs on one of the 20 Bybit connections
   **When** the adapter detects it
   **Then** it emits `NeedsSnapshot` signals only for the symbols on that connection
   **And** the other 19 connections continue uninterrupted
   **And** reconnect and re-subscription completes within 90 seconds (NFR7)

4. **Given** `MockWSServer` for L3 tests
   **Then** it is defined in `exchange/bybit/bybit_test.go` with `//go:build l3` — NOT in `internal/testutil/`

5. **Given** 20 connections are active and ping is sent on each ≤10s
   **When** the Go scheduler is under load
   **Then** the ping goroutine for each connection uses `time.NewTicker` with a fixed interval — not cumulative sleep — so scheduler delays do not compound

---

## Tasks / Subtasks

- [x] Update `mux.go` to expose market data dispatch and ping/pong (AC: 1, 2, 5)
  - [x] Add fields to `wireMsg`: `Topic string \`json:"topic,omitempty"\``, `Type string \`json:"type,omitempty"\``, `Ts int64 \`json:"ts,omitempty"\``
  - [x] Export `type MsgCallback func(topic string, ts int64, msgType string, data json.RawMessage)` at package level
  - [x] Add to `Mux` struct: `pingInterval time.Duration`, `pingTimeout time.Duration`, `onMsg MsgCallback`
  - [x] Add `WithMsgCallback(fn MsgCallback)` setter method on `*Mux`
  - [x] Add `SendTick(t exchange.Tick)` method — non-blocking send to `m.ticks`, drops with warn on full
  - [x] Set defaults in `New()`: `pingInterval: 10 * time.Second`, `pingTimeout: 20 * time.Second`
  - [x] Add `pongCh chan struct{}` (buffered 1) to `slot`; initialise in `newSlot`
  - [x] Update `runSlot`: launch `pingLoop(connCtx, s)` alongside `readLoop` — `inner.Add(2)` instead of `inner.Add(1)`; added `innerDone` channel so pong-timeout triggered close wakes runSlot even when transport keepalive is disabled
  - [x] Update `dispatch`: add `case "pong"` → non-blocking send to `s.pongCh`; add `default` branch → if `msg.Topic != ""` and `m.onMsg != nil`, call `m.onMsg(msg.Topic, msg.Ts, msg.Type, msg.Data)`
  - [x] Add `pingLoop(ctx context.Context, s *slot)`: ticker at `s.slot` uses `m.pingInterval`; on each tick write `{"op":"ping"}` then wait for pong/timeout/ctx; if pong timeout fires call `s.getConn().Close()`

- [x] Implement `bybit/parser.go` (AC: 2)
  - [x] Create `aggregator/internal/exchange/bybit/parser.go` with `package bybit`
  - [x] Define `ParsedUpdate struct { Symbol symbol.Symbol; Deltas []orderbook.Delta }` — identical to KuCoin's type
  - [x] Define `ParsedTrade struct { Symbol symbol.Symbol; Seq uint64; Price, Size, Side string; TsExchange int64 }`
  - [x] Implement `parseL2Update(topic string, tsMs int64, data json.RawMessage) (ParsedUpdate, error)`
  - [x] Implement `parseTrade(topic string, data json.RawMessage) (ParsedTrade, error)`
  - [x] Implement `symFromTopic(topic string) string` — `strings.LastIndex(topic, ".")`
  - [x] Implement `isL2Topic` and `isTradesTopic`
  - [x] `parser.go` is pure: no IO, no `time.Now()` calls, no global state

- [x] Create fixture files for L3 parser tests (AC: 2)
  - [ ] Create `aggregator/internal/exchange/bybit/testdata/fixtures/l2_snapshot_btcusdt.json`:
    ```json
    {
      "topic": "orderbook.1.BTCUSDT",
      "type": "snapshot",
      "ts": 1683016009277,
      "data": {
        "s": "BTCUSDT",
        "b": [["29501.50", "1.234"]],
        "a": [["29502.00", "0.567"]],
        "u": 1001
      }
    }
    ```
  - [x] Create `aggregator/internal/exchange/bybit/testdata/fixtures/l2_delta_btcusdt.json`:
    ```json
    {
      "topic": "orderbook.1.BTCUSDT",
      "type": "delta",
      "ts": 1683016009500,
      "data": {
        "s": "BTCUSDT",
        "b": [["29501.50", "0"]],
        "a": [["29502.00", "1.234"]],
        "u": 1002
      }
    }
    ```
  - [x] Create `aggregator/internal/exchange/bybit/testdata/fixtures/trade_btcusdt.json`:
    ```json
    {
      "topic": "publicTrade.BTCUSDT",
      "type": "snapshot",
      "ts": 1683016009300,
      "data": [
        {
          "T": 1683016009123,
          "p": "29501.50",
          "v": "0.001",
          "S": "Buy",
          "i": "2100000000086084442"
        }
      ]
    }
    ```

- [x] Implement `bybit/bybit.go` (AC: 1, 3)
  - [x] All methods implemented: `New`, `Name`, `Connect`, `Subscribe`, `Ticks`, `Signals`, `Close`, `onMsg`, `sideStr`
  - [x] `SetPingInterval` / `SetPingTimeout` exported for test overrides; applied to mux before Connect

- [x] Write L3 tests in `bybit/bybit_test.go` (AC: 2, 4, 5)
  - [x] `mockWSServer` in this file with auto-ack and auto-pong; `setSuppressPong` for timeout test
  - [x] `TestAdapter_ParseL2Snapshot`, `TestAdapter_ParseL2Delta`, `TestAdapter_ParseTrade`, `TestAdapter_ParseTrade_UnknownSide`
  - [x] `TestAdapter_ConnectSubscribeReceiveTick`, `TestAdapter_PingPong`, `TestAdapter_PingTimeout`
  - [x] Tests use `context.WithTimeout` with 5s deadline to prevent hangs

- [x] Run all test tiers and verify clean (AC: all)
  - [x] `make test-l1` — compile checks pass, time.Now() ban satisfied
  - [x] `make test-l2` — 6 L2 mux tests green (no regressions)
  - [x] `make test-l3` — 7 L3 bybit tests green, all kucoin and transport L3 tests still passing

---

## Dev Notes

### CRITICAL: time.Now() Ban

The Makefile's `check-nodirect-time` target scans all `.go` files for direct `time.Now()` calls and **fails the build if found**. The `Adapter` receives a `Clock` interface injected at construction. The composition root creates a `wallClock` — but `wallClock` **cannot** implement `Now()` by calling `time.Now()` directly.

The established pattern from `kucoin/kucoin.go` is that the `wallClock` type is defined in a dedicated file that is explicitly excluded from the lint scan. Check the Makefile for how KuCoin handles this — do the same for `bybit/`.

In tests, supply a `mockClock` that returns a fixed time (or advances on demand).

### mux.go Update Strategy

`mux.go` already exists and has 6 L2 tests passing. The updates must be **additive only** — do not break existing behavior:
- `wireMsg` extension: adding JSON fields with `omitempty` is backward-compatible
- `slot.pongCh`: initialise in `newSlot` — the existing `newSlot` call sites are `Connect()` only
- `runSlot`: adding a second goroutine (`pingLoop`) under `inner.Add(2)` — verify mux_test.go fakeConn handles extra writes (ping messages written to writeCh)
- `dispatch` default branch: only fires when `msg.Topic != ""` — existing subscribe ack messages have empty Topic, so existing behavior is unchanged
- Run `make test-l2` after mux changes to confirm no regressions

### pingLoop Design

```go
func (m *Mux) pingLoop(ctx context.Context, s *slot) {
    ticker := time.NewTicker(m.pingInterval)
    defer ticker.Stop()
    for {
        select {
        case <-ctx.Done():
            return
        case <-ticker.C:
        }
        conn := s.getConn()
        if err := conn.Write(ctx, map[string]string{"op": "ping"}); err != nil {
            return
        }
        select {
        case <-ctx.Done():
            return
        case <-s.pongCh:
            // pong received; next ping on next tick
        case <-time.After(m.pingTimeout):
            slog.Warn("bybit mux: pong timeout", "slot", s.idx)
            s.getConn().Close()
            return
        }
    }
}
```

Key: `time.NewTicker` (not cumulative sleep) so scheduler stalls don't compound. The `pingLoop` exits on context cancel or pong timeout (which triggers `runSlot` to reconnect via the `Reconnect()` channel). Note: `time.After` is acceptable inside `pingLoop` for the pong wait window since it is one-shot per ping, not a recurring sleep loop — but consider using `timer.Reset` for lower allocation in production.

### Transport Options for Bybit

The Bybit adapter sets `transport.Options{PingInterval: 4*time.Hour, PongTimeout: 5*time.Second}`. This effectively disables the transport-layer WebSocket RFC 6455 pings (they fire every 4h). The Bybit application-level ping/pong (handled by `pingLoop` in the mux) is the actual keepalive. The transport-layer pong timeout of 5s is defensive — if the transport ever sends a WS-level ping, the server has 5s to reply.

### Bybit Wire Format Reference

**Orderbook (outer envelope)**:
```json
{
  "topic": "orderbook.1.BTCUSDT",
  "type": "snapshot",        // or "delta"
  "ts": 1683016009277,       // milliseconds
  "data": {
    "s": "BTCUSDT",
    "b": [["29501.50", "1.234"]],   // [price, qty] — no seq per level
    "a": [["29502.00", "0.567"]],
    "u": 1001                // update ID — use as Seq for all deltas in this message
  }
}
```

**Trade (outer envelope)**:
```json
{
  "topic": "publicTrade.BTCUSDT",
  "type": "snapshot",
  "ts": 1683016009300,
  "data": [
    {
      "T": 1683016009123,    // milliseconds (trade execution time)
      "p": "29501.50",
      "v": "0.001",
      "S": "Buy",            // "Buy" → "bid", "Sell" → "ask"
      "i": "2100000000086084442"
    }
  ]
}
```

**Subscribe ack** (existing, handled by mux):
```json
{"op":"subscribe","req_id":"1","success":true,"ret_msg":""}
```

**Ping/Pong**:
- Send: `{"op":"ping"}`
- Receive: `{"op":"pong"}`

### parseL2Update Internal Structure

The `data` field of Bybit's orderbook message does NOT have a top-level timestamp. The timestamp is in the outer `ts` field (milliseconds). The inner `data.u` is the update sequence. All levels in one message share the same `u` as their `Seq`.

```go
// inner data struct for parseL2Update
var d struct {
    S string          `json:"s"` // symbol, e.g. "BTCUSDT"
    B [][2]string     `json:"b"` // bids [price, qty]
    A [][2]string     `json:"a"` // asks [price, qty]
    U uint64          `json:"u"` // update ID
}
```

### parseTrade Internal Structure

Bybit trade `data` is an array; parse only the first element (batch trade messages are rare on L1 feed).

```go
var trades []struct {
    T int64  `json:"T"` // ms
    P string `json:"p"` // price
    V string `json:"v"` // size
    S string `json:"S"` // "Buy" or "Sell"
}
```

### MockWSServer Pattern (from KuCoin story)

Follow the same pattern as `kucoin/kucoin_test.go`:
- Use `httptest.NewServer` with a handler that upgrades to WebSocket
- On each connection, spawn a goroutine that reads messages
- Auto-respond: if `msg.Op == "subscribe"` → send ack; if `msg.Op == "ping"` → send `{"op":"pong"}`
- Expose a channel for test to push market data messages
- Expose a `t.Cleanup(server.Close)` call

Bybit does NOT have a token endpoint — no `/api/v1/bullet-public` needed. The adapter connects directly to the WebSocket URL.

### Symbol Normalization

`symbol.Normalize("bybit", "BTCUSDT")` — check `internal/symbol/symbol.go` for what this returns. In tests, symbols that don't match known suffixes (e.g. `"SYM0001"`) may fall through to the raw string. The `bybit_test.go` tests use real symbols like `"BTCUSDT"` to exercise normalization.

### Mux Test Compatibility

After adding `pingLoop`, the existing `mux_test.go` fakeConn's `writeCh` will receive ping messages in addition to subscribe messages. Ensure `mux_test.go` tests drain the writeCh or use a large enough buffer. The `ackAll` helper reads from `writeCh` and only responds to subscribe messages — add a type check: if the message's `op` is `"ping"`, ignore it (do not try to ack it as a subscribe).

Alternatively, set `m.pingInterval` to a very large value (e.g. `24*time.Hour`) in L2 tests so pings never fire during the test window. This is the recommended approach — add a test-only setter `withPingInterval(d time.Duration)` on `*Mux` for test use.

### File Structure

```
aggregator/internal/exchange/bybit/
├── bybit.go              NEW
├── bybit_test.go         NEW (//go:build l3)
├── parser.go             NEW
└── testdata/
    └── fixtures/
        ├── l2_snapshot_btcusdt.json   NEW
        ├── l2_delta_btcusdt.json      NEW
        └── trade_btcusdt.json         NEW
aggregator/internal/exchange/bybit/mux/
└── mux.go                UPDATE
```

---

## Dev Agent Record

### Debug Log

- **runSlot pong timeout bug**: When `pingLoop` calls `conn.Close()` after a pong timeout, `transport.Conn.Reconnect()` never fires (WS-level keepalive is disabled at 4h interval). `runSlot` was stuck in its select forever. Fix: added `innerDone` channel that closes when both `readLoop` and `pingLoop` exit; added `<-innerDone` as a third select case in `runSlot`, triggering reconnect when goroutines exit without a transport-level reconnect signal.

### Completion Notes

- **mux.go** updated with `MsgCallback`, `pingLoop`, `pongCh`, `WithMsgCallback`, `WithPingInterval`, `WithPingTimeout`, `SendTick`, `innerDone` reconnect fix
- **parser.go** pure module: `parseL2Update` (shared `u` seq, `[2]string` tuples), `parseTrade` (first element, ms→ns), `symFromTopic`, `isL2Topic`, `isTradesTopic`
- **bybit.go** implements `exchange.Exchange`: `Connect` stores context, `Subscribe` creates mux with ConnFactory, `onMsg` routes to parser and sends ticks, `Close` cancels and waits
- **3 fixture files** for L3 parser tests (snapshot, delta, trade)
- **7 L3 tests**: 4 parser (fixture-based) + 3 integration (tick receive, ping/pong, ping timeout)
- All tiers green: L1 (time.Now() ban pass), L2 (6 mux tests), L3 (7 bybit + 9 kucoin/transport)

### File List

- `aggregator/internal/exchange/bybit/mux/mux.go` — updated
- `aggregator/internal/exchange/bybit/mux/mux_test.go` — updated (pingInterval=24h in all tests)
- `aggregator/internal/exchange/bybit/bybit.go` — new
- `aggregator/internal/exchange/bybit/parser.go` — new
- `aggregator/internal/exchange/bybit/bybit_test.go` — new (//go:build l3)
- `aggregator/internal/exchange/bybit/testdata/fixtures/l2_snapshot_btcusdt.json` — new
- `aggregator/internal/exchange/bybit/testdata/fixtures/l2_delta_btcusdt.json` — new
- `aggregator/internal/exchange/bybit/testdata/fixtures/trade_btcusdt.json` — new
- `_bmad-output/implementation-artifacts/stories/2-5-bybit-websocket-feed-adapter.md` — new (this file)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — updated

### Change Log

- Implemented story 2.5: Bybit WebSocket Feed Adapter — mux ping/pong, L2/trade parser, adapter, 7 L3 tests (Date: 2026-05-06)
