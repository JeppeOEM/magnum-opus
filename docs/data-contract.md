# Data Contract — magnum-opus

This document is the interface between the data pipeline (aggregator + candle service + AI/ML service)
and any downstream consumer: trading bots, backtesting, ML training, dashboards.

Status of each data source:
- **[LIVE]** — produced now by the Go aggregator
- **[PLANNED]** — defined, not yet built

---

## 1. Redis Streams

All streams use Redis Streams (XADD/XREAD). Consumers use consumer groups for guaranteed delivery.
Stream keys follow the pattern `{type}:{exchange}:{symbol}` or `{type}:{symbol}:{timeframe}`.

### 1.1 Raw ticks — `ticks:{exchange}:{symbol}` **[LIVE]**

One stream per (exchange, symbol) pair. Two entry types share the same stream, discriminated by `type`.

**Tick entry:**

| Field | Type | Example | Notes |
|-------|------|---------|-------|
| `type` | string | `"tick"` | discriminator |
| `exchange` | string | `"kucoin"` | `"kucoin"` or `"bybit"` |
| `symbol` | string | `"BTC-USDT"` | canonical — always hyphenated |
| `seq` | string (uint64) | `"1234567890"` | exchange sequence number |
| `ts_exchange` | string (int64 ms) | `"1715089200000"` | exchange-reported timestamp, Unix ms |
| `ts_local` | string (int64 ms) | `"1715089200012"` | aggregator receipt time, Unix ms |
| `side` | string | `"bid"` | `"bid"`, `"ask"`, or `""` for trades |
| `price` | string | `"62500.50"` | exact wire string — never float |
| `size` | string | `"0.012"` | `"0"` = level removed |
| `event_type` | string | `"update"` | `"update"`, `"snapshot"`, or `"trade"` |

**Gap marker entry:**

| Field | Type | Example | Notes |
|-------|------|---------|-------|
| `type` | string | `"gap"` | discriminator |
| `exchange` | string | `"kucoin"` | |
| `symbol` | string | `"BTC-USDT"` | |
| `gap_ts` | string (int64 ms) | `"1715089200000"` | when the gap was detected, Unix ms |
| `gap_cause` | string | `"external_disconnect"` | see causes below |
| `seq_before` | string (uint64) | `"1234567889"` | last good sequence number |
| `seq_after` | string (uint64) | `"1234567950"` | first sequence number after gap |

**Gap causes (exhaustive):**

| Cause | Producer | Meaning |
|-------|----------|---------|
| `external_disconnect` | Aggregator | Exchange WebSocket disconnected |
| `external_rate_limit` | Aggregator | Exchange signalled rate limiting |
| `internal_buffer_overflow` | Aggregator | Coordinator delta buffer was full |
| `internal_merge_error` | Aggregator | Snapshot arrived but sequence didn't overlap buffered deltas |
| `stream_overflow` | Candle Service | Stream was at MAXLEN when consumer group was created (startup) |
| `cold_start_buffer_overflow` | Candle Service | Cold-start delta buffer full before first snapshot arrived |
| `seq_reset` | Candle Service | Exchange sequence counter reset (detected via heuristic) |
| `snapshot_superseded` | Candle Service | Second snapshot arrived while replaying cold-start buffer |
| `reconstruction_incomplete` | Candle Service | QuestDB rows insufficient to reconstruct cascade bar at startup |

Aggregator-emitted `internal_*` causes indicate a bug or resource exhaustion. `external_*` causes are normal exchange behaviour. Candle Service causes are operational events, not bugs.

**Guarantees:**
- Gap markers are written in-band — a consumer reading the stream sequentially always sees the gap marker before the first tick that follows the gap.
- Duplicate gap markers may appear after a Redis write retry. Consumers must deduplicate on `(seq_before, seq_after, gap_cause)`.
- Stream is trimmed to approximately `REDIS_STREAM_MAXLEN` entries (default 50 000) per stream.

---

### 1.2 Gap log — `gaps:log` **[LIVE]**

Global log of every gap event across all symbols. Same fields as the gap marker above, plus:

| Field | Type | Example | Notes |
|-------|------|---------|-------|
| `seq_gap` | string (int64) | `"60"` | `seq_after - seq_before - 1` — missing sequence count |

Use for alerting and audit. Not intended for per-symbol consumption (use the tick stream for that).

---

### 1.3 Candle streams — `candles:{exchange}:{symbol}:{tf}` **[PLANNED]**

