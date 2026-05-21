# Chapter 08 — Data Contracts

**30-second summary:** This chapter documents every Redis key pattern, stream field
schema, QuestDB table schema, and ILP wire format used in the system. It is the
authoritative reference for what flows between services.

---

## 1. Redis Key Taxonomy

### 1.1 Streams (durable, XREADGROUP)

| Key pattern | Producer | Consumer |
|-------------|----------|---------|
| `ticks:{exchange}:{symbol}` | Aggregator | Candle service |
| `candles:close:{exchange}:{symbol}:{tf}` | Candle service | Bot service |
| `candles:ob:{exchange}:{symbol}` | Candle service | Bot service |

`{exchange}` = `kucoin` or `bybit`.
`{symbol}` = exchange symbol format, e.g., `BTC-USDT`.
`{tf}` = `1s`, `1m`, `5m`, `15m`, `1h`, `4h`, `1d`, `1w`.

### 1.2 Pub/Sub Channels (transient, PUBLISH/SUBSCRIBE)

| Channel pattern | Publisher | Subscribers |
|----------------|-----------|-------------|
| `orderbook:{exchange}:{symbol}` | Aggregator | Gateway, Bot service |
| `candles1s:{exchange}:{symbol}` | Candle service | Gateway |

### 1.3 Sorted Sets (heatmap)

| Key pattern | Score | Member |
|-------------|-------|--------|
| `heatmap:{exchange}:{symbol}` | Price (float) | `{ts}:{bid_vol}:{ask_vol}` |

---

## 2. Stream: `ticks:{exchange}:{symbol}`

Written by the aggregator's Redis stream writer at
[`aggregator/internal/writer/redis/writer.go`](../../aggregator/internal/writer/redis/writer.go).

| Field | Type | Description |
|-------|------|-------------|
| `ts` | string (int64 ms) | Exchange timestamp |
| `seq` | string (uint64) | Exchange sequence number |
| `feed_type` | string | `trade` or `quote` |
| `event_type` | string | `snapshot` or `update` |
| `price` | string (float64) | Trade price (trade ticks only) |
| `size` | string (float64) | Trade size (trade ticks only) |
| `side` | string | `buy` or `sell` (trade ticks only) |
| `bids` | string (JSON) | `[[price, size], ...]` — delta levels |
| `asks` | string (JSON) | `[[price, size], ...]` — delta levels |
| `best_bid` | string (float64) | Current best bid after apply |
| `best_ask` | string (float64) | Current best ask after apply |

A level with `size=0` is a deletion from the book.

---

## 3. Stream: `candles:close:{exchange}:{symbol}:{tf}`

Written by the candle service at
[`candle-service/internal/writer/redis/publisher.go`](../../candle-service/internal/writer/redis/publisher.go).

For `1s` bars, every field from the 67-feature `Bar` struct is included.
For cascade bars (`1m` through `1w`), only OHLCV + trade stats are included.

**1s bar fields** (subset — see [accumulator.go](../../candle-service/internal/accumulator/accumulator.go) for full list):

| Field | Type | Description |
|-------|------|-------------|
| `ts` | string (int64 ms) | Second boundary (Unix ms) |
| `exchange` | string | Exchange name |
| `symbol` | string | Symbol |
| `open` | string (float64) or `""` | First trade price |
| `high` | string (float64) or `""` | Highest trade price |
| `low` | string (float64) or `""` | Lowest trade price |
| `close` | string (float64) or `""` | Last trade price |
| `volume` | string (float64) | Total trade volume (base) |
| `quote_volume` | string (float64) | Total trade volume (quote) |
| `trade_count` | string (int) | Number of trades |
| `buy_volume` | string (float64) | Volume on buy side |
| `sell_volume` | string (float64) | Volume on sell side |
| `best_bid` | string (float64) | Last known best bid |
| `best_ask` | string (float64) | Last known best ask |
| `ofi` | string (float64) | Order flow imbalance |
| `realized_vol` | string (float64) | Std-dev of mid-price returns |
| `footprint_json` | string (JSON) | `{price: {bid_vol, ask_vol}}` |
| `absorption_detected` | string (`0`/`1`) | Absorption signal boolean |
| ... | | (all other accumulator.Bar fields) |

**Cascade bar fields:**

| Field | Type |
|-------|------|
| `ts` | string (int64 ms) — open timestamp |
| `tf` | string — timeframe |
| `open`, `high`, `low`, `close` | string (float64) |
| `volume`, `quote_volume` | string (float64) |
| `trade_count` | string (int) |
| `bar_count` | string (int) — 1s bars in this bar |
| `gap_count` | string (int) — empty 1s bars |
| `buy_volume`, `sell_volume` | string (float64) |

---

## 4. Stream: `candles:ob:{exchange}:{symbol}`

