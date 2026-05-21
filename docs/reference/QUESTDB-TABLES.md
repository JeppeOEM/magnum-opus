# QuestDB Tables Reference

Schema and query patterns for all QuestDB tables. Each table uses:
- `TIMESTAMP(ts)` — primary timestamp for partitioning and ordering
- `PARTITION BY DAY` — one partition per calendar day
- `WAL` — write-ahead log for durability
- `DEDUP UPSERT KEYS(ts, exchange, symbol)` — enables partial-bar overwrites

---

## snapshot_1s

**Purpose:** 1-second OHLCV bars with 67 microstructure features.
**Migration:** `candle-service/migrations/001_snapshot_1s.sql`
**TTL:** 30 days
**Writer:** `candle-service/internal/writer/questdb/writer.go`

### NULL semantics

All `DOUBLE` columns can be NULL when:
- The bar had no trades (trade-only fields like `buy_volume`, `max_trade_size`)
- The feature could not be computed (e.g., `realized_vol` requires ≥2 mid-price observations)
- `is_partial=true` bars may have fewer ticks than a full bar

NULL in `gap_count` means 0 gaps (never NULL in practice; default is 0).

### Column groups

#### Identity (3 columns)

| Column | Type | Description |
|--------|------|-------------|
| `ts` | TIMESTAMP | Bar close time (UTC, nanosecond precision). Primary key component. |
| `exchange` | SYMBOL | Exchange name: `kucoin`, `bybit`. SYMBOL type = dictionary-encoded for efficiency. |
| `symbol` | SYMBOL | Canonical symbol: e.g., `BTC-USDT`, `BTCUSDT`. Normalized by the aggregator. |

#### OHLCV (8 columns)

| Column | Type | Description |
|--------|------|-------------|
| `open` | DOUBLE | First trade price in the second. NULL if no trades. |
| `high` | DOUBLE | Highest trade price. NULL if no trades. |
| `low` | DOUBLE | Lowest trade price. NULL if no trades. |
| `close` | DOUBLE | Last trade price. NULL if no trades. |
| `volume` | DOUBLE | Total base asset volume (sum of trade sizes). |
| `quote_volume` | DOUBLE | Total quote asset volume (sum of price × size). |
| `trade_count` | INT | Number of trades in the second. |
| `twap` | DOUBLE | Time-weighted average price from trade times. NULL if no trades. |

#### Mid-price path (4 columns)

Mid-price = `(best_bid + best_ask) / 2`. Updated on every tick, not just trades.

| Column | Type | Description |
|--------|------|-------------|
| `mid_price_open` | DOUBLE | Mid-price at the start of the second. |
| `mid_price_high` | DOUBLE | Highest mid-price during the second. |
| `mid_price_low` | DOUBLE | Lowest mid-price during the second. |
| `vwmp` | DOUBLE | Volume-weighted average of all mid-price observations in the second. |

**Note:** `mid_price_close` is intentionally absent — derivable as `(best_bid + best_ask) / 2`.

#### Spread (4 columns)

| Column | Type | Description |
|--------|------|-------------|
| `spread_high` | DOUBLE | Maximum quoted spread (`best_ask − best_bid`) during the second. |
| `spread_low` | DOUBLE | Minimum quoted spread. |
| `spread_mean` | DOUBLE | Time-average quoted spread. |
| `effective_spread` | DOUBLE | Average realized spread: `2 × |trade_price − mid_price_at_trade|`. |

#### OB best quotes (4 columns)

| Column | Type | Description |
|--------|------|-------------|
| `best_bid_open` | DOUBLE | Best bid price at bar open. |
| `best_ask_open` | DOUBLE | Best ask price at bar open. |
| `best_bid` | DOUBLE | Best bid price at bar close. |
| `best_ask` | DOUBLE | Best ask price at bar close. |

#### OB depth at open (8 columns)

Snapshot of order book depth at the start of the second.

