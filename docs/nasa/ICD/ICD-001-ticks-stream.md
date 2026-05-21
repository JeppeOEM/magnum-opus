# ICD-001 — Ticks Redis Stream Interface

**Interface:** `ticks:{exchange}:{symbol}` Redis Stream
**Producer:** aggregator (`aggregator/internal/writer/redis/`)
**Consumer:** candle-service (`candle-service/internal/consumer/`)
**Version:** 1.0

---

## Stream key format

```
ticks:{exchange}:{symbol}
```

Examples:
- `ticks:kucoin:BTC-USDT`
- `ticks:bybit:BTCUSDT`

Exchange values: `kucoin`, `bybit`
Symbol format: exchange-canonical (normalized by `internal/symbol/symbol.go`).

---

## Message types

Each Redis Stream entry has a `type` field that determines the message schema.
Messages without a recognized `type` are logged as WARN and skipped by the consumer.

### type: `tick`

A single market event: either a trade execution or an L2 order book update.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Always `"tick"` |
| `price` | string | yes | Price as exact string (preserves exchange precision). e.g. `"29500.50"` |
| `size` | string | yes | Size as exact string. `"0"` means remove level (for OB updates). |
| `side` | string | yes | `"buy"` or `"sell"` for trades; `"buy"` or `"sell"` for OB side |
| `level` | string | yes | `"0"` = trade execution; `"1"` or higher = L2 OB update at that depth |
| `ts_ms` | string | yes | Exchange timestamp, millisecond Unix epoch as string |
| `seq` | string | yes | Exchange sequence number as string. Used for gap detection. |

**Trade vs OB update discrimination:**
- `level == "0"` → trade (matched order)
- `level != "0"` → L2 order book update at that level

**Price and size are strings**, not floats. The consumer parses them to float64 for arithmetic.
This preserves exact exchange precision and avoids float rounding on serialization.

### type: `gap`

Signals a sequence gap detected by the aggregator. The consumer should:
1. Call `acc.IncrementGap()` to mark the current bar as having a gap
2. Call `book.ApplyGap(gap)` to handle book state (may reset `snapshotSeen`)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Always `"gap"` |
| `gap_cause` | string | yes | One of: `internal_merge_error`, `internal_buffer_overflow`, `external_disconnect`, `external_rate_limit` |
| `exchange` | string | yes | Exchange name |
| `symbol` | string | yes | Symbol |
| `seq_before` | string | yes | Sequence number before the gap |
| `seq_after` | string | yes | Sequence number after the gap |
| `gap_ts_ms` | string | yes | When the gap was detected (ms Unix epoch) |

### type: `snapshot`

Signals that a full REST order book snapshot was received and applied.
The consumer should reset its accumulator state.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Always `"snapshot"` |
| `exchange` | string | yes | Exchange name |
| `symbol` | string | yes | Symbol |
| `seq` | string | yes | Snapshot sequence number |
| `ts_ms` | string | yes | Snapshot timestamp (ms Unix epoch) |
| `bids_json` | string | yes | JSON-encoded `[["price","size"],...]` array |
| `asks_json` | string | yes | JSON-encoded `[["price","size"],...]` array |

---

## Stream configuration

| Parameter | Value |
|-----------|-------|
| `MAXLEN` | `~50000` (approximate trim, configurable via `REDIS_STREAM_MAXLEN`) |
| Consumer group | `candle-{exchange}-{symbol}` (created by candle-service on first connect) |
| ACK timing | Before dispatch (see NOMICON §2) |
| Delivery guarantee | At-least-once within group (exactly-once via in-window dedup) |

---

## Gap deduplication

The consumer uses a Redis ZSET `candle:gap_dedup:{exchange}:{symbol}` to prevent
duplicate gap application when multiple consumer instances read the same gap message
(e.g., during blue-green overlap). Key: `seqBefore:seqAfter:cause`. Max 10,000 entries.

---

## Version compatibility

The consumer skips unknown `type` values with a WARN log. New message types can be
added by the producer without breaking the consumer. Removal of an existing type is a
breaking change.

---

## Example entries

```
# XREAD ticks:kucoin:BTC-USDT 0 COUNT 3

# Trade: buy 0.001 BTC at 29500.50
{type: "tick", price: "29500.50", size: "0.001", side: "buy", level: "0", ts_ms: "1716278400000", seq: "12345678"}

# L2 OB update: bid at 29499.00 for 0.5 BTC
{type: "tick", price: "29499.00", size: "0.5", side: "buy", level: "1", ts_ms: "1716278400001", seq: "12345679"}

# L2 OB remove: cancel ask at 29501.00
{type: "tick", price: "29501.00", size: "0", side: "sell", level: "1", ts_ms: "1716278400002", seq: "12345680"}

# Gap event
{type: "gap", gap_cause: "external_disconnect", exchange: "kucoin", symbol: "BTC-USDT", seq_before: "12345670", seq_after: "12345678", gap_ts_ms: "1716278399500"}
```
