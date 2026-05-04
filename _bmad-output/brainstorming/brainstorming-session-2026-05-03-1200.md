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