| Column | Type | Description |
|--------|------|-------------|
| `bid_depth_l1_open` | DOUBLE | Total size at best bid price level only. |
| `ask_depth_l1_open` | DOUBLE | Total size at best ask price level only. |
| `bid_depth_l2_open` | DOUBLE | Total size within 2 levels of best bid. |
| `ask_depth_l2_open` | DOUBLE | Total size within 2 levels of best ask. |
| `bid_depth_top10_open` | DOUBLE | Total size within top 10 bid levels. |
| `ask_depth_top10_open` | DOUBLE | Total size within top 10 ask levels. |
| `bid_depth_total_open` | DOUBLE | Total visible bid-side depth (all levels). |
| `ask_depth_total_open` | DOUBLE | Total visible ask-side depth (all levels). |

#### OB depth at close (8 columns)

Same fields as above, captured at the end of the second.

#### Book shape (2 columns)

| Column | Type | Description |
|--------|------|-------------|
| `weighted_bid_price` | DOUBLE | Size-weighted average price of top 10 bid levels. |
| `weighted_ask_price` | DOUBLE | Size-weighted average price of top 10 ask levels. |

#### Market impact (2 columns)

| Column | Type | Description |
|--------|------|-------------|
| `depth_to_1pct_bid` | DOUBLE | Total bid size available within 1% below mid-price. |
| `depth_to_1pct_ask` | DOUBLE | Total ask size available within 1% above mid-price. |

#### Order flow imbalance (2 columns)

| Column | Type | Description |
|--------|------|-------------|
| `ofi` | DOUBLE | Order flow imbalance (L1): `Σ(ΔBidSizeAtBest − ΔAskSizeAtBest)`. Both columns are L1. |
| `ofi_l1` | DOUBLE | Same as `ofi` (full-book OFI deferred to future epic). |

#### Trade flow (2 columns)

| Column | Type | Description |
|--------|------|-------------|
| `buy_volume` | DOUBLE | Volume in buyer-initiated trades. |
| `buy_count` | INT | Number of buyer-initiated trades. |

**Note:** `sell_volume` is absent from `snapshot_1s`; derivable as `volume − buy_volume`.

#### Block trades (2 columns)

| Column | Type | Description |
|--------|------|-------------|
| `block_buy_volume` | DOUBLE | Buy volume from trades above the block-size threshold. |
| `block_sell_volume` | DOUBLE | Sell volume from trades above the block-size threshold. |

#### Trade distribution (7 columns)

| Column | Type | Description |
|--------|------|-------------|
| `max_trade_size` | DOUBLE | Largest single trade size in the second. |
| `large_bid_orders` | INT | Count of buy trades above 2× average trade size. |
| `large_ask_orders` | INT | Count of sell trades above 2× average trade size. |
| `first_trade_offset_ms` | INT | Milliseconds from bar start to first trade. |
| `last_trade_offset_ms` | INT | Milliseconds from bar start to last trade. Range: [0, 1000]. |
| `trade_clustering` | DOUBLE | Fraction of trades that arrive within 100ms of another trade. Range: [0.0, 1.0]. |
| `max_consecutive_run` | INT | Longest run of consecutive same-direction trades (buy=+1, sell=−1). e.g., 3 means 3 buys in a row. |

#### Volatility (4 columns)

| Column | Type | Description |
|--------|------|-------------|
| `realized_vol` | DOUBLE | Std dev of log mid-price returns within the second (NOT trade returns). NULL if < 2 mid-price changes. |
| `realized_skewness` | DOUBLE | Skewness of mid-price log returns. Positive = right-skewed (more large up moves). |
| `uptick_count` | INT | Number of ticks where mid-price moved up. |
| `downtick_count` | INT | Number of ticks where mid-price moved down. |

#### OB activity (10 columns)

