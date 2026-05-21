# Chapter 05 — Gateway

**30-second summary:** The gateway is a tiny Go service that bridges Redis pub/sub to
browser WebSocket clients. It subscribes to `orderbook:*` and `candles1s:*` channels
from Redis, encodes order book payloads to a compact binary format, and fan-outs each
message to clients who have subscribed to that symbol. New clients receive an
immediate snapshot of the last known order book state.

All source lives under `gateway/`.

---

## 1. Package Structure

```
gateway/
├── cmd/gateway/main.go         Entry point
└── internal/
    ├── hub/hub.go              Hub: symbol routing + client registry
    ├── hub/hub_test.go
    ├── codec/codec.go          Binary encoding for orderbook messages
    ├── codec/codec_test.go
    └── config/config.go        Config from env
```

---

## 2. Architecture

```
Redis pub/sub
  orderbook:{ex}:{sym}  ──────────┐
  candles1s:{ex}:{sym}  ──────────┤
                                   ▼
                             runSubscriber
                               (goroutine)
                                   │
                             hub.Route(channel, payload)
                                   │
               ┌───────────────────┴─────────────────────┐
               │ channel starts with "orderbook:"          │ starts with "candles1s:"
               ▼                                           ▼
         routeOrderbook(symbol, payload)          routeCandles1s(symbol, payload)
               │                                           │
         codec.EncodeBinary(json)               codec.InjectType(json, "candles1s")
               │                                           │
         hub.lastSnap[symbol] = encoded          (no snapshot cache)
               │                                           │
         for each subscriber of symbol:          for each subscriber:
           client.Send(Binary, encoded)            client.Send(Text, tagged)
```

---

## 3. Main Entry Point

[`gateway/cmd/gateway/main.go`](../../gateway/cmd/gateway/main.go)

```go
rdb  := goredis.NewClient(&goredis.Options{Addr: cfg.RedisAddr})
h    := hub.NewHub()

go runSubscriber(ctx, rdb, h)      // Redis fan-out goroutine

mux.HandleFunc("/ws", wsHandler(ctx, h))
mux.HandleFunc("/health", healthHandler)
srv.ListenAndServe()
```

**`runSubscriber`:** Runs `subscribe()` in an infinite loop with 1-second reconnect
delay on error. The loop retries automatically on Redis pub/sub disconnection.

**`subscribe()`:** Opens a `PSubscribe` to `"orderbook:*", "candles1s:*"` and calls
`h.Route(msg.Channel, []byte(msg.Payload))` for each message until the context is
cancelled.

---

## 4. The Hub

[`gateway/internal/hub/hub.go`](../../gateway/internal/hub/hub.go)

The `Hub` is the central routing structure, protected by a single `sync.RWMutex`.

```go
type Hub struct {
    mu       sync.RWMutex
    senders  map[Sender]bool              // all connected clients
    bySymbol map[string]map[Sender]bool   // symbol → set of subscribers
    lastSnap map[string][]byte            // last binary snapshot per symbol
}
```

**All exported methods are goroutine-safe.**

### Client Lifecycle

1. `RegisterSender(c)` — called when WebSocket handshake completes.
2. Client sends binary control messages to subscribe/unsubscribe:
   - Byte 0: `0x10` = subscribe, `0x11` = unsubscribe.
   - Byte 1: symbol length (`symLen`).
   - Bytes 2..2+symLen: UTF-8 symbol string.
3. `Subscribe(c, symbol)` — adds to `bySymbol[symbol]`; if `lastSnap[symbol]` exists,
   sends it immediately (cache hit = no waiting for next OB update).
4. `UnregisterSender(c)` — removes from all symbol sets; cleans up empty sets +
   deletes `lastSnap` entries for symbols with zero subscribers.

### `Send()` — Non-Blocking Drop

```go
func (c *Client) Send(typ websocket.MessageType, data []byte) {
    select {
    case c.send <- message{typ: typ, data: data}:
    default:
        slog.Warn("gateway: client send buffer full — dropping message")
    }
}
```

Each `Client` has a 16-message buffered channel (`clientSendBuf = 16`). If a slow
client's buffer is full, the message is silently dropped. The client's `WritePump`
goroutine drains this channel and writes to the WebSocket.

---

## 5. Binary Codec

[`gateway/internal/codec/codec.go`](../../gateway/internal/codec/codec.go)

**`EncodeBinary(jsonPayload []byte) ([]byte, error)`**

Converts the JSON order book snapshot (from Redis pub/sub) to a compact binary
format for efficient browser delivery. The binary frame contains:
- A fixed header (message type byte + exchange/symbol offsets).
- Packed bid and ask levels as `(price float64, size float64)` pairs.

This reduces the per-message payload size significantly vs raw JSON for
high-frequency order book updates.

**`InjectType(payload []byte, msgType string) ([]byte, error)`**

Takes a JSON blob and adds a `"type": "candles1s"` field. The candles payload
is sent as a text (JSON) frame rather than binary, since it's lower-frequency
and already structured for the dashboard to consume directly.

---

## 6. Configuration

[`gateway/internal/config/config.go`](../../gateway/internal/config/config.go)

| Env var | Default | Purpose |
|---------|---------|---------|
| `GATEWAY_LISTEN_ADDR` | `:8083` | HTTP + WebSocket listen address |
| `REDIS_ADDR` | `redis:6379` | Redis connection |

---

## 7. Error Handling

| Scenario | Behaviour |
|----------|-----------|
| Redis pub/sub disconnects | `runSubscriber` retries after 1 s |
| Client WebSocket write fails | `WritePump` logs and returns; client is cleaned up |
| Client send buffer full | Message dropped; `slog.Warn` logged |
| Binary encode fails | `slog.Warn`; message skipped (pub/sub continues) |
| Unknown message opcode from client | `slog.Warn`; ignored |

---

## 8. Connecting from a Browser Client

```javascript
const ws = new WebSocket("ws://localhost:8083/ws");

// Subscribe to BTC-USDT orderbook updates
ws.addEventListener("open", () => {
    const sym = "BTC-USDT";
    const buf = new Uint8Array(2 + sym.length);
    buf[0] = 0x10;  // subscribe
    buf[1] = sym.length;
    for (let i = 0; i < sym.length; i++) buf[2 + i] = sym.charCodeAt(i);
    ws.send(buf.buffer);
});

// Binary frames = orderbook snapshots
// Text frames   = candles1s JSON
ws.addEventListener("message", (e) => {
    if (e.data instanceof ArrayBuffer) {
        // decode binary orderbook frame
    } else {
        const data = JSON.parse(e.data);  // candles1s
    }
});
```

The dashboard uses this WebSocket via a `dcc.Store` + interval polling hybrid — the
gateway is the real-time delivery path while QuestDB provides historical data for the
initial page load.