OB feature snapshot, one entry per 1s flush. Used by bot strategies that need depth
data on the bar boundary rather than real-time.

| Field | Type | Description |
|-------|------|-------------|
| `ts` | string (int64 ms) | Bar timestamp |
| `bid_depth_l1` | string (float64) | Best bid size |
| `ask_depth_l1` | string (float64) | Best ask size |
| `bid_depth_l2` | string (float64) | Top-2 bid cumulative |
| `ask_depth_l2` | string (float64) | Top-2 ask cumulative |
| `bid_depth_top10` | string (float64) | Top-10 bid cumulative |
| `ask_depth_top10` | string (float64) | Top-10 ask cumulative |
| `bid_depth_total` | string (float64) | Full book bid depth |
| `ask_depth_total` | string (float64) | Full book ask depth |
| `weighted_bid_price` | string (float64) | VWAP of book bids |
| `weighted_ask_price` | string (float64) | VWAP of book asks |
| `ofi` | string (float64) | Order flow imbalance |

---

## 5. Pub/Sub: `orderbook:{exchange}:{symbol}`

JSON payload published after each OB-mutating tick. The gateway encodes this to
binary before forwarding to WebSocket clients.

```json
{
  "ts": 1716100000000,
  "exchange": "kucoin",
  "symbol": "BTC-USDT",
  "bids": [["67500.00", "1.234"], ["67499.50", "0.500"], ...],
  "asks": [["67500.50", "0.800"], ["67501.00", "2.100"], ...]
}
```

`bids` and `asks` are top-N levels (N = `OB_DEPTH` env var, default 20), sorted
best-first.

---

## 6. Pub/Sub: `candles1s:{exchange}:{symbol}`

JSON payload for the real-time 1s partial bar (published every 250ms mid-second +
on bar completion). The gateway injects `"type": "candles1s"` and forwards as a
text WebSocket frame.

```json
{
  "type": "candles1s",
  "ts": 1716100001000,
  "exchange": "kucoin",
  "symbol": "BTC-USDT",
  "open": 67500.0,
  "high": 67520.0,
  "low": 67495.0,
  "close": 67510.0,
  "volume": 0.45,
  "buy_volume": 0.30,
  "sell_volume": 0.15,
  "trade_count": 12,
  "ofi": 0.15,
  "is_partial": true
}
```

---

## 7. QuestDB Tables

### 7.1 `ticks`

Written by the aggregator.

```sql
CREATE TABLE ticks (
    ts TIMESTAMP,
    exchange SYMBOL,
    symbol SYMBOL,
    feed_type SYMBOL,
    event_type SYMBOL,
    price DOUBLE,
    size DOUBLE,
    side SYMBOL,
    seq LONG,
    best_bid DOUBLE,
    best_ask DOUBLE
) TIMESTAMP(ts) PARTITION BY DAY;
```

### 7.2 `snapshot_1s`

Written by the candle service. 67 columns — the full feature set.

```sql
CREATE TABLE snapshot_1s (
    ts TIMESTAMP,
    exchange SYMBOL,
    symbol SYMBOL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    quote_volume DOUBLE,
    trade_count INT,
    twap DOUBLE,
    buy_volume DOUBLE,
    sell_volume DOUBLE,
    buy_trade_count INT,
    sell_trade_count INT,
    best_bid_open DOUBLE,
    best_ask_open DOUBLE,
    best_bid DOUBLE,
    best_ask DOUBLE,
    mid_price_open DOUBLE,
    mid_price_high DOUBLE,
    mid_price_low DOUBLE,
    vwmp DOUBLE,
    spread_high DOUBLE,
    spread_low DOUBLE,
    spread_mean DOUBLE,
    effective_spread DOUBLE,
    bid_depth_l1_open DOUBLE,
    ask_depth_l1_open DOUBLE,
    bid_depth_l2_open DOUBLE,
    ask_depth_l2_open DOUBLE,
    bid_depth_top10_open DOUBLE,
    ask_depth_top10_open DOUBLE,
    bid_depth_total_open DOUBLE,
    ask_depth_total_open DOUBLE,
    bid_depth_l1_close DOUBLE,
    ask_depth_l1_close DOUBLE,
    bid_depth_l2_close DOUBLE,
    ask_depth_l2_close DOUBLE,
    bid_depth_top10_close DOUBLE,
    ask_depth_top10_close DOUBLE,
    bid_depth_total_close DOUBLE,
    ask_depth_total_close DOUBLE,
    weighted_bid_price DOUBLE,
    weighted_ask_price DOUBLE,
    ofi DOUBLE,
    ofi_cumulative DOUBLE,
    ofi_per_trade DOUBLE,
    quote_stuff_ratio DOUBLE,
    max_consecutive_run INT,
    poc_price DOUBLE,
    value_area_high DOUBLE,
    value_area_low DOUBLE,
    footprint_json STRING,
    single_print_levels_json STRING,
    absorption_detected BOOLEAN,
    iceberg_detected BOOLEAN,
    realized_vol DOUBLE,
    price_divergence DOUBLE,
    volume_divergence DOUBLE
) TIMESTAMP(ts) PARTITION BY DAY;
```