| Column | Type | Description |
|--------|------|-------------|
| `bid_order_arrivals` | INT | New bid price levels added (OBEventAdd on buy side). |
| `ask_order_arrivals` | INT | New ask price levels added (OBEventAdd on sell side). |
| `bid_cancel_count` | INT | Bid levels removed (OBEventCancel on buy side). |
| `ask_cancel_count` | INT | Ask levels removed (OBEventCancel on sell side). |
| `ob_modify_count` | INT | Levels modified (OBEventModify, either side). |
| `avg_bid_order_size` | DOUBLE | Average size of new bid orders arriving in the second. |
| `avg_ask_order_size` | DOUBLE | Average size of new ask orders arriving in the second. |
| `best_bid_changes` | INT | Number of times best bid price changed. |
| `best_ask_changes` | INT | Number of times best ask price changed. |
| `quote_stuff_ratio` | DOUBLE | `(bid_order_arrivals + ask_order_arrivals + bid_cancel_count + ask_cancel_count) / trade_count`. NULL if `trade_count=0`. |

#### Trade microstructure (3 columns)

| Column | Type | Description |
|--------|------|-------------|
| `trade_sign_autocorr` | DOUBLE | Pearson autocorrelation of consecutive trade signs (+1=buy, −1=sell). Range: [−1.0, 1.0]. NULL if < 2 trades. |
| `inter_trade_interval_std_ms` | DOUBLE | Std dev of milliseconds between consecutive trades. NULL if < 2 trades. |
| `num_trade_price_levels` | INT | Number of distinct price levels where trades executed. |

#### Quality (3 columns)

| Column | Type | Description |
|--------|------|-------------|
| `is_partial` | BOOLEAN | `true` for intra-second partial bars published every 250ms. Overwritten by the complete bar via DEDUP. |
| `gap_count` | INT | Number of gap events that occurred during this second. Non-zero = data may be incomplete. |
| `bar_count` | INT | Incremental bar sequence counter per (exchange, symbol). Used to detect missed bars. |

### Example queries

```sql
-- Last 100 complete 1-second bars for BTC-USDT on KuCoin
SELECT ts, open, high, low, close, volume, ofi, realized_vol, gap_count
FROM snapshot_1s
WHERE exchange = 'kucoin' AND symbol = 'BTC-USDT'
  AND is_partial = false
ORDER BY ts DESC
LIMIT 100;

-- Average spread by hour for last 7 days
SELECT date_trunc('hour', ts) AS hour, avg(spread_mean) AS avg_spread
FROM snapshot_1s
WHERE symbol = 'BTCUSDT' AND exchange = 'bybit'
  AND ts > dateadd('d', -7, now())
  AND is_partial = false
GROUP BY hour
ORDER BY hour DESC;

-- Bars with gaps in last 24 hours
SELECT ts, exchange, symbol, gap_count
FROM snapshot_1s
WHERE gap_count > 0
  AND ts > dateadd('d', -1, now())
ORDER BY ts DESC;
```

---

## snapshot_1m

**Purpose:** 1-minute bars. Aggregated by candle-service cascade from `snapshot_1s`.
Also serves 5-minute bars (bot-service aggregates on-demand from 1m data).
**Migration:** `candle-service/migrations/025_snapshot_1m.sql`
**TTL:** 365 days
**Extra columns vs snapshot_1s:** `sell_volume`, full footprint set (18 columns), CVD (2), iceberg (3).

### Additional columns (not in snapshot_1s)

#### Trade flow (adds sell_volume)

| Column | Type | Description |
|--------|------|-------------|
| `sell_volume` | DOUBLE | Volume in seller-initiated trades (explicit in 1m; derivable as `volume−buy_volume` in 1s). |

#### Footprint (18 columns)

