# Story 2.2: KuCoin WebSocket Feed Adapter

**Status:** ready-for-dev
**Epic:** 2 — Exchange Feed Connectivity
**Story ID:** 2.2
**Story Key:** `2-2-kucoin-websocket-feed-adapter`

---

## Story

As the service,
I want a KuCoin exchange adapter that obtains a REST WebSocket token, maintains the connection with heartbeats, and parses L2 updates and trade events into normalized Ticks,
so that KuCoin market data flows correctly into the system from first connection through normal operation.

---

## Acceptance Criteria

1. **Given** the service starts with valid KuCoin credentials
   **When** the KuCoin adapter connects
   **Then** it fetches a WebSocket token from `POST /api/v1/bullet-private` before opening the WebSocket connection
   **And** the connection is established within the token's validity window

2. **Given** the adapter is connected
   **Then** it sends application-level JSON ping/pong at the exchange-required interval from the token response
   **And** if no pong is received within `pingTimeout`, it fires `reconnectTrigger` so `runLoop` reconnects

3. **Given** the adapter subscribes to symbols
   **When** ack messages arrive (one ack per subscription batch, not per-symbol)
   **Then** each symbol in that batch is individually resolved and marked confirmed in `a.confirmed`
   **And** a symbol with no confirmation within 30 seconds is logged at ERROR and its subscription retried
   **And** a spurious ack for an unknown msgID is logged at WARN and discarded — no state is modified

4. **Given** up to 200 symbols are configured for KuCoin
   **When** the adapter subscribes with `FeedTypeOrderBook`
   **Then** all 200 subscriptions are sent in 2 batches of 100 (`subBatchSize = 100`)
   **And** after both acks arrive, all 200 symbols are marked confirmed

5. **Given** a KuCoin L2 update message arrives on the wire
   **When** `parseL2Update` processes it
   **Then** it produces a `ParsedUpdate` with correct `Symbol`, `Deltas` (each with string `Price`, string `Size`, `Seq`, `Side`, `TsExchange`)
   **And** price and size are always string — never float64
   **And** `parseTrade` produces a `ParsedTrade` with "buy" → "bid" and "sell" → "ask" side mapping
   **And** both parsers are tested at L3 using `.json` fixture files in `testdata/fixtures/`

6. **Given** a disconnect occurs (server closes the WebSocket connection)
   **When** the adapter detects it via `heartbeatLoop` pong timeout or write failure
   **Then** `runLoop` emits a `SignalNeedsSnapshot` on `a.signals` for every confirmed symbol

7. **Given** `MockWSServer` for L3 tests
   **Then** it is defined in `kucoin_test.go` with `//go:build l3` — NOT in `internal/testutil/`
   **And** it handles both the KuCoin REST token endpoint AND the WebSocket connection

---

## Tasks / Subtasks

- [ ] Verify existing implementation satisfies all ACs (read-only) (AC: 1–6)
  - [ ] Read `kucoin.go` — confirm Connect() calls fetchToken() before transport.Dial(); confirm runLoop reconnect path; confirm emitNeedsSnapshot() emits for all confirmed symbols
  - [ ] Read `parser.go` — confirm parseL2Update produces string Price/Size; confirm parseTrade maps "buy"→"bid"/"sell"→"ask"; confirm parseLevels handles empty bids/asks without error
  - [ ] Read `token.go` — confirm fetchToken() uses Clock for HMAC timestamp and fetchedAt; confirm wsURL() returns endpoint-only when token is empty (test mode)
  - [ ] Confirm `subBatchSize = 100` and that Subscribe() batches correctly for 200 symbols → 2 messages
  - [ ] Note: `heartbeatLoop` uses `time.After(pingTimeout)` — NOT Clock-injected (this is acceptable; Makefile grep bans `time.Now()` and `time.Sleep()`, NOT `time.After`)

- [ ] Create JSON fixture files (AC: 5)
  - [ ] Create `testdata/fixtures/` directory
  - [ ] Create `testdata/fixtures/l2_update_btc_usdt.json` — raw KuCoin `/market/level2` wire message with both bid and ask deltas
  - [ ] Create `testdata/fixtures/trade_btc_usdt.json` — raw KuCoin `/market/match` wire message with "buy" side
  - [ ] Create `testdata/fixtures/trade_sell_btc_usdt.json` — raw KuCoin `/market/match` wire message with "sell" side (verifies "sell"→"ask" mapping)

