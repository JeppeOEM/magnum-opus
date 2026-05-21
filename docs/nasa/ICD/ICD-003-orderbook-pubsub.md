# ICD-003 — Order Book Redis Pub/Sub Interface

**Interface:** `orderbook:{exchange}:{symbol}` Redis pub/sub channel
**Producer:** aggregator (`aggregator/internal/writer/pubsub/`)
**Consumers:**
- gateway (`gateway/internal/hub/hub.go`) — forwards as binary WebSocket to browser
- bot-service strategies with `orderbook_mode in ("full_stream", "both")`
**Version:** 1.0

---

## Channel pattern

```
orderbook:{exchange}:{symbol}
```

Consumers subscribe with `PSUBSCRIBE orderbook:*` to receive all symbols.

Examples:
- `orderbook:kucoin:BTC-USDT`
- `orderbook:bybit:BTCUSDT`

---

## Message format

JSON-encoded object published on every EventTypeUpdate tick (after each L2 OB update).

| Field | Type | Description |
|-------|------|-------------|
| `exchange` | string | Exchange name |
| `symbol` | string | Canonical symbol |
| `ts` | int | Nanosecond Unix timestamp from the exchange tick |
| `seq` | int | Sequence number of the triggering tick |
| `bids` | array | Array of `[price_string, size_string]` pairs, best bid first |
| `asks` | array | Array of `[price_string, size_string]` pairs, best ask first |

`bids` and `asks` contain the **full visible book** (all levels), sorted:
- Bids: descending by price (best bid = first element)
- Asks: ascending by price (best ask = first element)

**Prices and sizes are strings** to preserve exchange precision.

---

## Binary encoding (gateway → browser)

The gateway re-encodes the JSON into a compact binary format for WebSocket transmission.
See `gateway/internal/codec/codec.go` for the binary layout.

Binary format (little-endian):
```
[4 bytes: magic]
[8 bytes: ts (int64, nanoseconds)]
[8 bytes: seq (uint64)]
[4 bytes: bid_count (int32)]
[bid_count × (8+8 bytes): (price float64, size float64)]
[4 bytes: ask_count (int32)]
[ask_count × (8+8 bytes): (price float64, size float64)]
```

The gateway decodes the JSON strings to float64 for binary encoding.
String precision is preserved to the extent that float64 represents it.

---

## Delivery guarantee

Pub/sub is **at-most-once**: messages can be dropped if:
- A subscriber is not connected when the message is published
- The subscriber's connection buffer is full (gateway: 16-message client send buffer)

**This is intentional.** The order book is a real-time snapshot. A missed update is
not harmful — the next update carries the full corrected book state.
There is no replay mechanism.

---

## Frequency

One message per L2 tick that changes the book state (excluding trades, which don't
update the visible book). In high-activity markets, this can be 100–500 messages/second
per symbol.

---

## Last snapshot delivery (gateway)

The gateway's Hub stores the last binary snapshot per symbol (`Hub.lastSnap`).
New WebSocket subscribers immediately receive the last known book state upon connection,
so the dashboard does not start blank.

---

## Example message

```json
{
  "exchange": "kucoin",
  "symbol": "BTC-USDT",
  "ts": 1716278400123456789,
  "seq": 12345679,
  "bids": [
    ["29500.00", "0.500"],
    ["29499.50", "1.200"],
    ["29499.00", "2.000"]
  ],
  "asks": [
    ["29501.00", "0.300"],
    ["29501.50", "0.800"],
    ["29502.00", "1.500"]
  ]
}
```
