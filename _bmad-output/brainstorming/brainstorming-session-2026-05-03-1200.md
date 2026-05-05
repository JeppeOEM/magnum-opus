---
stepsCompleted: [1, 2, 3, 4a, 4b-phase1]
inputDocuments: []
session_topic: 'Containerized cryptocurrency order book aggregation service in Go with CD'
session_goals: 'Design a correct, always-on always-on multi-service system capturing crypto order book data, computing technical and AI indicators, delivering signals to multiple trading strategy bots and a frontend analytics dashboard'
selected_approach: 'user-selected + ai-recommended-extension'
techniques_used: ['Solution Matrix', 'Decision Tree Mapping', 'Assumption Reversal', 'First Principles', 'SCAMPER', 'Fusion Cuisine', 'Field Decision Tree', 'Gap Analysis', 'Correlation Cluster Analysis', 'Reverse Brainstorming', 'Failure Analysis', 'Chaos Engineering']
ideas_generated: [12, 27, 35]
context_file: ''
---

# Brainstorming Session Results

**Facilitator:** mrqdt
**Date:** 2026-05-03

## Session Overview

**Topic:** Containerized cryptocurrency order book aggregation service in Go with continuous deployment
**Goals:** Design a correct, always-on service capturing and aggregating crypto order book data into Parquet files for AI and analytics consumers

### Session Setup

Primary concern: **data correctness** — every design decision to be evaluated against whether it preserves the integrity and completeness of order book data.

## Technique Selection

**Approach:** User-Selected Techniques
**Selected Techniques:**

- **Solution Matrix** — systematic grid of design choices × correctness criteria to decide *what* to build
- **Decision Tree Mapping** — stress-test the chosen design against all failure paths to validate *how* it behaves under real conditions

**Selection Rationale:** Run Solution Matrix first to make explicit, criteria-driven architecture decisions; then Decision Tree to enumerate every failure mode in the chosen design and ensure no silent data corruption paths exist.

---

## Solution Matrix — Complete

### Evaluation Criteria (Columns)

| Criterion | Definition |
|---|---|
| **Storage cost** | $/GB stored + $/GB egress |
| **Write cost** | Ingestion fees, API call costs |
| **Gap detectability** | Can the system know when it missed ticks/sequence numbers? |
| **Reconnect correctness** | After disconnect, does first committed record reflect true state? |
| **Write atomicity** | No partial files/records consumed as complete? |
| **Replay capability** | Can full state be re-derived from stored data alone? |
| **AI/query readiness** | How much transform work before useful downstream? |
| **Operational complexity** | What breaks at 3am and how hard to fix? |

---

### Decision 1 — Live Indicator Delivery to Trading Bot

**Winner: Redis Streams (XREAD)**

| Option | Latency | Persistence | Correctness | Cost |
|---|---|---|---|---|
| Redis Streams | <2ms | Yes | Excellent — bot can replay missed ticks | Near zero |
| Redis pub/sub | <1ms | No | Poor — miss a message if offline | Near zero |
| DB polling | 10–50ms | Yes | Good | Low |

