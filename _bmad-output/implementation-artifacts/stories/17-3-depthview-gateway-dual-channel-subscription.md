# Story 17.3: depthview Gateway — Dual-Channel Subscription & Message Routing

Status: done

## Story

As mrqdt,
I want a WebSocket gateway that subscribes to both `orderbook:*` and `candles1s:*` Redis pub/sub channels and multiplexes them to the browser,
so that the frontend receives both tick-level book updates and 1s computed features through a single WebSocket connection.

**Note:** This story creates a new `gateway/` Go module at the repo root (the "depthview gateway"). The frontend already exists at `frontend/` and defines the binary WebSocket protocol the gateway must implement.

## Acceptance Criteria

### AC 1 — Redis dual-channel subscription

1. Gateway subscribes to both `orderbook:*` and `candles1s:*` using a single Redis `PSUBSCRIBE` call (or two PSUBSCRIBE calls on the same connection object — either is fine as long as it's one `*redis.Client`).
2. `REDIS_ADDR` env var controls the Redis address (default `localhost:6379`).

### AC 2 — Message type routing

3. A message arriving from channel `orderbook:{exchange}:{symbol}`:
   - Is encoded as binary per the `DepthPayload` binary format (see Dev Notes).
   - Is sent as a WebSocket binary frame to all clients subscribed to `{symbol}`.
4. A message arriving from channel `candles1s:{exchange}:{symbol}`:
   - Gets `"type": "candles1s"` injected into the JSON payload.
   - Is sent as a WebSocket UTF-8 text frame to all clients subscribed to `{symbol}`.

### AC 3 — Snapshot-on-connect cache

5. The gateway maintains a per-symbol cache of the last received binary-encoded orderbook snapshot.
6. When a browser client subscribes to a symbol (sends `MSG_SUBSCRIBE`), if a cached snapshot exists for that symbol, it is sent immediately as a binary frame.

### AC 4 — Binary DepthPayload encoding

7. The binary format for orderbook messages matches the decode logic in `frontend/src/ws.ts`:
   - Byte 0: `0x01` (MSG_SNAPSHOT type byte)
   - Bytes 1–8: `ts_ns` as `float64` big-endian
   - Bytes 9–10: `bidCount` as `uint16` little-endian
   - Bytes 11–12: `askCount` as `uint16` little-endian
   - For each bid level: `price` as `float64` big-endian (8 bytes) + `size` as `float32` big-endian (4 bytes)
   - For each ask level: `price` as `float64` big-endian (8 bytes) + `size` as `float32` big-endian (4 bytes)
   - Bids come first, then asks, in the order they appear in the JSON (`bids` array then `asks` array, already sorted by the publisher)
8. The gateway reads `bids` and `asks` as `[][]string` from the DepthPayload JSON; each inner element is `[price_str, size_str]`. Parse both as `float64` for binary encoding; skip unparseable levels silently.

### AC 5 — WebSocket server

9. The gateway starts an HTTP server at `GATEWAY_ADDR` (default `:8083`).
10. `/ws` serves WebSocket connections via `github.com/coder/websocket v1.8.14`.
11. `/health` returns `{"status":"ok"}` as HTTP 200.
12. Browser subscribe/unsubscribe messages use the binary format from `frontend/src/ws.ts`:
    - `MSG_SUBSCRIBE = 0x10`: `[0x10][len u8][symbol_bytes]` — gateway adds client to `bySymbol[symbol]` and sends cached snapshot if present.
    - `MSG_UNSUBSCRIBE = 0x11`: `[0x11][len u8][symbol_bytes]` — gateway removes client from `bySymbol[symbol]`.
13. WebSocket accept options: `InsecureSkipVerify: true` (single-user local deployment; no CORS restrictions needed).

### AC 6 — Build and test gates

14. `go build ./...` from `gateway/` compiles clean.
15. `go test ./...` (L1) passes with no external dependencies.
16. New L1 tests: at minimum 4 codec tests + 3 hub routing tests = 7 tests.

## Tasks / Subtasks

- [x] Task 1: Create `gateway/go.mod` and directory scaffold
  - [x] `go mod init github.com/mrqdt/magnum-opus/gateway` with `go 1.21`
  - [x] Dependencies: `github.com/coder/websocket v1.8.14`, `github.com/redis/go-redis/v9`
  - [x] Create empty directories: `cmd/gateway/`, `internal/config/`, `internal/codec/`, `internal/hub/`

- [x] Task 2: `internal/config/config.go`
  - [x] `Config` struct: `RedisAddr`, `ListenAddr` fields
  - [x] `Load()` reads from env: `REDIS_ADDR` (default `localhost:6379`), `GATEWAY_ADDR` (default `:8083`)

- [x] Task 3: `internal/codec/codec.go` + tests (AC 4, AC 8)
  - [x] `EncodeBinary(payload []byte) ([]byte, error)` — decodes DepthPayload JSON, produces binary
  - [x] `InjectType(payload []byte, msgType string) ([]byte, error)` — injects `"type"` field into JSON
  - [x] Unit tests: type byte, level count, empty book, ts_ns encoding, InjectType output

- [x] Task 4: `internal/hub/hub.go` (AC 2, AC 3)
  - [x] `Hub` struct: `clients`, `bySymbol`, `lastSnap` (all guarded by `sync.RWMutex`)
  - [x] `NewHub() *Hub`
  - [x] `Route(channel string, payload []byte)` — routes to subscribed clients based on channel prefix
  - [x] `RegisterSender(s Sender)` / `UnregisterSender(s Sender)` — add/remove connected clients
  - [x] `Subscribe(s Sender, symbol string)` / `Unsubscribe(s Sender, symbol string)` — manage bySymbol map and send cached snapshot on subscribe

- [x] Task 5: `cmd/gateway/main.go` (AC 1, AC 5)
  - [x] Load config, create Redis client
  - [x] Create Hub, start Redis pub/sub goroutine (PSUBSCRIBE `orderbook:*` and `candles1s:*`)
  - [x] Start HTTP server: `/ws` (WebSocket handler), `/health` (JSON 200)
  - [x] Per-client goroutine: reads subscribe/unsubscribe messages from WebSocket
  - [x] Graceful shutdown on SIGTERM/SIGINT

- [x] Task 6: Build and test gates (AC 6)
  - [x] `go build ./...` clean
  - [x] `go test ./...` all pass (11 tests: 6 codec + 5 hub)

## Dev Notes

### Directory — this is a NEW Go module in magnum-opus

Create `gateway/` at the repo root. This is the "depthview gateway" (formerly planned as `../code/depthview`). Since the frontend lives at `frontend/` in magnum-opus, the gateway lives here too.

```
gateway/
├── cmd/gateway/main.go
├── internal/
│   ├── config/config.go
│   ├── codec/codec.go
│   ├── codec/codec_test.go
│   ├── hub/hub.go
│   └── hub/hub_test.go
├── go.mod
└── go.sum
```

Module path: `github.com/mrqdt/magnum-opus/gateway`

### Binary DepthPayload encoding (AC 4, AC 8)

The frontend `frontend/src/ws.ts` decodes binary frames from the gateway. This is the EXACT decode logic the gateway must mirror when encoding:

```typescript
// ws.ts decode (the inverse of what gateway must produce)
if (type === MSG_SNAPSHOT) {             // type = view.getUint8(0)  →  0x01
  const tsNs      = view.getFloat64(1, false); // big-endian float64
  const bidCount  = view.getUint16(9, true);   // little-endian uint16
  const askCount  = view.getUint16(11, true);  // little-endian uint16
  // for each level:
  const price = view.getFloat64(off, false);   // big-endian float64
  const size  = view.getFloat32(off+8, false); // big-endian float32
}
```

Gateway Go encoding using `encoding/binary`:

```go
const msgSnapshot = 0x01

func EncodeBinary(payload []byte) ([]byte, error) {
    var dp depthPayload
    if err := json.Unmarshal(payload, &dp); err != nil {
        return nil, err
    }
    bidLevels := parseLevels(dp.Bids)
    askLevels := parseLevels(dp.Asks)

    // header: 1 (type) + 8 (tsNs f64) + 2 (bidCount u16) + 2 (askCount u16) = 13 bytes
    // per level: 8 (price f64) + 4 (size f32) = 12 bytes
    buf := make([]byte, 13+(len(bidLevels)+len(askLevels))*12)
    buf[0] = msgSnapshot
    binary.BigEndian.PutUint64(buf[1:], math.Float64bits(float64(dp.TsNs)))
    binary.LittleEndian.PutUint16(buf[9:], uint16(len(bidLevels)))
    binary.LittleEndian.PutUint16(buf[11:], uint16(len(askLevels)))
    off := 13
    for _, lv := range append(bidLevels, askLevels...) {
        binary.BigEndian.PutUint64(buf[off:], math.Float64bits(lv.price))
        binary.BigEndian.PutUint32(buf[off+8:], math.Float32bits(float32(lv.size)))
        off += 12
    }
    return buf, nil
}

type depthPayload struct {
    TsNs int64      `json:"ts_ns"`
    Bids [][]string `json:"bids"`
    Asks [][]string `json:"asks"`
}

type levelEntry struct{ price, size float64 }

func parseLevels(raw [][]string) []levelEntry {
    out := make([]levelEntry, 0, len(raw))
    for _, pair := range raw {
        if len(pair) < 2 { continue }
        p, ep := strconv.ParseFloat(pair[0], 64)
        s, es := strconv.ParseFloat(pair[1], 64)
        if ep != nil || es != nil { continue }
        out = append(out, levelEntry{p, s})
    }
    return out
}
```

### candles1s JSON type injection (AC 2, AC 4)

For `candles1s:*` messages, inject `"type":"candles1s"` into the existing JSON:

```go
func InjectType(payload []byte, msgType string) ([]byte, error) {
    var m map[string]any
    if err := json.Unmarshal(payload, &m); err != nil {
        return nil, err
    }
    m["type"] = msgType
    return json.Marshal(m)
}
```

This is used for candles1s messages. The result is sent as `websocket.MessageText`.

For orderbook messages, the type is the binary 0x01 byte — no JSON field needed.

### Hub design (AC 2, AC 3)

The hub uses a `Sender` interface so routing logic is unit-testable without real WebSocket connections:

```go
// Sender is the minimal interface for sending messages to a client.
// *websocket.Conn does NOT satisfy this directly — the real client wraps it.
type Sender interface {
    Send(typ websocket.MessageType, data []byte)
}

type message struct {
    typ  websocket.MessageType
    data []byte
}

// client wraps a WebSocket connection. Send is non-blocking: drops if channel full.
type client struct {
    conn    *websocket.Conn
    symbols map[string]bool
    send    chan message  // buffered, size 16
}

func (c *client) Send(typ websocket.MessageType, data []byte) {
    select {
    case c.send <- message{typ, data}:
    default: // drop if slow consumer
    }
}

type Hub struct {
    mu       sync.RWMutex
    senders  map[Sender]bool
    bySymbol map[string]map[Sender]bool
    lastSnap map[string][]byte // last binary snapshot per symbol
}
```

**Route** receives a channel string and payload:
1. Split: `strings.SplitN(channel, ":", 3)` → `[prefix, exchange, symbol]`
2. If prefix is `"orderbook"`: `EncodeBinary(payload)`, store in `lastSnap[symbol]`, send binary to `bySymbol[symbol]` senders
3. If prefix is `"candles1s"`: `InjectType(payload, "candles1s")`, send text to `bySymbol[symbol]` senders

**subscribe** (called when browser sends MSG_SUBSCRIBE):
1. Adds sender to `bySymbol[symbol]`
2. If `lastSnap[symbol]` exists, sends it immediately as binary

### WebSocket protocol (browser → gateway messages)

From `frontend/src/ws.ts`:

```
MSG_SUBSCRIBE   = 0x10  →  [0x10][len:1byte][symbol:len bytes]
MSG_UNSUBSCRIBE = 0x11  →  [0x11][len:1byte][symbol:len bytes]
```

Gateway reads loop for each connected client:

```go
for {
    _, data, err := conn.Read(ctx)
    if err != nil { break }
    if len(data) < 2 { continue }
    msgType := data[0]
    symLen  := int(data[1])
    if len(data) < 2+symLen { continue }
    sym := string(data[2 : 2+symLen])
    switch msgType {
    case 0x10: hub.subscribe(c, sym)    // MSG_SUBSCRIBE
    case 0x11: hub.unsubscribe(c, sym)  // MSG_UNSUBSCRIBE
    }
}
```

### coder/websocket v1.8.14 server-side API

```go
// Accept a WebSocket upgrade in an HTTP handler:
conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{
    InsecureSkipVerify: true,
})

// Read a message from browser:
msgType, data, err := conn.Read(ctx)

// Write binary to browser:
err = conn.Write(ctx, websocket.MessageBinary, binaryData)

// Write text (JSON) to browser:
err = conn.Write(ctx, websocket.MessageText, jsonData)
```

`websocket.MessageText` and `websocket.MessageBinary` are the two message type constants.

### Redis pub/sub subscription

Use go-redis v9 `PubSub`:

```go
ps := rdb.PSubscribe(ctx, "orderbook:*", "candles1s:*")
defer ps.Close()

ch := ps.Channel()
for msg := range ch {
    hub.Route(msg.Channel, []byte(msg.Payload))
}
```

`msg.Channel` is the full channel name (e.g., `"orderbook:kucoin:BTC-USDT"`).
`msg.Payload` is the JSON string from Redis.

A single `PSubscribe` call with two patterns subscribes to both channels on one connection. This satisfies AC 1.

### Client send goroutine pattern

Each client has a buffered `send` channel and a write goroutine:

```go
func (c *client) writePump(ctx context.Context) {
    for msg := range c.send {
        if err := c.conn.Write(ctx, msg.typ, msg.data); err != nil {
            return
        }
    }
}
```

Hub sends to clients by doing `c.send <- message{...}` (non-blocking: if channel full, drop and log).

Buffer size 16 per client is sufficient for single-user local use.

### Symbol extraction from Redis channel

The channel format is `{prefix}:{exchange}:{symbol}` (e.g., `"orderbook:kucoin:BTC-USDT"`).

```go
parts := strings.SplitN(channel, ":", 3)
if len(parts) != 3 { return }
prefix, _, symbol := parts[0], parts[1], parts[2]
```

Browser subscribe messages send just the symbol (e.g., `"BTC-USDT"` from the HTML `<select>` value). The hub maps by symbol only.

### Concurrency model

- One goroutine runs the Redis pub/sub loop (calls `hub.Route`).
- One goroutine per WebSocket client reads subscribe/unsubscribe messages.
- One goroutine per WebSocket client writes outbound messages from `send` channel.
- Hub uses `sync.RWMutex` to protect `clients`, `bySymbol`, `lastSnap`.
- Client `send` channel is buffered — write goroutine is the only writer to the WebSocket.

### Port

`GATEWAY_ADDR` defaults to `:8083`. This is next after candle-green's `:8082`. The frontend uses `VITE_WS_URL ?? "/ws"` — in development, run vite as proxy to `localhost:8083`.

### Error handling conventions

Follow the same patterns as other services:
- `log/slog` for structured logging
- `ctx.Err() == nil` guard before logging on shutdown
- Fire-and-forget: errors from individual client writes are logged at WARN and the client is disconnected; never block the Route loop

### What NOT to implement

- No `/candles` REST endpoint (that's for story 17-5)
- No Prometheus metrics (out of scope for this story)
- No Dockerfile or docker-compose entry (out of scope)
- No heartbeat pings to browsers (the frontend reconnects on close; coder/websocket handles pong frames internally)

### Testing guidance

**`internal/codec/codec_test.go`** — pure unit tests, no external deps:

```go
func TestEncodeBinary_TypeByte(t *testing.T) {
    payload := mustJSON(map[string]any{
        "ts_ns": int64(1_700_000_000_000_000_000),
        "bids": [][]string{{"29500.0", "1.5"}},
        "asks": [][]string{{"29501.0", "0.5"}},
    })
    b, err := EncodeBinary(payload)
    require.NoError(t, err)
    assert.Equal(t, byte(0x01), b[0]) // MSG_SNAPSHOT
}

func TestEncodeBinary_LevelCounts(t *testing.T) {
    // 2 bids, 3 asks → bidCount=2, askCount=3 in little-endian uint16
    ...
}

func TestEncodeBinary_EmptyBook(t *testing.T) {
    // empty bids+asks → still valid header with counts=0
    ...
}

func TestEncodeBinary_TsNsEncoding(t *testing.T) {
    // ts_ns=1234567890 → verify bytes 1-8 decode back to same float64
    ...
}

func TestInjectType_Candles1s(t *testing.T) {
    payload := mustJSON(map[string]any{"ts_ns": 123, "close": 29500.0})
    out, err := InjectType(payload, "candles1s")
    require.NoError(t, err)
    var m map[string]any
    require.NoError(t, json.Unmarshal(out, &m))
    assert.Equal(t, "candles1s", m["type"])
    assert.Equal(t, float64(29500), m["close"]) // original fields preserved
}
```

**`internal/hub/hub_test.go`** — uses `fakeSender` (no WebSocket connections needed):

```go
// fakeSender implements Sender for unit tests
type fakeSender struct {
    messages []message
}

func (f *fakeSender) Send(typ websocket.MessageType, data []byte) {
    f.messages = append(f.messages, message{typ, data})
}

func TestRoute_Orderbook(t *testing.T) {
    h := NewHub()
    fs := &fakeSender{}
    h.RegisterSender(fs)
    h.Subscribe(fs, "BTC-USDT")
    payload := mustJSON(map[string]any{"ts_ns": int64(1_000_000), "bids": [][]string{{"100.0","1.0"}}, "asks": [][]string{{"101.0","0.5"}}})
    h.Route("orderbook:kucoin:BTC-USDT", payload)
    require.Len(t, fs.messages, 1)
    assert.Equal(t, websocket.MessageBinary, fs.messages[0].typ)
    assert.Equal(t, byte(0x01), fs.messages[0].data[0])
}

func TestRoute_Candles1s(t *testing.T) {
    h := NewHub()
    fs := &fakeSender{}
    h.RegisterSender(fs)
    h.Subscribe(fs, "BTC-USDT")
    payload := mustJSON(map[string]any{"ts_ns": int64(1_000_000), "close": 100.0})
    h.Route("candles1s:kucoin:BTC-USDT", payload)
    require.Len(t, fs.messages, 1)
    assert.Equal(t, websocket.MessageText, fs.messages[0].typ)
    var m map[string]any
    require.NoError(t, json.Unmarshal(fs.messages[0].data, &m))
    assert.Equal(t, "candles1s", m["type"])
    assert.InDelta(t, 100.0, m["close"], 1e-9)
}

func TestSubscribeOnConnect_SendsLastSnapshot(t *testing.T) {
    h := NewHub()
    // Pre-populate cache by routing an orderbook message
    firstClient := &fakeSender{}
    h.RegisterSender(firstClient)
    h.Subscribe(firstClient, "BTC-USDT")
    payload := mustJSON(map[string]any{"ts_ns": int64(1_000_000), "bids": [][]string{{"100.0","1.0"}}, "asks": [][]string{{"101.0","0.5"}}})
    h.Route("orderbook:kucoin:BTC-USDT", payload) // populates lastSnap
    // Now a NEW client subscribes — should receive cached snapshot immediately
    newClient := &fakeSender{}
    h.RegisterSender(newClient)
    h.Subscribe(newClient, "BTC-USDT") // triggers snapshot-on-connect
    require.Len(t, newClient.messages, 1)
    assert.Equal(t, websocket.MessageBinary, newClient.messages[0].typ)
    assert.Equal(t, byte(0x01), newClient.messages[0].data[0])
}
```

### CLAUDE.md rules to follow

- `log/slog` for logging (stdlib)
- `context.Context` as first param on all IO functions
- `os.Getenv` only in `internal/config/`
- `github.com/redis/go-redis/v9` — never v8
- `github.com/coder/websocket` — never gorilla/websocket
- No `time.Now()` in `internal/` packages (but the gateway has no clock-injected path; this rule applies to aggregator and candle-service only)
- The gateway is a NEW module — it does NOT import from aggregator or candle-service

### References

- Frontend binary protocol: `frontend/src/ws.ts` (the definitive decode spec)
- Frontend subscribe format: `frontend/src/ws.ts` `sendSubscribe` method
- DepthPayload JSON schema: `aggregator/internal/coordinator/interfaces.go` OBPublisher doc comment
- candles1s JSON schema: `candle-service/internal/writer/pubsub/publisher.go` Publish1sBar payload
- coder/websocket Accept: `/home/mrqdt/go/pkg/mod/github.com/coder/websocket@v1.8.14/accept.go`
- coder/websocket Read/Write: `/home/mrqdt/go/pkg/mod/github.com/coder/websocket@v1.8.14/read.go`, `write.go`
- go-redis PubSub: `rdb.PSubscribe(ctx, patterns...)` → `.Channel()` → `for msg := range ch`

### Review Findings

- [x] [Review][Patch] Shutdown context has no timeout — hangs indefinitely on misbehaving clients [cmd/gateway/main.go:41]
- [x] [Review][Patch] Redis pubsub no reconnect — silently goes dark when pubsub channel closes [cmd/gateway/main.go:50-65]
- [x] [Review][Patch] uint16 cast with no bounds check — wraps silently if level count > 65535 [internal/codec/codec.go:51-52]
- [x] [Review][Patch] parseLevels silent skip — no slog.Warn on unparseable pair data loss [internal/codec/codec.go:83-86]
- [x] [Review][Patch] WritePump goroutine leak — for-range loop does not check ctx.Done() [internal/hub/hub.go:51-60]
- [x] [Review][Patch] lastSnap memory leak — symbol entries never evicted from map [internal/hub/hub.go:68]
- [x] [Review][Patch] Unknown WebSocket opcode silently discarded — no warning log [cmd/gateway/main.go:99-104]
- [x] [Review][Patch] Empty symbol guard missing — symLen==0 pollutes bySymbol map [cmd/gateway/main.go:94-98]
- [x] [Review][Patch] WebSocket message type not validated — text frames accepted as binary control [cmd/gateway/main.go:87]
- [x] [Review][Defer] InjectType int64 precision via map[string]any round-trip [internal/codec/codec.go:66-73] — deferred, no practical impact for 1s candle display data
- [x] [Review][Defer] No protocol version byte — premature for v1 [internal/codec/codec.go] — deferred, pre-existing design choice

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

- `go mod tidy` must be run after each new file is created that adds imports — LSP shows spurious "cannot import" until tidy resolves transitive deps
- `go 1.24` required (go-redis/v9 v9.19.0 demands it; tidy auto-upgraded from 1.21)
- `client` type exported as `Client` + `NewClient` factory so `cmd/gateway/main.go` can create instances; `WritePump` also exported

### Completion Notes List

- All 6 ACs satisfied; 11 unit tests pass (6 codec + 5 hub)
- `client` struct renamed to `Client` (exported) with `NewClient(conn) *Client` and `WritePump` exported — required for main.go to use it; fakeSender in tests is unaffected (uses Sender interface)
- `symbols` field removed from Client struct (it was dead code; hub tracks subscriptions in bySymbol, not on the client)
- main.go uses `signal.NotifyContext` for graceful shutdown; server Shutdown called on ctx.Done

### File List

- `gateway/go.mod` — NEW
- `gateway/go.sum` — NEW
- `gateway/cmd/gateway/main.go` — NEW
- `gateway/internal/config/config.go` — NEW
- `gateway/internal/codec/codec.go` — NEW
- `gateway/internal/codec/codec_test.go` — NEW
- `gateway/internal/hub/hub.go` — NEW
- `gateway/internal/hub/hub_test.go` — NEW