- [ ] Write `MockWSServer` in `kucoin_test.go` (AC: 7)
  - [ ] Add `//go:build l3` tag at top; use `package kucoin` (white-box — required for `newWithAPIBase` and `confirmTimeout` access; document this at file top)
  - [ ] `mockWSServer` struct: `srv *httptest.Server`, configurable `pingIntervalMs int`, `pingTimeoutMs int`; channels for controlling server behavior in tests
  - [ ] `handleTokenRequest` (HTTP handler): returns `{"code":"200000","data":{"token":"test-token","instanceServers":[{"endpoint":"<ws-url>","pingInterval":<ms>,"pingTimeout":<ms>}]}}` — endpoint is `"ws"+srv.srv.URL[len("http"):]`
  - [ ] WebSocket handler: upgrades, sends `{"type":"welcome"}`, dispatches incoming messages: subscribe → sends ack; ping → sends pong; has opt-in "drop connection" mode for disconnect tests
  - [ ] Helper `newTestAdapter(t, srv) *Adapter`: calls `newWithAPIBase(config.KuCoinConfig{}, http.DefaultClient, srv.srv.URL, testutil.NewMockClock(time.Now()))` with short `confirmTimeout` (50ms for unconfirmed-test, 30s elsewhere)
  - [ ] Helper `wsURL(serverURL string) string`: converts `http://` to `ws://`

- [ ] Write parser unit tests using fixture files (AC: 5)
  - [ ] `TestParseL2Update_FromFixture`: load `l2_update_btc_usdt.json`, call `parseL2Update`, assert Symbol, len(Deltas), Price/Size as strings, Seq, Side, TsExchange in nanoseconds
  - [ ] `TestParseL2Update_EmptyChanges`: wire message with empty bids and asks — assert `ParsedUpdate{Deltas: nil}`, no error
  - [ ] `TestParseTrade_BuySide`: load `trade_btc_usdt.json`, assert Side == "bid"
  - [ ] `TestParseTrade_SellSide`: load `trade_sell_btc_usdt.json`, assert Side == "ask"
  - [ ] `TestParseL2Update_MalformedData`: empty/invalid JSON in `data` field — assert non-nil error returned
  - [ ] `TestParseTrade_BadSequence`: non-numeric sequence string — assert non-nil error

- [ ] Write adapter happy-path integration test (AC: 1, 3)
  - [ ] `TestAdapter_ConnectSubscribeReceiveTick`: server sends welcome → client calls Subscribe(["BTC-USDT"], [FeedTypeOrderBook]) → server sends ack → server sends L2 update wire message → assert tick arrives on `adapter.Ticks()` within 2s with correct Symbol, Price, Size (strings), Side, EventType == EventTypeUpdate
  - [ ] After tick received, call `adapter.Close()` and verify it returns without blocking (2s deadline)

- [ ] Write heartbeat pong-timeout test (AC: 2)
  - [ ] `TestAdapter_HeartbeatPongTimeout`: MockWSServer returns `pingInterval=100ms`, `pingTimeout=100ms`; after connect, server does NOT respond to ping messages; assert `adapter.Signals()` receives `SignalNeedsSnapshot` within 2s (disconnect path: heartbeatLoop fires `reconnectTrigger` → runLoop emits NeedsSnapshot → runLoop tries to reconnect → call `adapter.Close()` to stop)
  - [ ] Note: at pong timeout, `runLoop` emits NeedsSnapshot only for CONFIRMED symbols. For this test, subscribe and ack at least one symbol before stopping pong responses.

- [ ] Write 200-symbol subscription test (AC: 4)
  - [ ] `TestAdapter_Subscribe200Symbols`: generate 200 raw symbols (e.g., `"SYM-1"` through `"SYM-200"`), call Subscribe, assert server receives exactly 2 subscribe messages (each with 100 symbols in topic), server acks both, assert all 200 symbols appear in `a.confirmed` map
  - [ ] Access `a.confirmed` (unexported) — requires `package kucoin` white-box test

- [ ] Write spurious ack test (AC: 3)
  - [ ] `TestAdapter_SpuriousAck`: after connect, server sends `{"id":"unknown-id","type":"ack"}` without the adapter having subscribed with that ID; verify: adapter does NOT panic; no symbols are added to `a.confirmed`; (WARN log is emitted but not asserted in test — log output testing is low value)