One stream per (exchange, symbol, timeframe). Written by the Go Candle Service when a bar closes.

Timeframes: `1s`, `1m`, `5m`, `15m`, `1h`, `4h`, `1d`, `1w`

| Field | Type | Notes |
|-------|------|-------|
| `ts` | string (int64 ms) | bar open time, Unix ms |
| `exchange` | string | |
| `symbol` | string | canonical |
| `tf` | string | e.g. `"1m"` |
| `open` | string (float) | null on idle bars with no trades |
| `high` | string (float) | null on idle bars with no trades |
| `low` | string (float) | null on idle bars with no trades |
| `close` | string (float) | null on idle bars with no trades |
| `volume` | string (float) | base asset volume; null on idle bars |
| `quote_volume` | string (float) | null on idle bars |
| `trade_count` | string (int) | null on idle bars |
| `is_complete` | string (bool) | `"true"` when bar is closed and will not be updated |
| `is_partial` | string (bool) | `"true"` for mid-second flush on snapshot reinit — valid bar, not an error |

Full 67-field bars include all microstructure fields from the `snapshot_1s` schema.

Weekly bars also published to `candles:1w:{exchange}:{symbol}` as an alias for subscribers that only want weekly data.

---

### 1.4 OB feature snapshots — `ob_features:{exchange}:{symbol}` **[PLANNED]**

1-second order book feature snapshots. Written by the Go Candle Service on each 1s bar close.

| Field | Type | Notes |
|-------|------|-------|
| `ts` | string (int64 ms) | bar close time, Unix ms |
| `exchange` | string | |
| `symbol` | string | canonical |
| `best_bid` | string (float) | |
| `best_ask` | string (float) | |
| `bid_depth_l1` | string (float) | bid depth at L1 |
| `ask_depth_l1` | string (float) | ask depth at L1 |
| `bid_depth_l2` | string (float) | bid depth at L2 |
| `ask_depth_l2` | string (float) | ask depth at L2 |
| `bid_depth_top10` | string (float) | |
| `ask_depth_top10` | string (float) | |
| `bid_depth_total` | string (float) | |
| `ask_depth_total` | string (float) | |
| `ofi` | string (float) | order flow imbalance, full book (Cont et al. 2014) |
| `ofi_l1` | string (float) | order flow imbalance, L1 only |

---

### 1.5 AI/ML signals — `ai:{symbol}:signals` **[PLANNED]**

Published by the Python AI/ML Service after model inference. One entry per inference cycle (typically 1s or on candle close).

| Field | Type | Notes |
|-------|------|-------|
| `ts` | string (int64 ms) | inference time |
| `symbol` | string | canonical |
| `model` | string | model identifier, e.g. `"lgbm_v3"` |
| `direction` | string | `"long"`, `"short"`, or `"neutral"` |
| `confidence` | string (float 0–1) | model output probability |
| `horizon_s` | string (int) | prediction horizon in seconds |
| `features_ts` | string (int64 ms) | timestamp of the features used for inference |

---

### 1.6 Bot commands — `bot:commands` **[PLANNED]**

Published by the Kill Switch service.

| Field | Type | Notes |
|-------|------|-------|
| `command` | string | `"kill_all"` or `"kill"` |
| `target` | string | strategy name, or `"*"` for kill_all |
| `ts` | string (int64 ms) | issued time |
| `reason` | string | human-readable reason |

---

## 2. QuestDB tables

QuestDB is the warm-tier audit store. All tables use `TIMESTAMP(...) PARTITION BY DAY WAL`.
Access via SQL on port 9000. Writes via ILP on port 9009.

### 2.1 `raw_ticks` **[LIVE]**

Complete L2 delta audit trail. One row per tick or gap marker. Written by the Go aggregator.

```sql
CREATE TABLE IF NOT EXISTS raw_ticks (
    exchange    SYMBOL,       -- "kucoin" | "bybit"
    symbol      SYMBOL,       -- canonical, e.g. "BTC-USDT"
    seq         LONG,         -- exchange sequence number
    ts_exchange TIMESTAMP,    -- designated timestamp (partitioning key)
    ts_local    TIMESTAMP,    -- aggregator receipt time
    side        SYMBOL,       -- "bid" | "ask" | ""
    price       STRING,       -- exact wire string
    size        STRING,       -- exact wire string; "0" = level removed
    event_type  SYMBOL,       -- "update" | "snapshot" | "trade"
    is_gap      BOOLEAN,      -- true for gap marker rows
    gap_cause   SYMBOL        -- non-null only when is_gap = true
) TIMESTAMP(ts_exchange) PARTITION BY DAY WAL;
```