### 7.3 `snapshot_1m` and `snapshot_15m`

OHLCV + trade stats only (no microstructure features):

```sql
CREATE TABLE snapshot_1m (
    ts TIMESTAMP,
    exchange SYMBOL,
    symbol SYMBOL,
    tf SYMBOL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    quote_volume DOUBLE,
    trade_count INT,
    bar_count INT,
    gap_count INT,
    buy_volume DOUBLE,
    sell_volume DOUBLE
) TIMESTAMP(ts) PARTITION BY WEEK;
```

### 7.4 `order_events`

Written by the bot service `OrderQueueWorker`.

```sql
CREATE TABLE order_events (
    ts TIMESTAMP,
    order_id SYMBOL,
    client_order_id SYMBOL,
    strategy SYMBOL,
    exchange SYMBOL,
    symbol SYMBOL,
    side SYMBOL,
    order_type SYMBOL,
    status SYMBOL,          -- placed|filled|rejected|cancelled|failed
    limit_price DOUBLE,
    stop_price DOUBLE,
    take_profit_price DOUBLE,
    requested_size DOUBLE,
    filled_size DOUBLE,
    remaining_size DOUBLE,
    avg_fill_price DOUBLE,
    fee DOUBLE,
    realized_pnl DOUBLE,
    slippage DOUBLE,
    position_size_after DOUBLE,
    paper_trading BOOLEAN,
    backtest BOOLEAN,
    ts_placed TIMESTAMP,    -- when order was sent to exchange
    ts_exchange TIMESTAMP   -- exchange-confirmed timestamp
) TIMESTAMP(ts) PARTITION BY DAY;
```

---

## 8. QuestDB SQL Query Patterns

### Fetch last 500 1s candles

```sql
SELECT ts, open, high, low, close, volume, trade_count, ofi, absorption_detected
FROM snapshot_1s
WHERE exchange = 'kucoin' AND symbol = 'BTC-USDT'
ORDER BY ts DESC
LIMIT 500
```

### Fetch new candles since timestamp

```sql
SELECT * FROM snapshot_1s
WHERE exchange = 'kucoin'
  AND symbol = 'BTC-USDT'
  AND ts > '2024-05-20T10:30:00.000000Z'
ORDER BY ts ASC
```

### Open orders for reconciliation

```sql
SELECT order_id, strategy, exchange, symbol, side, order_type, limit_price, requested_size, ts_placed
FROM order_events
WHERE strategy = 'MyStrategy'
  AND status NOT IN ('filled', 'cancelled', 'rejected', 'failed')
ORDER BY ts DESC
```

### Daily PnL by strategy

```sql
SELECT
    strategy,
    datetrunc('day', ts) AS day,
    sum(realized_pnl) AS daily_pnl,
    count() AS fills
FROM order_events
WHERE status = 'filled' AND backtest = false
GROUP BY strategy, day
ORDER BY day DESC
```

---

## 9. ILP Wire Format

QuestDB ILP is InfluxDB Line Protocol over TCP port 9009:

```
table_name,tag1=val1,tag2=val2 field1=1.23,field2="str" timestamp_ns
```

Example tick row:
```
ticks,exchange=kucoin,symbol=BTC-USDT,feed_type=trade,side=buy \
  price=67500.0,size=0.123,seq=1234567890,best_bid=67499.5,best_ask=67500.5 \
  1716100000000000000
```

Tags (SYMBOL columns in QuestDB) are indexed for fast filtering.
Fields (DOUBLE, LONG, BOOLEAN, STRING) are the data columns.
Timestamp is nanoseconds since Unix epoch.

---

## 10. Field Definitions and Semantics

Critical fields with non-obvious semantics:

| Field | Correct formula | Note |
|-------|----------------|------|
| `quote_stuff_ratio` | `(arrivals + cancels) / trade_count` | Ratio of non-trade order activity to actual trades |
| `realized_vol` | Std-dev of mid-price **returns** (not price levels) | Mid = (best_bid + best_ask) / 2 |
| `max_consecutive_run` | Max run of same-side **trades** | Not price direction — sign of trade (buy/sell) |
| `ofi` | `Σ (bid_size_change_if_bid_improved - ask_size_change_if_ask_improved)` | Standard OFI formula |
| `effective_spread` | `2 × |trade_price - mid_price|` averaged over all trades | Measures transaction cost |
| `vwmp` | `Σ (mid_price × trade_size) / Σ trade_size` | Volume-weighted mid price |