- [ ] Write unconfirmed-symbol timeout test (AC: 3)
  - [ ] `TestAdapter_UnconfirmedTimeout`: set `a.confirmTimeout = 50*time.Millisecond`; subscribe to one symbol, server does NOT send ack; wait 200ms; assert adapter logs ERROR (use `slog` capture or verify via side effect: `confirmWatcher` calls `sendBatch` retry, which sends another subscribe to the server — assert server receives 2 subscribe messages for the same symbol within 500ms)

- [ ] Write disconnect → NeedsSnapshot test (AC: 6)
  - [ ] `TestAdapter_DisconnectEmitsNeedsSnapshot`: server returns `pingInterval=100ms`, `pingTimeout=100ms`; adapter subscribes to `["BTC-USDT"]`, server acks → symbol confirmed; server then closes the WebSocket connection; within 1s, `heartbeatLoop` detects failure (write error or pong timeout) and fires `reconnectTrigger`; assert `SignalNeedsSnapshot` for `symbol.Normalize("kucoin","BTC-USDT")` arrives on `adapter.Signals()` within 2s; call `adapter.Close()` to stop reconnect loop

- [ ] Run `make test-l3` and verify all new tests pass (AC: all)
  - [ ] `cd aggregator && make test-l3` — all tests green
  - [ ] `cd aggregator && make test-l1` — no regressions
  - [ ] `cd aggregator && go vet ./internal/exchange/kucoin/...` — clean

- [ ] Update story and sprint status (AC: all)
  - [ ] Mark all task checkboxes
  - [ ] Update story Status → `review`
  - [ ] Update `_bmad-output/implementation-artifacts/sprint-status.yaml`: `2-2-kucoin-websocket-feed-adapter` → `review`

---

## Dev Notes

### Current Implementation State

**This is a test-creation and verification pass — the production code is already implemented.**

| File | State | Action |
|---|---|---|
| `kucoin.go` | Fully implemented ✓ | Verify ACs; no production code changes expected |
| `parser.go` | Fully implemented ✓ | Verify ACs; no production code changes expected |
| `token.go` | Fully implemented ✓ | Verify ACs; no production code changes expected |
| `kucoin_test.go` | Does not exist | CREATE — MockWSServer + all L3 tests |
| `testdata/fixtures/*.json` | Does not exist | CREATE — 3 fixture files |

### Test File Package: `package kucoin` (White-Box Required)

```go
// Package kucoin provides L3 acceptance tests for the KuCoin exchange adapter.
// White-box (package kucoin rather than kucoin_test) is required for:
//   - newWithAPIBase() — test constructor that overrides REST API base URL
//   - a.confirmed — unexported map verified in 200-symbol and spurious-ack tests
//   - a.confirmTimeout — overridden to 50ms in unconfirmed-timeout test
package kucoin
```

This is documented explicitly in architecture.md: "use `package foo` (white-box) only when testing unexported invariants — document why at file top."

### MockWSServer Design

The server must handle BOTH the KuCoin REST token endpoint AND WebSocket connections in a single `httptest.Server` (because the adapter's `newWithAPIBase` overrides the REST base URL, and that same URL is what the token response's `endpoint` must point to for WS).

