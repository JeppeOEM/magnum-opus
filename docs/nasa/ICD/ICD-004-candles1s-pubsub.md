# ICD-004 — Candles1s Redis Pub/Sub Interface

**Interface:** `candles1s:{exchange}:{symbol}` Redis pub/sub channel
**Producer:** candle-service (`candle-service/internal/writer/pubsub/`)
**Consumers:**
- gateway (`gateway/internal/hub/hub.go`) — forwards as JSON to browser WebSocket
- bot-service strategies with `orderbook_mode in ("snapshot_1s", "both")`
**Version:** 1.0

---

## Channel pattern

```
candles1s:{exchange}:{symbol}
```

Consumers subscribe with `PSUBSCRIBE candles1s:*`.

Examples:
- `candles1s:kucoin:BTC-USDT`
- `candles1s:bybit:BTCUSDT`

---

## Message format

JSON-encoded object published on each bar flush (complete or partial).

| Field | Type | Description |
|-------|------|-------------|
| `exchange` | string | Exchange name |
| `symbol` | string | Canonical symbol |
| `ts` | string | Bar timestamp, ISO 8601 UTC |
| `open` | float | Open price |
| `high` | float | High price |
| `low` | float | Low price |
| `close` | float | Close price |
| `volume` | float | Base asset volume |
| `quote_volume` | float | Quote asset volume |
| `trade_count` | int | Number of trades |
| `ofi` | float | Order flow imbalance |
| `realized_vol` | float \| null | Intra-second mid-price volatility |
| `spread_mean` | float | Average quoted spread |
| `best_bid` | float | Best bid at bar close |
| `best_ask` | float | Best ask at bar close |
| `buy_volume` | float | Buyer-initiated volume |
| `gap_count` | int | Gap events in this bar |
| `is_partial` | bool | `true` for 250ms partial bars |
| `type` | string | Always `"candle1s"` (injected by gateway for WebSocket routing) |

**Note:** `type` is injected by the gateway before forwarding to WebSocket clients.
The raw pub/sub message from candle-service does not include `type`.

---

## Publish frequency

- **Partial bars:** Every 250ms (4× per second), `is_partial: true`
- **Complete bars:** Once at the 1-second boundary, `is_partial: false`

Total publish rate: ~5 messages/second per (exchange, symbol).

---

## Delivery guarantee

Pub/sub is **at-most-once**. The most recent bar is available as the last snapshot
in the gateway Hub. Dashboard does not receive historical bars via pub/sub — those
come from QuestDB queries.

---

## Usage in bot-service strategies

Strategies with `orderbook_mode = "snapshot_1s"` or `"both"` receive these messages
via the BusManager's `_pubsub_c1s_callbacks`. The callback is called from the
`bus-pubsub` OS thread — it must be thread-safe.

```python
class MyStrategy(BaseStrategy):
    @property
    def orderbook_mode(self) -> str:
        return "snapshot_1s"

    def _on_candles1s(self, payload: dict) -> None:
        # Called from bus-pubsub thread — thread-safe!
        close_price = payload["close"]
        # Do NOT call place_order here — use the event loop queue
```

---

## Example message

```json
{
  "exchange": "kucoin",
  "symbol": "BTC-USDT",
  "ts": "2026-05-21T03:00:01Z",
  "open": 29500.00,
  "high": 29502.50,
  "low": 29499.00,
  "close": 29501.00,
  "volume": 0.125,
  "quote_volume": 3687.75,
  "trade_count": 8,
  "ofi": 0.45,
  "realized_vol": 0.00012,
  "spread_mean": 1.00,
  "best_bid": 29500.50,
  "best_ask": 29501.50,
  "buy_volume": 0.08,
  "gap_count": 0,
  "is_partial": false
}
```
