# ICD-002 — Candles Close Redis Stream Interface

**Interface:** `candles:close:{exchange}:{symbol}:{tf}` Redis Stream
**Producer:** candle-service (`candle-service/internal/writer/redis/`)
**Consumer:** bot-service (`bot-service/bot_service/bus/event_bus.py`)
**Version:** 1.0

---

## Stream key format

```
candles:close:{exchange}:{symbol}:{tf}
```

Examples:
- `candles:close:kucoin:BTC-USDT:1s`
- `candles:close:bybit:BTCUSDT:1m`

Timeframe values: `1s`, `1m`, `15m`

---

## Message schema

Each entry represents one bar (complete or partial).

| Field | Type | Description |
|-------|------|-------------|
| `ts` | string | Bar close timestamp, ISO 8601 UTC. e.g. `"2026-05-21T03:00:00Z"` |
| `exchange` | string | Exchange name: `kucoin`, `bybit` |
| `symbol` | string | Canonical symbol |
| `tf` | string | Timeframe: `1s`, `1m`, `15m` |
| `open` | string | Open price (float as string) |
| `high` | string | High price |
| `low` | string | Low price |
| `close` | string | Close price |
| `volume` | string | Base asset volume |
| `quote_volume` | string | Quote asset volume |
| `trade_count` | string | Trade count (integer as string) |
| `is_complete` | string | `"true"` or `"false"`. `false` = partial bar (250ms) |
| `has_gap` | string | `"true"` if `gap_count > 0` |

**All numeric values are serialized as strings** for Redis compatibility.
The consumer parses them to appropriate Python types (`float`, `int`, `bool`).

---

## Bar lifecycle

A complete 1-second bar is emitted once at the bar boundary.
Partial bars (`is_complete=false`) are emitted every 250ms for real-time dashboard use.

**In QuestDB**, the DEDUP UPSERT KEY `(ts, exchange, symbol)` ensures partial bars
are overwritten by the complete bar within the same second.

**In Redis Streams**, partial bars appear as separate messages. The bot-service
`BarClose` event consumer only processes `is_complete=true` messages for strategy signals.
Partial bars are used by the dashboard only.

---

## Consumer group

| Parameter | Value |
|-----------|-------|
| Group name | `bot-service` (configurable via `BOT_CONSUMER_GROUP`) |
| MAXLEN | Inherits from candle-service write configuration |
| ACK timing | After routing to all strategy queues (in BusManager) |

---

## Example entries

```
# Complete 1-second bar:
{
  ts: "2026-05-21T03:00:01Z",
  exchange: "kucoin",
  symbol: "BTC-USDT",
  tf: "1s",
  open: "29500.00",
  high: "29502.50",
  low: "29499.00",
  close: "29501.00",
  volume: "0.125",
  quote_volume: "3687.75",
  trade_count: "8",
  is_complete: "true",
  has_gap: "false"
}

# Partial 250ms bar:
{
  ts: "2026-05-21T03:00:01Z",
  exchange: "kucoin",
  symbol: "BTC-USDT",
  tf: "1s",
  open: "29500.00",
  high: "29501.00",
  low: "29500.00",
  close: "29501.00",
  volume: "0.04",
  quote_volume: "1180.04",
  trade_count: "3",
  is_complete: "false",
  has_gap: "false"
}
```

---

## Gap marker

A gap event is encoded as a special entry with `type: "gap"`:

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | `"gap"` |
| `exchange` | string | Affected exchange |
| `symbol` | string | Affected symbol |
| `gap_cause` | string | Gap cause string (see ICD-001) |
| `ts` | string | Gap timestamp (ms Unix epoch as string) |

The bot-service BusManager routes gap entries to all strategies as `GapMarker` events.

---

## Event type routing in bot-service

The `parse_stream_entry` function in `bot-service/bot_service/bus/event_types.py`
parses stream entries and returns typed events:

- `type == "gap"` → `GapMarker` → dispatched to `strategy.handle_gap()`
- Missing `type` field → `BarClose` (inferred from OHLCV fields) → dispatched to `strategy.on_bar()`
- Unknown type → logged as WARN, entry skipped