```go
type mockWSServer struct {
    srv            *httptest.Server
    pingIntervalMs int
    pingTimeoutMs  int
    // dropNextPing: if true, server ignores the next ping (for pong-timeout test)
    dropPings      atomic.Bool
    // connDropped: closed by server handler after dropping the WS connection
    connDropped    chan struct{}
}

func newMockWSServer(t *testing.T, pingIntervalMs, pingTimeoutMs int) *mockWSServer {
    s := &mockWSServer{
        pingIntervalMs: pingIntervalMs,
        pingTimeoutMs:  pingTimeoutMs,
        connDropped:    make(chan struct{}),
    }
    mux := http.NewServeMux()
    mux.HandleFunc("/api/v1/bullet-private", s.handleToken)
    mux.HandleFunc("/", s.handleWebSocket)
    s.srv = httptest.NewServer(mux)
    t.Cleanup(s.srv.Close)
    return s
}

func (s *mockWSServer) handleToken(w http.ResponseWriter, r *http.Request) {
    // Build WS endpoint from our own server URL
    wsEndpoint := "ws" + s.srv.URL[len("http"):]
    resp := fmt.Sprintf(`{"code":"200000","data":{"token":"test-token","instanceServers":[{"endpoint":"%s","pingInterval":%d,"pingTimeout":%d}]}}`,
        wsEndpoint, s.pingIntervalMs, s.pingTimeoutMs)
    w.Header().Set("Content-Type", "application/json")
    w.Write([]byte(resp))
}

func (s *mockWSServer) handleWebSocket(w http.ResponseWriter, r *http.Request) {
    conn, err := websocket.Accept(w, r, nil)
    if err != nil { return }
    defer conn.Close(websocket.StatusNormalClosure, "")

    ctx := r.Context()
    // Send welcome
    wsjson.Write(ctx, conn, map[string]string{"type": "welcome"})

    for {
        var msg map[string]any
        if err := wsjson.Read(ctx, conn, &msg); err != nil { return }

        msgType, _ := msg["type"].(string)
        msgID, _ := msg["id"].(string)

        switch msgType {
        case "subscribe":
            if !s.dropPings.Load() { // reuse flag for simplicity or add separate dropAcks
                wsjson.Write(ctx, conn, map[string]string{"id": msgID, "type": "ack"})
            }
        case "ping":
            if !s.dropPings.Load() {
                wsjson.Write(ctx, conn, map[string]string{"id": msgID, "type": "pong"})
            }
        }
    }
}
```

For disconnect tests: close the WS connection mid-test by calling `s.srv.Close()`. This drops all active connections at the TCP level, which triggers `heartbeatLoop` to detect a write failure on the next ping.

### Disconnect Detection Path (Critical)

When the server closes the WebSocket connection, this is how the adapter detects it:

1. `readLoop` calls `conn.Read()` → fails → `readLoop` exits
2. `readLoop` exiting does NOT directly trigger `runLoop` — `runLoop` is blocked in `select { case <-conn.Reconnect(): ... case <-reconnectTrigger: ... }`
3. `heartbeatLoop` sends the next ping via `conn.Write()` → **fails** (connection closed) → calls `fireTrigger()` → `reconnectTrigger` fires
4. `runLoop` unblocks on `reconnectTrigger`, calls `emitNeedsSnapshot("disconnect")`, then enters reconnect loop

**Therefore:** disconnect tests MUST use a short `pingInterval` (e.g. 100ms) in the MockWSServer token response. Otherwise tests wait a full KuCoin production interval (~30s) for the heartbeat to fire.

**Test timing budget for disconnect tests:**
- `pingInterval` (100ms) + `pingTimeout` (100ms) = 200ms before reconnect triggers
- Use 2s deadline for `SignalNeedsSnapshot` to be received — this is ample headroom

### KuCoin Wire Message Formats

**Welcome (server → client):**
```json
{"type": "welcome", "id": ""}
```

**Subscribe (client → server):**
```json
{
  "id": "1",
  "type": "subscribe",
  "topic": "/market/level2:BTC-USDT,ETH-USDT",
  "privateChannel": false,
  "response": true
}
```

**Ack (server → client, one per subscribe message):**
```json
{"id": "1", "type": "ack"}
```

**Ping (client → server, JSON application-level):**
```json
{"id": "42", "type": "ping"}
```

**Pong (server → client):**
```json
{"id": "42", "type": "pong"}
```

**L2 Update (server → client):**
```json
{
  "id": "",
  "type": "message",
  "topic": "/market/level2:BTC-USDT",
  "subject": "trade.l2update",
  "data": {
    "sequenceStart": 100,
    "sequenceEnd": 101,
    "changes": {
      "bids": [["29500.50", "1.5", 100]],
      "asks": [["29501.00", "0", 101]]
    },
    "time": 1620000000000
  }
}
```

**Trade (server → client):**
```json
{
  "id": "",
  "type": "message",
  "topic": "/market/match:BTC-USDT",
  "subject": "trade.l3match",
  "data": {
    "sequence": "1627940716440",
    "price": "29500.00",
    "size": "0.1",
    "side": "buy",
    "time": "1620000000000000000",
    "tradeId": "test-trade-001",
    "symbol": "BTC-USDT"
  }
}
```

### Fixture File Format