| Column | Type | Description |
|--------|------|-------------|
| `footprint_json` | VARCHAR | JSON array of `{price, bid_vol, ask_vol}` objects, one per price level. |
| `poc_price` | DOUBLE | Point of Control: price level with highest traded volume. |
| `value_area_high` | DOUBLE | Upper bound of the 70% volume value area. |
| `value_area_low` | DOUBLE | Lower bound of the 70% volume value area. |
| `poc_volume` | DOUBLE | Total traded volume at the POC price level. |
| `imbalance_buy_count` | INT | Number of price levels where buy volume exceeds sell by threshold. |
| `imbalance_sell_count` | INT | Number of price levels where sell volume exceeds buy by threshold. |
| `imbalance_stack_buy` | INT | Consecutive stacked bid imbalances from POC upward. |
| `imbalance_stack_sell` | INT | Consecutive stacked ask imbalances from POC downward. |
| `imbalance_ratio` | DOUBLE | `max(buy_imbalance_vol / sell_imbalance_vol, sell/buy)`. Range: [1.0, ∞). |
| `single_print_count` | INT | Number of price levels touched by trades from only one side. |
| `single_print_levels_json` | VARCHAR | JSON array of price strings that are single-print levels. |
| `unfinished_top` | BOOLEAN | True if the bar's highest price level has one-sided volume (unanswered initiative). |
| `unfinished_bottom` | BOOLEAN | True if the bar's lowest price level has one-sided volume. |
| `absorption_detected` | BOOLEAN | True when high one-sided volume fails to move price (absorption pattern). |
| `footprint_delta_divergence` | BYTE | Directional signal: +1 (price up, delta down), −1 (price down, delta up), 0 (no divergence). |

#### CVD (2 columns)

| Column | Type | Description |
|--------|------|-------------|
| `cum_delta` | DOUBLE | Cumulative volume delta: running sum of `buy_volume − sell_volume` within bar. |
| `cvd_divergence` | BYTE | Signal byte: +1 (price ↑, CVD ↓), −1 (price ↓, CVD ↑), 0 (no divergence). |

#### Iceberg detection (3 columns)

| Column | Type | Description |
|--------|------|-------------|
| `iceberg_bid_detected` | BOOLEAN | True when hidden bid order is inferred (repeated same-price refills). |
| `iceberg_ask_detected` | BOOLEAN | True when hidden ask order is inferred. |
| `iceberg_price` | DOUBLE | Price level at which iceberg activity was detected. NULL if none detected. |

---

## snapshot_15m

**Purpose:** 15-minute bars. Aggregated by candle-service cascade from `snapshot_1m`.
Also serves 1h, 4h, 1d, 1w bars (bot-service aggregates on-demand).
**Migration:** `candle-service/migrations/026_snapshot_15m.sql`
**TTL:** 1825 days (5 years)
**Schema:** Identical to `snapshot_1m` (same columns, longer TTL).

---

## flush_manifest

**Purpose:** Tracks daily B2 flush completion. Prevents duplicate flushes on restart.
**Migration:** `candle-service/migrations/002_flush_manifest.sql`

Maintained by `candle-service/internal/flusher/flusher.go`. The Redis key
`candle:last_flush_date` (string, format `YYYY-MM-DD`) is the primary flush
state; this table is secondary for observability.

---

## Partitioning and TTL

| Table | TTL | Partition size | Notes |
|-------|-----|---------------|-------|
| `snapshot_1s` | 30 days | 1 day | Daily Parquet export to B2 before deletion |
| `snapshot_1m` | 365 days | 1 day | ~525,600 rows/year per (exchange, symbol) |
| `snapshot_15m` | 5 years | 1 day | ~35,040 rows/year per (exchange, symbol) |

**WAL and DEDUP:** All tables use QuestDB WAL mode. `DEDUP UPSERT KEYS(ts, exchange, symbol)` means that a second write with the same `(ts, exchange, symbol)` updates the existing row. This enables the partial-bar pattern: a partial bar written at 250ms intervals is overwritten by the complete bar at 1000ms without creating duplicate rows.