**Retention:** indefinite (audit trail). No TTL set.

---

### 2.2 `snapshot_1s` **[PLANNED]**

One row per second per (exchange, symbol). Written by the Python Candle Service.
67 fields. 30-day hot TTL, then flushed to B2 Parquet.

```sql
CREATE TABLE IF NOT EXISTS snapshot_1s (
    -- Identity
    ts               TIMESTAMP,
    exchange         SYMBOL CAPACITY 8   INDEX,
    symbol           SYMBOL CAPACITY 256 INDEX,

    -- OHLCV
    open             DOUBLE,
    high             DOUBLE,
    low              DOUBLE,
    close            DOUBLE,
    volume           DOUBLE,        -- base asset
    quote_volume     DOUBLE,
    trade_count      INT,
    twap             DOUBLE,        -- time-weighted average price

    -- Mid price path
    mid_price_open   DOUBLE,
    mid_price_high   DOUBLE,
    mid_price_low    DOUBLE,
    vwmp             DOUBLE,        -- volume-weighted mid price

    -- Spread
    spread_high      DOUBLE,        -- widest spread in the second
    spread_low       DOUBLE,        -- tightest spread in the second
    spread_mean      DOUBLE,        -- time-weighted average spread
    effective_spread DOUBLE,        -- 2 × |trade_price - mid_at_trade|

    -- OB state at close
    best_bid                  DOUBLE,
    best_ask                  DOUBLE,
    bid_depth_usd_l1          DOUBLE,
    ask_depth_usd_l1          DOUBLE,
    bid_depth_usd_l2          DOUBLE,
    ask_depth_usd_l2          DOUBLE,
    bid_depth_usd_top10       DOUBLE,
    ask_depth_usd_top10       DOUBLE,
    bid_depth_usd_total       DOUBLE,
    ask_depth_usd_total       DOUBLE,

    -- OB state at open (for change-in-depth features)
    bid_depth_usd_top10_open  DOUBLE,
    ask_depth_usd_top10_open  DOUBLE,
    bid_depth_usd_total_open  DOUBLE,
    ask_depth_usd_total_open  DOUBLE,

    -- Book shape (centre of mass)
    weighted_bid_price        DOUBLE,
    weighted_ask_price        DOUBLE,

    -- Market impact
    depth_to_1pct_bid         DOUBLE,   -- USD depth needed to move bid 1%
    depth_to_1pct_ask         DOUBLE,

    -- Order flow imbalance
    ofi                       DOUBLE,   -- full book OFI
    ofi_l1                    DOUBLE,   -- L1-only OFI (divergence = spoofing signal)

    -- Trade flow
    buy_volume                DOUBLE,
    buy_count                 INT,

    -- Block trades (threshold: top 1% by size)
    block_buy_volume          DOUBLE,
    block_sell_volume         DOUBLE,

    -- Trade distribution
    max_trade_size            DOUBLE,
    large_bid_orders          INT,
    large_ask_orders          INT,
    first_trade_offset_ms     INT,      -- ms after second boundary
    last_trade_offset_ms      INT,
    trade_clustering          DOUBLE,   -- Gini coefficient of inter-trade intervals
    max_consecutive_run       INT,      -- longest buy or sell run by trade sign

    -- Volatility
    realized_vol              DOUBLE,   -- std dev of intra-second mid-price returns
    realized_skewness         DOUBLE,
    uptick_count              INT,
    downtick_count            INT,

    -- OB activity
    bid_order_arrivals        INT,
    ask_order_arrivals        INT,
    bid_cancel_count          INT,
    ask_cancel_count          INT,
    ob_modify_count           INT,
    avg_bid_order_size        DOUBLE,
    avg_ask_order_size        DOUBLE,
    best_bid_changes          INT,
    best_ask_changes          INT,
    quote_stuff_ratio         DOUBLE,   -- (arrivals + cancels) / trade_count

    -- Trade microstructure
    trade_sign_autocorr       DOUBLE,   -- order-splitting detection
    inter_trade_interval_std_ms DOUBLE,
    num_trade_price_levels    INT,      -- distinct prices traded (sweep vs stable)

    -- Quality
    gap_count                 INT,      -- number of sequence gaps in this second
    bar_count                 INT       -- number of L2 updates processed
) TIMESTAMP(ts) PARTITION BY DAY TTL 30d;
```