Fixtures are the raw JSON objects as they appear on the wire — exactly what `wsjson.Read()` would decode into `wireMessage`. The test loads the file, calls `parseL2Update(msg)` or `parseTrade(msg)` directly, and asserts on the output.

**No streaming replay protocol needed** — the fixtures are used directly as input to the pure parser functions, not sent over a WS connection.

### 200-Symbol Test Pattern

```go
// Generate 200 symbols
syms := make([]string, 200)
for i := range syms {
    syms[i] = fmt.Sprintf("SYM%d-USDT", i+1)
}

// subBatchSize = 100, so Subscribe sends 2 messages, server acks 2
// After both acks, all 200 appear in a.confirmed
```

The MockWSServer must be configured to ack ALL subscribe messages (not just one). The test should count incoming subscribe messages from the server side and verify exactly 2 arrive for `FeedTypeOrderBook`.

### Test Adapter Constructor Pattern

```go
func newTestAdapter(t *testing.T, srv *mockWSServer) *Adapter {
    t.Helper()
    clk := testutil.NewMockClock(time.Now())
    a := newWithAPIBase(config.KuCoinConfig{}, http.DefaultClient, srv.srv.URL, clk)
    // confirmTimeout defaults to 30s — override for fast tests when needed
    return a
}
```

`config.KuCoinConfig{}` with empty credentials is fine — the token endpoint mock doesn't validate credentials (it returns success unconditionally). The `kucoinHMAC` HMAC computation with empty strings will not panic.

### sendTick is Non-Blocking (Channel Drop)

```go
func (a *Adapter) sendTick(t exchange.Tick) {
    select {
    case a.ticks <- t:
    default:
        slog.Warn("kucoin: ticks channel full, dropping", "sym", t.Symbol)
    }
}
```

`ticks` has capacity 4096 (`tickBuf`). Tests must drain `a.Ticks()` promptly. For tests that send multiple market data messages, use a consumer goroutine:

```go
var received []exchange.Tick
done := make(chan struct{})
go func() {
    defer close(done)
    for t := range adapter.Ticks() {
        received = append(received, t)
    }
}()
```

### Signals Channel: emitNeedsSnapshot Uses Non-Blocking Send

`emitNeedsSnapshot` uses:
```go
select {
case a.signals <- sig:
default:
    slog.Warn("kucoin: signals channel full", "sym", raw)
}
```

`signals` has capacity 256. Tests must read from `adapter.Signals()` promptly to avoid signals being dropped before assertions.

### Makefile Target for L3

```
cd aggregator && make test-l3
# runs: go test -count=1 -tags l3 ./...
```

This runs ALL packages — transport L3 tests and the new kucoin L3 tests. All must pass.

### Forbidden Patterns (Reminder)

- `time.Now()` anywhere in production code outside `cmd/` — use `clk.Now()`
- `time.Sleep()` in production code — use `time.After` in select with ctx.Done()
- `gorilla/websocket` or `nhooyr.io/websocket` — use `github.com/coder/websocket`
- MockWSServer in `internal/testutil/` — it belongs in `exchange/kucoin/kucoin_test.go`
- `t.Skip()` in new tests — activate fully from the start

### References

- `aggregator/internal/exchange/kucoin/kucoin.go` — full adapter implementation (read this first)
- `aggregator/internal/exchange/kucoin/parser.go` — wire format parsers
- `aggregator/internal/exchange/kucoin/token.go` — REST token fetch, `wsURL()`, `expiresAt()`
- `aggregator/internal/exchange/transport/conn.go` — `Conn` type used by adapter
- `aggregator/internal/exchange/transport/conn_test.go` — L3 test pattern with `httptest` + `coder/websocket`; reference for MockWSServer approach
- `aggregator/internal/testutil/clock.go` — `MockClock`, `ClockAdvancer`
- `aggregator/internal/testutil/captured_ws_session.go` — NOT used in story 2.2 (it's for reconnect state machine tests, not WS payload replay)
- `_bmad-output/project-context.md` — Technology Stack & Versions (non-negotiable library rules)
- `_bmad-output/planning-artifacts/architecture.md#MockWSServer placement` — line 125
- `_bmad-output/planning-artifacts/architecture.md#exchange/kucoin/ directory` — lines 454–460

---

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

None.

### Completion Notes List

### File List