**Rationale:** Trading bot needs persistence (can't miss a signal), consumer groups (multiple bot instances), and sub-millisecond latency. Streams over pub/sub for correctness.

---

### Decision 2 — Live Order Book for Frontend

**Winner: Removed from aggregator scope**

Frontend connects to:
- Indicator Redis Stream for live signals (already exists)
- QuestDB REST API for historical charts
- Exchange REST API directly for order book snapshots if needed

**Rationale:** Aggregator's single job is correct data capture. Frontend order book streaming can be added later as a separate WebSocket gateway reading the existing Redis Stream — zero changes to aggregator.

---

### Decision 3 — Indicator Computation Location

**Winner: Separate Python indicator service**

| Option | Latency | Separation | Restartability | ML/AI fit |
|---|---|---|---|---|
| Inside Go aggregator | Fastest | Poor — monolith risk | Coupled | Poor |
| Separate Python service | +2–3ms | Excellent | Independent | Native |
| Inside each trading bot | Fastest | Good | Poor — loses window state | Poor |
| Go sidecar | Near-zero | Good | Partially coupled | Poor |

**Rationale:** Multiple strategies on different timeframes make shared indicator computation essential — no redundant RSI/MACD across N bots. Python is native for DataFrames, pandas-ta, and ML inference. 2–3ms Redis hop is irrelevant at 500ms decision cycle.

---

### Decision 4 — Indicator Service Language and Stack

**Winner: Python with pandas-ta + Redis asyncio**

| Library | Purpose |
|---|---|
| `pandas-ta` | 130+ technical indicators, DataFrame-native |
| `polars` | Fast rolling window management |
| `redis-py` (asyncio) | Redis Streams consumer/producer |
| `pytorch` / `onnxruntime` | ML model inference |
| `scikit-learn` | Classical ML signals |

**Rationale:** Go handles reliability and concurrency. Python handles intelligence and ML. Natural boundary at the Redis Stream interface.

---

### Decision 5 — Multi-Timeframe Indicator Architecture

**Winner: Cascading candle aggregation with fan-out per timeframe**

```
Raw tick → 1s bar → 1m bar → 5m bar → 15m bar → 1h bar
              │         │        │         │        │
           publish   publish  publish   publish  publish
           to Redis  to Redis  ...
```

Each strategy subscribes to the timeframe channel it needs. Zero redundant computation across strategies.

---

### Decision 6 — Warm Tier (Historical + Indicator Queries)

**Winner: QuestDB (self-hosted, OSS)**

| Option | Cost | Time-series query speed | Financial data support | Ops |
|---|---|---|---|---|
| **QuestDB** | Free (OSS) | Extremely fast | LATEST BY, SAMPLE BY, OHLCV native | Very low |
| TimescaleDB | Free (OSS) | Fast | Continuous aggregates, full Postgres | Medium |
| ClickHouse | Free (OSS) | Extremely fast | Good | Medium–High |

**Rationale:** No need for full SQL compatibility. QuestDB's SAMPLE BY and LATEST BY are purpose-built for order book and OHLCV queries. Free, self-hosted, low ops overhead.

**Rolling window recovery:** On Python service restart, query QuestDB for last 60 days, rebuild DataFrames once, then append live ticks. Correct restart recovery is automatic.

---

### Decision 7 — Cold Storage (ML Training + Backtesting)

**Winner: Parquet on Backblaze B2**

| Option | Cost/GB | ML readiness | Notes |
|---|---|---|---|
| Backblaze B2 | $0.006 | Native (pandas, polars, PyTorch) | Cheapest reputable S3-compatible |
| Cloudflare R2 | $0.015 | Native | Zero egress cost, pricier storage |
| Hetzner Object Storage | ~$0.007 | Native | Good EU option |

**Rationale:** Python ML libraries read Parquet natively. QuestDB exports to Parquet directly. Cold storage is read rarely — cost per GB dominates, not query latency.

---

### Final Architecture

```
Exchange WebSockets
        │
        ▼
Go Aggregation Service          ← one job: correct data capture
  └─► Redis Stream: raw ticks   → Python Indicator Service
  └─► QuestDB (async writes)    → 60-day history, ML export
        │
        ▼
Python Indicator Service        ← one job: compute signals
  └─ rolling DataFrames (polars/pandas)
  └─ pandas-ta indicators per timeframe
  └─ ML model inference (PyTorch/ONNX)
  └─► Redis Stream: indicators:SYMBOL:1m   → Strategy Bot A
  └─► Redis Stream: indicators:SYMBOL:5m   → Strategy Bot B
  └─► Redis Stream: indicators:SYMBOL:15m  → Strategy Bot C
  └─► Redis Stream: indicators:SYMBOL:1h   → Strategy Bot D

QuestDB REST API                → Frontend historical charts
Backblaze B2 (Parquet)         → ML training jobs, backtesting
```

### Cost Profile

| Component | Technology | Cost |
|---|---|---|
| Live signal delivery | Redis (self-hosted) | ~$0 |
| Warm time-series | QuestDB (self-hosted) | ~$0 + VM |
| Cold ML storage | Parquet on B2 | ~$0.006/GB |
| Computation | Go + Python services | VM cost only |

**Total storage cost:** effectively just VM hosting + ~$0.006/GB on B2.

---

## Additional Decisions (post-matrix refinement)

### Decision 8 — Candle Aggregation vs Indicator Service Split

**Winner: Separate Candle Service + Per-bot technical indicators + Shared AI/ML service**

- Python Candle Service: aggregates raw ticks → OHLCV per timeframe → Redis Streams
- Each bot: subscribes to candle stream, computes own technical indicators (RSI, MACD etc.) with own parameters in-process
- Shared AI/ML Service: expensive model inference computed once, published to `ai:SYMBOL:signals`
- Rationale: Infinite parameter combinations (RSI(7) vs RSI(21)) make shared indicator computation impractical. Candle aggregation (not indicator computation) is the right shared primitive.

### Decision 9 — Bot Strategy Definition

**Winner: Single Python file per strategy, no YAML**

- Each strategy is a Python class inheriting `BaseStrategy`
- Required interface enforced: `symbols`, `timeframe`, `stop_loss_pct`, `max_position_pct`, `on_candle()`
- All indicator params (RSI length, overbought etc.) defined as class constants in `.py` file
- No YAML — one file is the complete truth for a strategy

### Decision 10 — Bot Management

**Winner: SSH + filesystem control plane**

- `strategies/active/` — watchdog spawns bots for every `.py` file here
- `strategies/inactive/` — move file here to stop bots
- Upload via `scp`, start via copy to `active/`, stop via `mv` to `inactive/`
- AST import scanner validates every file before spawning (blocks `os`, `subprocess`, `socket` etc.)
- Redis registry persists running state across restarts
- No REST API for management

### Decision 11 — Remote Kill Switch

**Winner: Minimal separate service, two endpoints only**

- `POST /kill/all` — stops all bots
- `POST /kill/{name}` — stops individual bot
- Bearer token auth, rate limited to 5 req/min
- Publishes to Redis kill channel; Bot Manager subscribes and acts
- Never directly accesses bot processes

### Decision 12 — Frontend Authentication

**Winner: Single password → JWT (read-only)**

- Password hash stored as env var
- JWT tokens, 1-hour expiry
- Frontend is read-only: bot performance, candlestick charts, P&L
- No start/stop from frontend — management is SSH only

---

## Final Service Map

| Service | Language | Job | Public? |
|---|---|---|---|
| Aggregation | Go | WebSocket data capture, gap detection | No |
| Candle Service | Python | OHLCV aggregation per timeframe | No |
| AI/ML Service | Python | Model inference, AI signal publishing | No |
| Bot Manager | Python + asyncio | Strategy lifecycle, file watcher | No — SSH only |
| Kill Switch | Go or Python | POST /kill/all + /kill/{name} | Yes — token auth |
| Frontend API | Any | Bot performance, charts, P&L | Yes — password + JWT |

## Go Aggregation Service — Detailed Spec

**Single responsibility:** Connect to exchange WebSocket feeds, maintain correct order book state, detect gaps, handle reconnects, write to Redis Streams and QuestDB.

### Outputs
- `Redis Stream: ticks:{SYMBOL}` — every normalized order book update
- `QuestDB` — async batch writes of tick data (non-blocking)

### Required behaviours
- Sequence number gap detection — log and flag missing ticks
- Reconnect with full snapshot request — never resume from delta after reconnect
- Backpressure handling — if Redis is slow, buffer in memory up to N ticks, then drop oldest with a gap marker
- Graceful shutdown — flush in-flight writes before exit
- Health endpoint — liveness probe for container orchestration

### Exchange connectivity
- One goroutine per exchange per symbol pair
- Supervisor goroutine restarts workers on crash without affecting other pairs
- Configurable exchange list via environment variables or config file

### Correctness invariants
- After reconnect: always request full L2 snapshot before resuming delta updates
- Every published tick includes: exchange, symbol, timestamp (exchange), timestamp (local), sequence number
- Gap marker published to stream when sequence break detected — downstream consumers can handle it explicitly

---

## Go Aggregation Service — Complete Spec (Final)

### Exchanges
- **KuCoin** and **Bybit** to start
- Decoupled via `Exchange` interface — adding new exchange = implement interface, register in factory
- Both public and private feeds — config-driven per exchange

### Exchange Interface (decoupling pattern)
```go
type Exchange interface {
    Name()      string
    Connect(ctx context.Context) error
    Subscribe(symbols []string, feeds []FeedType) error
    Ticks()     <-chan Tick
    Close()     error
}
// Registry: map[string]func(ExchangeConfig) Exchange
// "bybit" → NewBybitExchange, "kucoin" → NewKuCoinExchange
// New exchange = add one entry, implement interface
```

### KuCoin specifics
- Token-based WebSocket — must call REST `/api/v1/bullet-public` (or private) to get WS token first
- Token refreshes every 24h — renewal built into connector
- Ping/pong heartbeat required
- L2 feed: subscribe `/market/level2:{symbol}` — snapshot + incremental deltas
- Up to 300 subscriptions per connection

### Bybit specifics
- Public: `wss://stream.bybit.com/v5/public/spot`
- Private: `wss://stream.bybit.com/v5/private` — HMAC auth on connect
- Subscribe: `{"op":"subscribe","args":["orderbook.200.BTCUSDT"]}`
- `orderbook.200` = full 200-level depth
- Up to 10 topics per connection — at 200 symbols, ~20 WS connections needed

### Scale: 100–200 symbols
- WebSocket connections are multiplexed — NOT one goroutine per symbol
- One connection handles N subscriptions (exchange-limited)
- At 200 symbols on Bybit: ~20 WebSocket connections (10 symbols each)
- At 200 symbols on KuCoin: ~1 connection (300 sub limit)
- Goroutine per connection, fan-out to per-symbol state within that goroutine

### Order book depth
- L2 full depth — max levels available per exchange
- Bybit: 200 levels, KuCoin: 100 levels
- Full snapshot on connect + incremental deltas

### Serialization
- **JSON** — human readable, compatible with all consumers, sufficient for this scale
- Revisit if Redis throughput becomes a bottleneck at 200 symbols

### Redis Stream naming
- `ticks:bybit:BTCUSDT` — separate per exchange per symbol
- Downstream consumers can subscribe to one exchange, one symbol, or both
- Gap markers published to same stream with `is_gap: true`

### Reconnect rate limiter
- Token bucket per exchange: max 1 reconnect/second, burst of 3
- KuCoin: aggressive reconnects trigger token invalidation — backoff enforced
- Bybit: recommended max 1 reconnect/second
- Exponential backoff: 1s → 2s → 4s → 8s → max 60s

### Observability — simple but complete
**Prometheus metrics exposed on `/metrics`:**
```
aggregator_connection_up{exchange,symbol}          gauge 0/1
aggregator_ticks_total{exchange,symbol}            counter
aggregator_gaps_total{exchange,symbol}             counter  ← CRITICAL
aggregator_reconnects_total{exchange}              counter
aggregator_last_tick_timestamp{exchange,symbol}    gauge    ← stale detection
aggregator_ws_connections_active{exchange}         gauge
```

**Gap alert path:**
1. Publish tick with `is_gap: true` to Redis Stream
2. Increment `aggregator_gaps_total` Prometheus counter
3. Write to `gaps:log` Redis Stream: `{exchange, symbol, expected_seq, got_seq, ts}`
4. Log at ERROR level with structured fields

**Health endpoint `/health` (JSON):**
```json
{
  "status": "degraded",
  "exchanges": {
    "bybit":  {"connected": true,  "symbols_up": 198, "symbols_total": 200},
    "kucoin": {"connected": false, "symbols_up": 0,   "symbols_total": 200}
  },
  "gaps_last_hour": 3
}
```
Returns HTTP 200 if all connected, 206 if partial, 503 if all down.

### Tick schema (JSON in Redis Stream)
```json
{
  "exchange":     "bybit",
  "symbol":       "BTCUSDT",
  "exchange_ts":  1714732800123,
  "local_ts":     1714732800145,
  "seq":          8842001,
  "is_snapshot":  false,
  "is_gap":       false,
  "bids":         [["65432.10", "0.542"], ["65431.00", "1.200"]],
  "asks":         [["65433.00", "0.100"], ["65434.50", "2.300"]]
}
```

### Configuration (env vars)
```bash
EXCHANGES=bybit,kucoin
SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT,...
BYBIT_PUBLIC=true
BYBIT_PRIVATE=false
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
KUCOIN_PUBLIC=true
KUCOIN_PRIVATE=true
KUCOIN_API_KEY=...
KUCOIN_API_SECRET=...
KUCOIN_API_PASSPHRASE=...
REDIS_URL=redis://localhost:6379
QUESTDB_URL=http://localhost:9000
QUESTDB_BUFFER_SIZE=10000
ORDER_BOOK_DEPTH=200
RECONNECT_MAX_INTERVAL_S=60
```

### What it does NOT do
- Compute indicators or candles
- Serve the frontend
- Authenticate API requests
- Know about trading bots or strategies

---

## snapshot_1s Schema Analysis — Complete (Three-Pass)

### Context

Three analysis techniques applied sequentially to arrive at the final 67-field 1-second aggregate schema.

---

### Pass 1 — Field Decision Tree: Must-Store vs Derivable

**Rule:** "Derivable" means computable from other stored snapshot_1s fields at query time. The raw tick stream is NOT retained — once a second closes, intra-second data is gone.

**18 fields eliminated (76 → 58):**

| Eliminated | Derivation |
|---|---|
| `vwap` | `quote_volume / volume` |
| `twap` | Approximate: `(mid_open + mid_high + mid_low + mid_close) / 4` |
| `vwap_twap_divergence` | `(vwap - twap) / twap` |
| `mid_price` | `(best_bid + best_ask) / 2` |
| `mid_price_return` | `(mid_price - mid_price_open) / mid_price_open` |
| `spread_abs` | `best_ask - best_bid` |
| `spread_bps` | `spread_abs × 10000 / mid_price` |
| `price_impact_per_usd` | `0.01 / ((depth_to_1pct_bid + depth_to_1pct_ask) / 2)` |
| `imbalance_top5` | `(bid_top5 - ask_top5) / (bid_top5 + ask_top5)` |
| `imbalance_top10` | same pattern |
| `ofi_delta` | `LAG(ofi, 1)` in QuestDB or Python service |
| `ofi_percentile` | Rolling PERCENT_RANK over historical ofi |
| `sell_volume` | `volume - buy_volume` |
| `sell_count` | `trade_count - buy_count` |
| `avg_trade_size` | `volume / trade_count` |
| `noise_ratio` | `2 × min(uptick, downtick) / trade_count` |
| `volume_zscore` | Rolling z-score — compute in Python at signal time |
| `volume_percentile` | Rolling PERCENT_RANK — compute at training time |
| `spread_percentile` | Rolling PERCENT_RANK — compute at training time |

---

### Pass 2 — Gap Analysis: Missing Fields

**14 fields added (58 → 72):**

| Added | Why irreplaceable | Trading | ML |
|---|---|---|---|
| `bid_depth_usd_l1` | L1 quote fragility — cannot derive from cumulative top5 | H | H |
| `ask_depth_usd_l1` | same | H | H |
| `bid_depth_usd_l2` | L1→L2 drop tells you first support depth | M | H |
| `ask_depth_usd_l2` | same | M | H |
| `ofi_l1` | L1-only OFI outperforms full-book at short horizons (Cont 2014) | H | H |
| `block_buy_volume` | Volume from large classified buys — institutional flow | H | H |
| `block_sell_volume` | same, sell side | H | H |
| `bid_depth_usd_total_open` | Total depth change was uncomputable without open snapshot | H | H |
| `ask_depth_usd_total_open` | same | H | H |
| `weighted_bid_price` | Centre of mass of bid book — not derivable from cumulative sums | M | H |
| `weighted_ask_price` | same | M | H |
| `realized_skewness` | Third moment of intra-second returns — crash/spike asymmetry | M | H |
| `trade_sign_autocorr` | Order splitting detection (+1) vs market making (-1) | H | H |
| `inter_trade_interval_std_ms` | Std of inter-trade gaps — beyond what trade_clustering captures | M | H |
| `num_trade_price_levels` | Distinct trade prices — sweep vs stable fill detection | H | H |
| `gap_count` | Integer gap severity (replaces boolean has_gap) | essential | essential |

---

### Pass 3 — Correlation Cluster Analysis: Redundancies

**5 fields dropped (72 → 67 FINAL):**

| Dropped | Cluster | Reason |
|---|---|---|
| `bid_depth_usd_top5` | Depth cascade | r≈0.90 with top10; information bracketed by l2 (below) + top10 (above) |
| `ask_depth_usd_top5` | Depth cascade | same |
| `spread_open` | Spread OHLC | Weakest spread field — approximable from prior row's spread_mean |
| `has_gap` | Quality | Exact derivation: `gap_count > 0` |
| `large_trade_zscore` | Block trade cluster | Computable at training time from `max_trade_size` + `volume / trade_count` |

**Key correlation findings:**
- `ofi` vs `ofi_l1`: r≈0.5–0.7 normally, diverges during spoofing. Divergence = implicit manipulation signal. Keep both intentionally.
- `top5` vs `top10`: r≈0.85–0.95 — highest-correlation pair in schema. Drop top5.
- Spread OHLC: converge in stable markets; `spread_high - spread_low` is the key signal. `spread_open` adds least unique information.
- `uptick_count` vs `buy_count`: different concepts (tick direction vs taker aggressor) — keep both despite surface similarity.

---

### Final snapshot_1s Schema (67 fields)

```sql
CREATE TABLE snapshot_1s (
    ts                          TIMESTAMP,
    exchange                    SYMBOL CAPACITY 8   INDEX,
    symbol                      SYMBOL CAPACITY 256 INDEX,
    open                        DOUBLE,
    high                        DOUBLE,
    low                         DOUBLE,
    close                       DOUBLE,
    volume                      DOUBLE,
    quote_volume                DOUBLE,
    trade_count                 INT,
    twap                        DOUBLE,
    mid_price_open              DOUBLE,
    mid_price_high              DOUBLE,
    mid_price_low               DOUBLE,
    vwmp                        DOUBLE,
    spread_high                 DOUBLE,
    spread_low                  DOUBLE,
    spread_mean                 DOUBLE,
    effective_spread            DOUBLE,
    best_bid                    DOUBLE,
    best_ask                    DOUBLE,
    bid_depth_usd_l1            DOUBLE,
    ask_depth_usd_l1            DOUBLE,
    bid_depth_usd_l2            DOUBLE,
    ask_depth_usd_l2            DOUBLE,
    bid_depth_usd_top10         DOUBLE,
    ask_depth_usd_top10         DOUBLE,
    bid_depth_usd_total         DOUBLE,
    ask_depth_usd_total         DOUBLE,
    bid_depth_usd_top10_open    DOUBLE,
    ask_depth_usd_top10_open    DOUBLE,
    bid_depth_usd_total_open    DOUBLE,
    ask_depth_usd_total_open    DOUBLE,
    weighted_bid_price          DOUBLE,
    weighted_ask_price          DOUBLE,
    depth_to_1pct_bid           DOUBLE,
    depth_to_1pct_ask           DOUBLE,
    ofi                         DOUBLE,
    ofi_l1                      DOUBLE,
    buy_volume                  DOUBLE,
    buy_count                   INT,
    block_buy_volume            DOUBLE,
    block_sell_volume           DOUBLE,
    max_trade_size              DOUBLE,
    large_bid_orders            INT,
    large_ask_orders            INT,
    first_trade_offset_ms       INT,
    last_trade_offset_ms        INT,
    trade_clustering            DOUBLE,
    max_consecutive_run         INT,
    realized_vol                DOUBLE,
    realized_skewness           DOUBLE,
    uptick_count                INT,
    downtick_count              INT,
    bid_order_arrivals          INT,
    ask_order_arrivals          INT,
    bid_cancel_count            INT,
    ask_cancel_count            INT,
    ob_modify_count             INT,
    avg_bid_order_size          DOUBLE,
    avg_ask_order_size          DOUBLE,
    best_bid_changes            INT,
    best_ask_changes            INT,
    quote_stuff_ratio           DOUBLE,
    trade_sign_autocorr         DOUBLE,
    inter_trade_interval_std_ms DOUBLE,
    num_trade_price_levels      INT,
    gap_count                   INT,
    bar_count                   INT
) TIMESTAMP(ts) PARTITION BY DAY TTL 30d;
```

---

## Testing Strategy — Go Aggregation Service

**Extension session:** TDD and correctness testing — ensuring no wrong data is ever stored or published.

### Phase 1: Reverse Brainstorming — Attack Vectors (27 total)

"How do we make the aggregator store wrong data?" — every answer is a test case.

#### Gap Detection Failures

| ID | Attack Vector | What Goes Wrong |
|---|---|---|
| Gap #1 | First-tick blind spot | Tracker initialised at 0 fires false gap on seq=8842001 at connect |
| Gap #2 | Sequence number wrap | uint32 rollover triggers false gap at 4294967295→0 |
| Gap #3 | Mid-stream snapshot (no disconnect) | Exchange pushes unprompted snapshot; handler treats as delta, double-applies levels |
| Gap #4 | Duplicate sequence number | Exchange resends same seq; detector fires false gap or silently ignores |
| Gap #5 | Gap during snapshot request window | Snapshot at seq N, buffered deltas N-10 to N+20 — off-by-one on replay boundary drops or duplicates one delta |

#### Wrong Calculation Failures

| ID | Attack Vector | What Goes Wrong |
|---|---|---|
| Calc #1 | Bid/ask side swap | Parser maps "b"/"a" or 0/1 incorrectly; best_bid > best_ask, inverted spread |
| Calc #2 | Delete on non-existent level | Panic or phantom re-insertion when deleted level reappears |
| Calc #3 | Stale depth after snapshot | Depth accumulators not reset on reconnect — wrong depth numbers on correct levels |
| Calc #4 | Float precision in price levels | "65432.10" parsed as 65432.09999... — delete lookup fails, phantom levels accumulate |
| Calc #5 | Size=0 means delete | Handler inserts zero-size level instead of removing; book grows unboundedly |
| Calc #6 | Out-of-order delta application | Two goroutines apply seq=1002 before seq=1001; final state wrong |

#### QuestDB Write Correctness Failures

| ID | Attack Vector | What Goes Wrong |
|---|---|---|
| QDB #1 | WAL suspension silent write loss | HTTP 200 returned, zero rows written, no error surfaced |
| QDB #2 | Partial batch on flush interval | Process killed mid-buffer; restart causes duplicate rows or silent loss |
| QDB #3 | Timestamp field wrong | exchange_ts in ms sent to TIMESTAMP column expecting µs; rows land in 1970 |
| QDB #4 | OOO writes exceed commit lag | Ticks with exchange_ts > commit.lag behind are silently dropped |
| QDB #5 | ILP connection pool exhaustion | Per-goroutine connections hit QuestDB limit; silent drops under reconnect storm |

#### Redis Write Correctness Failures

| ID | Attack Vector | What Goes Wrong |
|---|---|---|
| Redis #1 | Gap marker published after the tick it describes | Candle Service processes tick N+1 before gap marker; bar computed without gap awareness |
| Redis #2 | MAXLEN trim races with consumer | Consumer's last-seen ID older than stream's first entry; missed messages undetected |
| Redis #3 | JSON serialization non-determinism | Map iteration randomised; bid levels not sorted; best bid not at index 0 |
| Redis #4 | Connection loss between gap marker and tick | Tick arrives after reconnect with higher message ID than gap marker on other symbols |
| Redis #5 | Wrong stream key | BTC-USDT vs BTCUSDT inconsistency; Candle Service misses half the ticks silently |

#### Exchange-Specific Edge Cases

| ID | Attack Vector | What Goes Wrong |
|---|---|---|
| KuCoin #1 | Token expiry mid-stream | Connection stays open, zero ticks arrive, connection_up gauge stays 1 |
| KuCoin #2 | Subscription confirmation race | Snapshot arrives before ack; discarded as unexpected; book never initialises |
| Bybit #1 | 10-topic connection limit exceeded | 11th symbol silently receives zero ticks; no error |
| Bybit #2 | Snapshot type field absent | Handler defaults to delta; full book payload applied incrementally; levels duplicated |

#### Silent Failure Modes

| ID | Attack Vector | What Goes Wrong |
|---|---|---|
| Silent #1 | QuestDB HTTP 200 but pre-commit kill | Batch acknowledged, never committed; no gap marker, no alert |
| Silent #2 | Redis wrong instance | Publishes to test Redis; Candle Service reads production Redis; sees nothing |
| Silent #3 | Symbol normalisation drops a symbol | Unknown symbol subscription error ignored; symbol receives zero ticks forever |
| Silent #4 | Backpressure drop gap marker wrong seq range | 5 dropped ticks had internal gap; reported as single gap of 5 not two gaps |
| Silent #5 | SIGTERM drops in-flight QuestDB batch | ctx cancelled mid-HTTP request; batch lost; no gap marker |

#### Concurrency & Race Conditions

| ID | Attack Vector | What Goes Wrong |
|---|---|---|
| Race #1 | Snapshot buffer and delta buffer share no lock | Data race on buffer during reconnect; deltas lost or double-counted |
| Race #2 | Order book read during serialization | Partially-updated book serialized; published JSON internally inconsistent |
| Race #3 | Reconnect storm spawns multiple goroutines | Two active connections per symbol; interleaved seqs look like constant gaps |
| Race #4 | Context cancellation during snapshot merge | Partial merge committed to book; state is neither pre nor post-merge |
| Race #5 | Health endpoint reads without sync | /health reports connected:true during mid-disconnect |
| Race #6 | Token bucket not goroutine-safe | Two goroutines both consume last token; rate limit bypassed |
| Race #7 | Prometheus label map built from shared map | Map resize during concurrent label registration causes race |

---

### TDD Test Catalogue — 35 Tests, Ordered by Layer

#### Layer 1: Pure Functions (no I/O, no goroutines)

| Test | Asserts |
|---|---|
| `TestApplyDelta_AddLevel` | New price level inserted into empty book |
| `TestApplyDelta_UpdateLevel` | Size of existing level updated correctly |
| `TestApplyDelta_DeleteLevel` | size=0 removes level |
| `TestApplyDelta_DeleteNonExistent` | No-op, no panic |
| `TestSortBids_DescendingPrice` | Bids sorted highest price first |
| `TestSortAsks_AscendingPrice` | Asks sorted lowest price first |
| `TestDetectGap_FirstTick` | No gap emitted on first tick |
| `TestDetectGap_ContiguousSeq` | No gap when seq = prev+1 |
| `TestDetectGap_MissedTicks` | Gap emitted with correct seq_before/seq_after |
| `TestDetectGap_Duplicate` | No gap on duplicate seq; tick discarded |
| `TestDetectGap_Wrap` | No gap on uint32 rollover |
| `TestNormalizeSymbol_KuCoin` | "BTC-USDT" → "BTCUSDT" |
| `TestNormalizeSymbol_Bybit` | Already normalized symbol unchanged |
| `TestPriceStringToKey` | "65432.10" always maps to same map key |

#### Layer 2: State Machine (no I/O)

| Test | Asserts |
|---|---|
| `TestOrderBook_ApplySnapshot` | Full book reset; prior levels cleared |
| `TestOrderBook_ApplyDeltaSequence` | 10 sequential deltas produce correct final state |
| `TestOrderBook_ReconnectMerge` | Snapshot at seq N, buffered deltas N-5 to N+10 — exactly N+1..N+10 replayed, no gap marker, no duplicates |
| `TestOrderBook_MidStreamSnapshot` | Unprompted snapshot resets book; no gap emitted |
| `TestGapTracker_InternalBufferDrop` | gap_cause = internal_buffer_overflow |
| `TestGapTracker_ExternalDisconnect` | gap_cause = external_disconnect |

#### Layer 3: Integration (in-process mock WebSocket server)

| Test | Asserts |
|---|---|
| `TestKuCoinConnector_ConnectAndReceiveTick` | Full round-trip through mock WS server |
| `TestKuCoinConnector_TokenRenewal` | Expired token triggers REST renewal and reconnect |
| `TestBybitConnector_SubscriptionLimit` | Error/split when > 10 topics per connection |
| `TestReconnectWithBackoff` | 5 disconnects produce backoff delays 1s/2s/4s/8s/16s |
| `TestSnapshotDeltaMerge_Integration` | Mock WS: disconnect + snapshot + buffered deltas → clean resume |
| `TestGapMarker_PublishedBeforeTick` | Gap marker Redis message ID < first following tick message ID |

#### Layer 4: Fault Injection (real Redis + QuestDB in Docker, `-race`)

| Test | Asserts |
|---|---|
| `TestRedisBackpressure_DropAndGapMarker` | Slow Redis triggers drop; gap marker emitted with correct seq range |
| `TestQuestDBWALSuspension_AutoRecovery` | WAL suspended; recovery issued within 30s; ticks written after |
| `TestGracefulShutdown_FlushBatch` | SIGTERM mid-batch; all rows present in QuestDB; exit 0 |
| `TestReconnectStorm_SingleGoroutine` | 10 rapid disconnects; exactly 1 active connection at all times |
| `TestRaceDetector_OrderBook` | 50-run concurrent read/write under `-race`; zero race reports |
| `TestRaceDetector_ReconnectBuffer` | Reconnect + delta arrival concurrent under `-race`; zero race reports |
| `TestHealthEndpoint_Consistency` | /health hammered during 200 symbol reconnects under `-race`; no inconsistent state |
| `TestWrongStreamKey_NeverOccurs` | All published stream keys match normalized format; no raw exchange format leaks |
| `TestGapMarkerOrder_AfterRedisReconnect` | Redis drop after gap marker; tick published after reconnect; stream consistent |

---

### Phase 2: Failure Analysis — Spec Failure Modes

| # | Failure Mode | Observable Symptom | Correct Behaviour | Test Layer |
|---|---|---|---|---|
| F1 | WebSocket disconnect | `connection_up=0`; `last_tick_timestamp` stale | Reconnect on backoff; snapshot before deltas; `external_disconnect` gap marker | Layer 3 |
| F2 | Exchange rate-limits reconnect | `reconnects_total` rising; `connection_up=0` sustained | Token bucket throttles; backoff to 60s; `/health` 503 | Layer 3 |
| F3 | Sequence gap in live feed | `gaps_total` increments; gap marker in stream | Marker before next real tick; correct `seq_before`/`seq_after` | Layer 2+3 |
| F4 | Snapshot/delta merge fails silently | Book wrong; no gap emitted | Can't happen if `TestOrderBook_ReconnectMerge` passes — that test is the gate | Layer 2 |
| F5 | Redis unavailable | Buffer fills; oldest ticks dropped | `internal_buffer_overflow` gap marker; resumes on recovery; no crash | Layer 4 |
| F6 | Redis slow (backpressure) | Buffer depth rising; write lag growing | MAXLEN trim; gap marker per drop; write path never blocks WebSocket goroutine | Layer 4 |
| F7 | QuestDB WAL suspended | HTTP 200; `rows_written` diverges from `ticks_total` | `wal_tables()` poll detects within 30s; `RESUME WAL` issued; `/health` reflects | Layer 4 |
| F8 | QuestDB unavailable | ILP connection refused | Buffer up to limit; drop + gap marker; WebSocket capture continues | Layer 4 |
| F9 | KuCoin token expiry mid-stream | Connection open; `last_tick_timestamp` stale; `ticks_total` flat | Stale detection fires; forced reconnect; new token fetched before reconnect | Layer 3 |
| F10 | Bybit 10-topic limit breach | `symbols_up < symbols_total`; one symbol `last_tick_timestamp` never updates | Enforced at subscribe time; auto-split to new connection | Layer 3 |
| F11 | Process OOM kill | All connections drop | systemd `Restart=always`; on restart each symbol emits one `external_disconnect` gap | Operational |
| F12 | SIGTERM during flush | Batch in flight; rows may be lost | Flush completes before shutdown; exit 0 | Layer 4 |
| F13 | Unknown symbol in config | Subscription error from exchange | Logged ERROR; symbol `symbols_down` in `/health`; no crash; other symbols unaffected | Layer 3 |
| F14 | Wrong Redis URL | Connection refused at startup | Startup fails fast with clear error; no partial state | Layer 1 |
| F15 | Float price key collision | Phantom levels accumulate; depth drifts | String keys for price levels; never float64 map keys | Layer 1 |
| F16 | Reconnect storm | Multiple active connections; constant false gaps | Reconnect serialised per (exchange, symbol); exactly 1 active connection | Layer 4 `-race` |

---

### Phase 3: Chaos Engineering — Fault Injection Framework

#### Four Injectable Seams

```
WebSocket feed  ──►  [Exchange interface]  ──►  Order book state
                                                      │
                                           [Redis writer interface]
                                                      │
                                           [QuestDB writer interface]
                                                      │
                                           [Clock interface]
```

Every external dependency is behind an interface. Tests inject broken implementations. No `time.Sleep` to wait for reconnects — clock is injected and controllable.

#### Seam 1: MockWSServer — Scriptable WebSocket

```go
type MockWSServer struct{ events []WSEvent }
type WSEvent struct {
    Type    string        // "message", "close", "pause"
    Payload []byte
    After   time.Duration
}
```

Events are delivered synchronously — each consumed before the next fires. No goroutine races between events N and N+1. Reconnect merge test: emit snapshot(seq=1000), three deltas, close, two more deltas, new snapshot(seq=1005), resume delta — assert exactly the right deltas replayed, no gap marker, no duplicates.

#### Seam 2: FakeRedis / Toxiproxy Redis

- `FakeRedis`: in-memory, `failAfter N` and `slowAfter N` controls — used for ordering and content assertions
- Toxiproxy in front of real Docker Redis: 200ms latency toxic triggers real backpressure without mocking — used for Layer 4 backpressure tests

#### Seam 3: FakeQuestDB — Suspendable HTTP Server

```go
type FakeQuestDB struct {
    suspended  bool
    httpServer *httptest.Server
}
func (f *FakeQuestDB) SetSuspended(v bool)
// When suspended: accepts ILP, returns HTTP 200, stores nothing
// QueryWALTables() returns suspended=true — aggregator must detect and recover
```

#### Seam 4: MockClock — No time.Sleep in Tests

```go
type Clock interface {
    Now() time.Time
    After(d time.Duration) <-chan time.Time
}
// MockClock.Advance(d) fires all waiters whose deadline has passed
// Backoff test runs in microseconds, not seconds
```

#### Determinism Rules

1. **No `time.Sleep` in assertions** — use channels with `time.After` deadline
2. **MockWSServer delivers events synchronously** — no goroutine races
3. **`-race` is mandatory** — all Layer 3+4 tests run with `-race` in CI
4. **QuestDB version pinned** in Docker Compose — not `latest`
5. **Each Layer 4 test uses its own Redis keyspace prefix** — `ticks:test-{uuid}:SYMBOL`

#### CI Gate

```makefile
test-unit:        go test -race ./internal/...
test-integration: go test -race ./integration/... -timeout 60s
test-fault:       docker compose up -d && go test -race ./fault/... -timeout 120s
coverage:         go test -covermode=atomic | awk '{if ($3+0 < 80) exit 1}'
```

Layers 1–3 run on every PR. Layer 4 runs on merge to main only (Docker spin-up cost). Coverage gate on every PR measures Layers 1–3.

---

### Layer 5: Live Exchange Smoke Tests (18 tests, manual trigger only)

Run with `go test -v -tags live ./live/... -timeout 120s` with real API credentials. Never run in CI. Triggered on-demand: before production deployment, after config changes, after exchange outages.

**Excluded by design:** Rate limit recovery (no need to deliberately trigger exchange bans during testing) and KuCoin subscription count boundary (won't be near the 300-symbol limit initially).

**Known gap:** Bybit private feed HMAC auth — no live test. Private feed is out of V1 scope.

| Test | Asserts |
|---|---|
| `TestLive_KuCoinTokenFetch` | REST `/api/v1/bullet-public` returns valid WS token and endpoint URL |
| `TestLive_KuCoinConnect` | WebSocket connects with token; subscription ack received within 5s |
| `TestLive_KuCoinFirstTick` | First tick received for BTCUSDT within 10s of subscribe |
| `TestLive_KuCoinPingPong` | Heartbeat ping sent; pong received within configured interval |
| `TestLive_KuCoinTokenRenewal` | Token TTL overridden to 30s; connector auto-renews via REST; resumes ticks without gap marker |
| `TestLive_BybitConnect` | WebSocket connects to `wss://stream.bybit.com/v5/public/spot` |
| `TestLive_BybitSubscribe` | `orderbook.200.BTCUSDT` subscription returns success response |
| `TestLive_BybitFirstTick` | First orderbook update received within 10s |
| `TestLive_BybitSubscriptionLimit` | 10 subscriptions succeed on one connection; 11th triggers split to new connection, not error |
| `TestLive_BybitDeadConnection` | TCP drop simulated via Toxiproxy against real endpoint; connector detects via ping timeout; reconnects; resumes |
| `TestLive_KuCoinSequenceContinuity` | 30s of ticks recorded; force reconnect; assert sequence tracker does not reset to 0; assert no false gap on first tick after snapshot |
| `TestLive_MultiSymbolInterleaving` | 10 symbols, 60s; all symbols receive ticks; no symbol's `symbol` field contains another symbol's value; no symbol stale > 10s |
| `TestLive_RedisLatency` | p99 latency from `exchange_ts` to Redis Stream message ID < 50ms over 60s window |
| `TestLive_QuestDBPipeline` | 60s live ticks; query QuestDB; row count matches expected; no `ts = 1970`; `exchange_ts` within 5s of `local_ts` |
| `TestLive_HealthAccuracy` | `/health` polled every 1s for 60s; `connected: true` stable; `symbols_up` matches config; `gaps_last_hour = 0` |
| `TestLive_GracefulShutdown` | SIGTERM during live tick flow; clean shutdown ≤5s; all in-flight ILP rows in QuestDB; exit 0 |
| `TestLive_BothExchangesSimultaneous` | KuCoin + Bybit both connected; 5 symbols each; 60s; assert no cross-contamination between exchange tick streams |
| `TestLive_GapMarkerNotFiredOnCleanSession` | 60s clean session on both exchanges; assert `aggregator_gaps_total` remains 0 throughout |

**Coverage completeness against failure categories:**

| Category | Mock (L1–L4) | Live (L5) |
|---|---|---|
| Token fetch + renewal | Controlled mock renewal | Real API token renewal (30s TTL override) |
| Exchange-side disconnect | Close frame via mock | TCP dead connection via Toxiproxy |
| Sequence continuity | Controlled seq numbers | Real seq across real reconnect |
| Multi-symbol correctness | 2 symbols max in mock | 10 symbols, 60s live |
| Write latency | Not measurable in mock | Real p99 measurement |
| QuestDB full pipeline | Fake QuestDB | Real rows, real timestamps |
| Health endpoint accuracy | Simulated state | Real connection state, 60s |
| Graceful shutdown | Mock exchange load | Real exchange load |
| Cross-exchange isolation | Single exchange per test | Both exchanges simultaneously |
| Clean session baseline | N/A | Zero gaps over 60s clean window |

---

### Decision Tree — Coverage Analysis

**Root question: Could wrong or missing data reach Redis or QuestDB?**

Two branches: Wrong data (correct quantity, wrong values) and Missing data (gaps).

#### Gaps Found — 7 missing tests (filled)

| # | Gap | Severity | Test Added |
|---|---|---|---|
| G1 | Unknown exchange field silently corrupts state | Medium | `TestParse_UnknownFieldIgnored` [L1] |
| G2 | JSON field names not validated against schema | Medium | `TestSerialize_FieldNames` [L1] |
| G3 | [DB #7] doesn't assert correct table name | Low | Extended [DB #7] |
| G4 | ILP queue depth limit under QuestDB backpressure | High | `TestILPBackpressure_QueueLimit` [L4] |
| G5 | Unknown symbol subscription error not logged/marked down | High | `TestSymbolNorm_UnknownSymbolLoggedAndMarkedDown` [L3] |
| G6 | Redis wrong instance not detected at startup | High | `TestStartup_RedisSentinelKeyVerification` [L4b] |
| G7 | Memory growth not bounded under sustained load | Medium | `TestMemoryGrowth_Bounded` [L4b] |

---

### Pre-mortem Analysis — Structural Testing Holes

**Premise:** 69 tests pass, service ships, data still corrupts. How did it get through?

7 structural holes found. 200-symbol concurrency hole explicitly excluded (does not matter for this scale).

| Hole | Root Cause | Fix |
|---|---|---|
| Version gap | Test infra pinned at specific versions, production drifts | `TestVersionParity` [L5 + deploy pre-check] — assert QuestDB + Redis versions match pinned test versions at runtime |
| Latency-dependent gap marker ordering | Tests use sub-ms local Docker Redis; ordering guarantee breaks at 3ms+ | `TestGapMarkerOrder_UnderLatency` [L4] — Toxiproxy adds 5ms, assert ordering still holds; forces atomic Redis pipeline for gap marker + tick |
| Quiet fixture problem | Capture sessions are average traffic; peak (10× at exchange open) never tested | 10× stress replay mode in `cmd/capture --stress`; `TestHighThroughput_10x` [L4b] — 60s at 10× speed, assert no backpressure gaps |
| 24-hour memory leak | No test runs > 5 minutes; 30-day unattended requirement has no test | `TestMemoryGrowth_Bounded` [L4b] — 10 min with continuous level insert/delete, assert sublinear RSS growth; 48h canary deployment gate before production |
| Library trust | Tests verify aggregator code, not ILP client retry behavior under timeouts | `TestILPClient_NoDoubleCommitOnTimeout` [L4] — fake QuestDB delays response past timeout, assert exactly N rows not 2N |
| First-30-seconds window | Full stack Docker test starts from replay frame 1, skips connect/subscribe/snapshot startup sequence | Full stack test must include startup: connect → subscribe → snapshot against embedded mock exchange, then begin replay |
| Credential assumption | Layer 5 uses dev credentials; prod credentials get different error response formats | `TestLive_ErrorResponseFormat` [L5] — trigger one intentional rate limit hit, assert error parsed correctly regardless of format (JSON vs WebSocket close frame) |

**Final test suite totals after all additions:**

| Layer | Count | Trigger |
|---|---|---|
| L1 — Pure functions | 16 | Every PR |
| L2 — State machine | 6 | Every PR |
| L3 — Mock WS integration | 7 | Every PR |
| L4 — Fault injection (Docker) | 11 | Merge to main |
| L4b — Full stack Docker | 13 | Merge to main |
| L5 — Live exchange | 19 | Manual on-demand |
| **Total** | **72** | — |

**Non-test deployment gates:**
- 48h canary run (single symbol pair, RSS sampled every 10 min) before any production deployment
- `TestVersionParity` runs as deployment pre-check against production infrastructure

---

### Assumption Reversal + Six Thinking Hats — Additions

#### New tests from Assumption Reversal

| Test | Layer | Assumption Challenged |
|---|---|---|
| `TestFuzz_IncomingWsFrames` — feed random mutations of valid captured frames (truncated JSON, extra fields, wrong types, null where string expected); assert no panic, no silent corruption | L1 | "Mock WS accurately represents real exchange behaviour" |
| `TestOrderBook_Invariants` — after every delta, assert: bids strictly descending, asks strictly ascending, no bid >= any ask, no size=0 level in map | L2 | "Race detector catches all concurrency bugs" |
| `TestDetectGap_LargeSequenceNumber` — parse seq=9007199254740993 and seq=9007199254740994; assert treated as distinct; assert no false gap (float64 precision at 2^53+1) | L1 | "Sequence numbers are always safe as JSON numbers" |
| `TestCrash_DataLossWindow` — SIGKILL (not SIGTERM) mid-write; restart; assert missing rows ≤ ceil(commit_lag_ms / tick_interval_ms); quantifies actual crash loss window | L4b | "500ms flush = 500ms data loss on crash" (actual loss = commit lag = 1000ms) |

#### Process additions from Six Thinking Hats

- **`TEST_TIMEOUT_MULTIPLIER=2` CI environment variable** — all test deadlines multiply by 2 in CI; parametrized not hardcoded; documented in `TESTING.md`
- **`TESTING.md` at repo root** — every test documented with: layer, purpose, failure mode it catches; maintained as the authoritative test catalogue
- **GitHub branch protection required status checks** — L1+L2+L3 required on every PR; L4+L4b required on merge to main; enforced not conventional
- **Layer 3 chaos mode** — optional `--chaos` flag on mock WS server that randomly delays/duplicates/drops 1% of messages; run as separate `test-chaos` target to find timing-sensitive tests

#### Updated totals (Assumption Reversal + Six Hats only, Chaos Monkey excluded)

| Layer | Count | Trigger |
|---|---|---|
| L1 — Pure functions | 18 | Every PR |
| L2 — State machine | 7 | Every PR |
| L3 — Mock WS integration | 7 | Every PR |
| L4 — Fault injection (Docker) | 11 | Merge to main |
| L4b — Full stack Docker | 14 | Merge to main |
| L5 — Live exchange | 19 | Manual on-demand |
| **Total** | **76** | — |

---

### Fixture Architecture — 24-Hour Automated Refresh

Fixtures are **not committed to the repo**. They are living data — refreshed every 24 hours by a scheduled capture job, always reflecting current exchange message formats.

**Capture schedule:** Systemd timer on the dev/staging machine, fires daily at 08:00 UTC (before most market opens). Captures 10 minutes of traffic from both exchanges, 5 symbols each (BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT).

**Refresh cycle:**
1. `cmd/capture` runs, writes new session files to `/tmp/fixtures-new/`
2. Verifies capture is valid (minimum frame count per symbol, no parse errors)
3. Uploads new fixtures to Backblaze B2 bucket `magnum-opus-fixtures/latest/` — **overwrites** previous
4. Deletes `/tmp/fixtures-new/`
5. Previous fixture is gone — no accumulation, no stale data

**CI access:** Before running Layer 3 replay and Layer 4b full stack tests, CI downloads the `latest/` fixtures from B2:
```makefile
fixtures-pull:
    rclone sync b2:magnum-opus-fixtures/latest/ ./testdata/fixtures/
```
`testdata/fixtures/` is in `.gitignore` — never committed.

**Fixture validation in CI:** If `testdata/fixtures/` is empty or older than 25 hours, CI fails with: "Fixtures stale or missing — run `make fixtures-pull` or check capture job." Forces explicit acknowledgement of missing fixtures rather than silently running without them.

**Failure handling:** If the capture job fails (exchange down, credential issue), the previous fixture remains in B2 (B2 keeps one version back). CI uses the previous version and logs a warning: "Fixtures are N hours old — capture job may have failed."

**Fixture format:** Each file is a `.jsonl` (one JSON line per WebSocket frame + connection event). Filename: `{exchange}-{symbols}-{date}.jsonl`. The `latest/` prefix always contains exactly one file per exchange.

**What fixtures cover that they didn't before:** Because captures run daily at market open (08:00 UTC catches Asian session open for Bybit), the fixture library naturally captures higher-volatility periods. The 24-hour refresh also means exchange protocol changes (new field added, type changed) surface within 24 hours — the capture job would either capture the new format or fail validation, alerting you before CI breaks silently.

---

### Rationalized Final Test Suite

**Constraint:** Tests run on local PC. GitHub Actions used minimally (no paid minutes).

#### Trigger Model

| Trigger | Tests | Where | Time |
|---|---|---|---|
| Pre-commit hook | L1 only | Local | ~2s |
| Pre-push hook | L1 + L2 + L3 | Local | ~30s |
| `make test-all` (pre-release) | L1 + L2 + L3 + L4 + L4b | Local | ~10min |
| GitHub Actions (on push) | L1 + L2 only | GitHub | ~45s, free tier |
| Manual on-demand | L5 live exchange | Local | ~2min |

#### Final Test Catalogue — 61 Tests

**L1 — Pure functions (19 tests, pre-commit + GitHub Actions)**

| Test | Failure mode caught |
|---|---|
| `TestApplyDelta_AddLevel` | New level insertion |
| `TestApplyDelta_UpdateLevel` | Level size update |
| `TestApplyDelta_DeleteLevel` | size=0 removes level |
| `TestApplyDelta_DeleteNonExistent` | Delete non-existent = no-op |
| `TestSortBids_DescendingPrice` | Bid ordering |
| `TestSortAsks_AscendingPrice` | Ask ordering |
| `TestDetectGap_FirstTick` | False gap on connect |
| `TestDetectGap_ContiguousSeq` | No gap on prev+1 |
| `TestDetectGap_MissedTicks` | Correct gap detection |
| `TestDetectGap_Duplicate` | Duplicate seq = no gap |
| `TestDetectGap_Wrap` | uint32 rollover = no gap |
| `TestDetectGap_LargeSequenceNumber` | float64 precision loss at 2^53+1 |
| `TestNormalizeSymbol_KuCoin` | BTC-USDT → BTCUSDT |
| `TestNormalizeSymbol_Bybit` | Already normalized unchanged |
| `TestPriceStringToKey` | String price map key consistency |
| `TestSerialize_FieldNames` | JSON field names match schema |
| `TestFuzz_IncomingWsFrames` | Unknown/malformed exchange frames |
| `TestConfig_UnknownSymbolValidation` | Unknown symbol logged, health marked down |
| `TestNormalizeSymbol_KeyFormat` | Stream key format, no raw exchange format leaks |

**L2 — State machine (7 tests, pre-commit + GitHub Actions)**

| Test | Failure mode caught |
|---|---|
| `TestOrderBook_ApplySnapshot` | Full book reset, prior levels cleared |
| `TestOrderBook_ApplyDeltaSequence` | 10 sequential deltas → correct state |
| `TestOrderBook_ReconnectMerge` | Snapshot at seq N, buffered N-5→N+10, exactly N+1→N+10 replayed |
| `TestOrderBook_MidStreamSnapshot` | Unprompted snapshot resets book, no gap |
| `TestOrderBook_Invariants` | After every delta: bids descending, asks ascending, no bid≥ask, no size=0 |
| `TestGapTracker_InternalBufferDrop` | gap_cause = internal_buffer_overflow |
| `TestGapTracker_ExternalDisconnect` | gap_cause = external_disconnect |

**L3 — Mock WS integration (6 tests, pre-push local)**

| Test | Failure mode caught |
|---|---|
| `TestKuCoinConnector_ConnectAndReceiveTick` | Full round-trip through mock WS |
| `TestKuCoinConnector_TokenRenewal` | Expired token triggers REST renewal, resumes without gap |
| `TestBybitConnector_SubscriptionLimit` | 11th topic splits to new connection, not error |
| `TestReconnectWithBackoff` | 5 disconnects → delays 1s/2s/4s/8s/16s |
| `TestSnapshotDeltaMerge_Integration` | Full disconnect+snapshot+buffer replay end-to-end |
| `TestGapMarker_PublishedBeforeTick` | Gap marker message ID < next tick message ID |

**L4 — Fault injection Docker (7 tests, `make test-all` local)**

| Test | Failure mode caught |
|---|---|
| `TestRedisBackpressure_DropAndGapMarker` | Slow Redis → buffer overflow → gap marker with correct seq range |
| `TestQuestDBWALSuspension_AutoRecovery` | WAL suspended → detected within 30s → RESUME WAL issued |
| `TestGracefulShutdown_FlushBatch` | SIGTERM mid-batch → all rows in QuestDB → exit 0 |
| `TestReconnectStorm_SingleGoroutine` | 10 rapid disconnects → exactly 1 active connection always |
| `TestRaceDetector_OrderBook` | Concurrent read/write under -race → zero races |
| `TestRaceDetector_ReconnectBuffer` | Reconnect + delta concurrent under -race → zero races |
| `TestILPClient_NoDoubleCommitOnTimeout` | ILP timeout → retry → assert N rows not 2N |

**L4b — Full stack Docker (8 tests, `make test-all` local)**

| Test | Failure mode caught |
|---|---|
| `[DB #2] Field value round-trip` | Timestamp units, price precision, field mapping end-to-end |
| `[DB #3] Timestamp ordering` | No out-of-order rows past commit lag, no 1970 timestamps |
| `[DB #5] Batch boundary correctness` | No duplicate rows on flush |
| `[DB #6] Gap rows in QuestDB` | Gap marker rows persisted with correct is_gap, seq_before, seq_after |
| `[DB #8] Concurrent symbol isolation` | No cross-contamination between symbols |
| `[DB #10] QuestDB restart recovery` | Aggregator reconnects, resumes writes, gap markers for downtime |
| `[Redis #4] Full pipeline message content` | Field-for-field Redis message matches WS frame |
| `TestStartup_RedisSentinelKeyVerification` | Wrong Redis instance detected at startup |

**L5 — Live exchange (14 tests, manual local)**

| Test | What it verifies |
|---|---|
| `TestLive_KuCoinTokenFetch` | Real REST token fetch |
| `TestLive_KuCoinConnect` | Real WS connect with token |
| `TestLive_KuCoinFirstTick` | First tick within 10s |
| `TestLive_KuCoinPingPong` | Heartbeat with real exchange |
| `TestLive_KuCoinTokenRenewal` | 30s TTL override → real renewal path |
| `TestLive_BybitConnect` | Real WS connect |
| `TestLive_BybitSubscribe` | Subscription ack from real exchange |
| `TestLive_BybitFirstTick` | First tick within 10s |
| `TestLive_BybitSubscriptionLimit` | 11th topic splits on real connection |
| `TestLive_BybitDeadConnection` | TCP drop via Toxiproxy → real reconnect |
| `TestLive_QuestDBPipeline` | Real timestamps, real fields, 60s end-to-end |
| `TestLive_HealthAccuracy` | /health correct under real load, 60s |
| `TestLive_GracefulShutdown` | SIGTERM under real exchange load |
| `TestLive_GapMarkerNotFiredOnCleanSession` | Zero gaps over clean 60s window |

#### Dismissed Tests — Rationale

| Dismissed | Reason |
|---|---|
| `TestHealthEndpoint_Consistency` [L4] | L3 mock tests cover health logic; real load overkill |
| `TestWrongStreamKey_NeverOccurs` [L4] | Moved to L1 as `TestNormalizeSymbol_KeyFormat` |
| `TestGapMarkerOrder_AfterRedisReconnect` [L4] | Redundant — covered by L3 + `TestGapMarkerOrder_UnderLatency` |
| `TestILPBackpressure_QueueLimit` [L4] | Overlaps with `TestRedisBackpressure_DropAndGapMarker` |
| `[DB #1] Row count exactness` [L4b] | Subsumed by `[DB #2]` field round-trip |
| `[DB #4] WAL suspension full stack` [L4b] | Duplicates L4 WAL test |
| `[DB #7] Schema validation` [L4b] | Covered by L1 field names test + QuestDB rejection caught by [DB #2] |
| `[DB #9] Graceful shutdown full stack` [L4b] | Duplicates L4 graceful shutdown test |
| `[Redis #1] Stream persistence` [L4b] | Tests Redis AOF config not aggregator code — operational runbook item |
| `[Redis #3] Consumer group offset` [L4b] | Candle Service concern, not aggregator |
| `TestCrash_DataLossWindow` [L4b] | Insight captured in TESTING.md as known bound; not a test |
| `TestMemoryGrowth_Bounded` [L4b] | Covered by 48h canary gate |
| `TestLive_KuCoinSequenceContinuity` [L5] | Covered by `TestSnapshotDeltaMerge_Integration` [L3] |
| `TestLive_MultiSymbolInterleaving` [L5] | Covered by `[DB #8]` concurrent symbol isolation |
| `TestLive_RedisLatency` [L5] | Flaky by nature (network variance); better as periodic manual measurement |
| `TestLive_BothExchangesSimultaneous` [L5] | `[DB #8]` covers isolation; operational complexity not worth it |
| `TestVersionParity` [L5] | Replaced by `make verify-versions` deployment pre-check script |
| `TestGapMarkerOrder_UnderLatency` [L4] | Good idea but adds Toxiproxy complexity; ordering guarantee enforced by atomic pipeline in code |

#### Non-Test Deployment Gates

- **48h canary** — single symbol pair, RSS sampled every 10 min, before any production deployment
- **`make verify-versions`** — checks QuestDB + Redis versions match pinned test versions
- **Fixture freshness check** — CI fails if B2 fixtures > 25 hours old