**Write rate:** ~400 rows/sec (200 symbols × 2 exchanges)
**Retention:** 30 days live in QuestDB, then flushed to B2 Parquet (Hive-partitioned, zstd)

**Derivable at query or training time — do NOT store:**

| Derivable field | Formula |
|-----------------|---------|
| `vwap` | `quote_volume / volume` |
| `mid_price` | `(best_bid + best_ask) / 2` |
| `spread_abs` | `best_ask - best_bid` |
| `spread_bps` | `spread_abs * 10000 / mid_price` |
| `sell_volume` | `volume - buy_volume` |
| `sell_count` | `trade_count - buy_count` |
| `imbalance_top10` | `(bid_top10 - ask_top10) / (bid_top10 + ask_top10)` |
| `has_gap` | `gap_count > 0` |
| `ofi_delta` | `LAG(ofi, 1)` |
| Rolling z-scores / percentiles | compute in Python at signal/training time |

---

## 3. Bot strategy interface **[PLANNED]**

Python. One file per strategy in `strategies/active/` or `strategies/inactive/`. The Bot Manager file-watches both directories.

```python
from abc import ABC, abstractmethod
from typing import ClassVar

class BaseStrategy(ABC):
    # --- Class-level configuration (set in subclass body) ---
    enabled: ClassVar[bool] = True
    use_ai_signals: ClassVar[bool] = False

    # Required: override these
    symbols: ClassVar[list[str]]       # canonical symbols, e.g. ["BTC-USDT"]
    timeframe: ClassVar[str]           # e.g. "1m"
    stop_loss_pct: ClassVar[float]     # e.g. 0.02 = 2%
    max_position_pct: ClassVar[float]  # fraction of portfolio, e.g. 0.10 = 10%

    @abstractmethod
    def on_candle(self, candle: dict) -> None:
        """
        Called on every closed candle for each subscribed symbol.

        candle keys match snapshot_1s field names plus:
          - symbol: str
          - exchange: str
          - ts: int  (Unix ms)
          - is_complete: bool

        Place orders via self.buy() / self.sell() / self.close().
        """
        ...

    # Optionally override for tick-level or signal-level processing
    def on_tick(self, tick: dict) -> None: ...
    def on_signal(self, signal: dict) -> None: ...  # only called if use_ai_signals = True

    # --- Provided by framework, call from on_candle ---
    def buy(self, symbol: str, size: float, order_type: str = "market") -> None: ...
    def sell(self, symbol: str, size: float, order_type: str = "market") -> None: ...
    def close(self, symbol: str) -> None: ...
    def position(self, symbol: str) -> float: ...  # current position size, negative = short
```

**Security:** the Bot Manager AST-scans every strategy file before loading. Any import of `os`, `subprocess`, `socket`, `sys`, or `ctypes` is rejected and the file is moved to `strategies/inactive/`.

---

## 4. Prometheus metrics exposed by aggregator **[LIVE]**

Available at `http://aggregator:8080/metrics`. Useful for bot health monitoring.

| Metric | Labels | Meaning |
|--------|--------|---------|
| `aggregator_feed_state` | `exchange`, `symbol` | `1` = live, `0` = not live |
| `aggregator_ticks_total` | `exchange`, `symbol` | cumulative ticks through pipeline |
| `aggregator_gap_total` | `exchange`, `symbol`, `cause` | cumulative gaps by cause |
| `aggregator_questdb_write_latency_ms` | — | ILP batch write latency histogram |

All series are pre-initialized at startup — a value of `0` always means zero, never missing data.

---

## 5. What does NOT exist yet

To be clear about gaps before building bots:

| Missing | Blocking what |
|---------|--------------|
| Go Candle Service _(in development)_ | `snapshot_1s` table, candle streams, ob_features stream |
| AI/ML Service | `ai:{symbol}:signals` stream |
| Bot Manager | strategy lifecycle, kill switch integration |
| Kill Switch service | `bot:commands` stream, `/kill` HTTP endpoint |
| `gap_count_24h` in `/health` | not wired — always 0 until gapwindow is connected to coordinator |

The Go aggregator is complete and producing `ticks:{exchange}:{symbol}`, `gaps:log`, and `raw_ticks`. Everything else is upstream of the bots but not yet built.
