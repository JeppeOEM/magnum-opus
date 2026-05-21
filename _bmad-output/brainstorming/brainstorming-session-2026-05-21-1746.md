---
stepsCompleted: [1, 2, 3, 4]
inputDocuments: []
session_topic: 'Python ML service architecture for crypto trading — data access, feature engineering, and multi-workflow pipeline design'
session_goals: 'Design a ML service with full order book + candle access, multiple model types (microstructure / candle / combined), and complexity reduction workflows including PCA+HDBSCAN+XGBoost per cluster'
selected_approach: 'ai-recommended'
techniques_used: ['Morphological Analysis', 'What If Scenarios', 'Cross-Pollination']
ideas_generated: [71]
context_file: ''
session_active: false
workflow_completed: true
---

# Brainstorming Session Results

**Facilitator:** mrqdt
**Date:** 2026-05-21

## Session Overview

**Topic:** Python ML service architecture for crypto trading — data access, feature engineering, and multi-workflow pipeline design

**Goals:** Design a ML service with full order book + candle access, multiple model types (microstructure-only / candle-only / combined), and complexity reduction pipelines (standardise → PCA → HDBSCAN → cluster analysis → XGBoost per cluster)

### Context Guidance

- Existing infrastructure: Go aggregator (full L2 OB in memory, writes ticks + `snapshot_1s` to QuestDB), candle service (1s–1w OHLCV to QuestDB + Redis)
- `snapshot_1s` has 67 pre-computed features: OHLCV, OB depths (L1/L2/top10/total at open+close), OFI, spread, microstructure
- `Tick` struct carries nanosecond `TsExchange`/`TsLocal` — already sufficient for Hawkes process
- Event types currently: `update` (L2 delta) and `trade` only — NEW_LIMIT/CANCEL/MODIFY inference needed
- Downstream: trained models publish signals to `ai:{symbol}:signals` Redis stream
- Target training data: 1 month BTC+ETH on Bybit + KuCoin (~2.25B ticks, ~135 GB)

### Session Setup

Three-phase AI-recommended technique sequence:
1. **Morphological Analysis** — mapped the full design space across 6 axes
2. **What If Scenarios** — broke constraints, discovered novel combinations
3. **Cross-Pollination** — borrowed patterns from MLflow, DuckDB, Feast, sklearn Pipeline API

---

## Technique Selection

**Approach:** AI-Recommended Techniques

**Recommended Techniques:**

- **Morphological Analysis:** Systematically enumerate axes (data source × feature computation × reduction × clustering × supervised model × serving) and all combinations
- **What If Scenarios:** Break fixed assumptions — what if features were a DAG? What if each cluster trained its own feature extractor?
- **Cross-Pollination:** Steal proven patterns from ML tooling (MLflow, DuckDB, Feast) and adapt to crypto trading context

**AI Rationale:** The ML service problem has at least 6 independent design axes with non-obvious interactions. Morphological Analysis maps the full space first; What If Scenarios then smashes assumed constraints; Cross-Pollination prevents reinventing already-solved problems.

---

## Technique Execution Results

### Morphological Analysis — Design Space Map

Six axes identified and explored:

| Axis | Options Explored |
|---|---|
| Data Ingestion | QuestDB batch, Redis streams, new tick_events table, Parquet cold storage, dual-mode |
| Feature Computation | Pure batch, rolling window, event-driven streaming, hybrid, feature store, SQL-first |
| Dimensionality Reduction | PCA (tick features only), PCA (all), no reduction, feature importance pruning |
| Clustering | HDBSCAN per regime, K-means, GMM, regime-gated taxonomy |
| Supervised Models | LR + XGBoost + RF per cluster, ensemble, incremental |
| Serving / Output | Redis signals stream, REST API, Redis + QuestDB, dual online/offline |

**Key Morphological Insight:** The design space is not "one ML service" — it is two separate data regimes (1s aggregates vs tick-level events) and three separate model types (microstructure-only / candle-only / combined), each needing separate feature pipelines.

### What If Scenarios — Constraint Breaking

Key breakthroughs:
- *"What if event type inference happened in the aggregator before Redis write?"* → EventClass enriched tick flows downstream for free
- *"What if the accumulator tracked Hawkes decay?"* → O(1) per tick, ~10 lines, no library
- *"What if DuckDB read the Parquet feature store directly?"* → sub-10-second training data assembly
- *"What if regime classification lived in the candle service?"* → zero new service, piggybacked on existing 1m flush

### Cross-Pollination — Borrowed Patterns

| Source Domain | Pattern Borrowed | Applied As |
|---|---|---|
| Feast (feature store) | Offline/online parity via shared feature definitions | Single Go `features/` package for both batch + streaming |
| DuckDB | Parquet predicate pushdown | Training data assembly: `SELECT * FROM features WHERE date BETWEEN ...` |
| sklearn Pipeline API | Composable transform steps | Python `FeaturePipeline` class: load → normalise → PCA → cluster → predict |
| MLflow | Experiment tracking + model registry | Lightweight `models.json` + dashboard model version table |
| Airflow | DAG-based dependency execution | Feature computation ordering (Hawkes needs timestamps before aggressiveness) |
| XGBoost warm-start | Incremental retraining | `xgb_model` param extends existing model with new week's data |

---

## Complete Idea Inventory (71 ideas)

### Theme A — Data Capture (Aggregator Changes)

| # | Title | Core Concept |
|---|---|---|
| 30 | Go-First Architecture | Go owns capture → features → Redis; Python owns models only. Boundary = `features:{exchange}:{symbol}` Redis stream |
| 31 | Event Class Inference | In `handleTick()`, read `w.book.Level()` before `w.book.Apply()`. Compare old vs new size: old=0,new>0=NEW_LIMIT; old>0,new=0=CANCEL; old>0,new>0=MODIFY; type==trade=TRADE |
| 45 | Event Class — Zero Overhead | `w.book.Level(side, price)` pre-apply → enrich `tick.EventClass` → flows to Redis AND tick_events. One method call + one comparison per tick |
| 46 | tick_events ILP Extension | Extend existing `ILPWriter` with second write to `tick_events (ts, ts_local, exchange, symbol, side, price, size, event_class, seq)`. Same fire-and-forget pattern. TTL 35d |

**Data Volume:** ~450–1300 ticks/sec across BTC+ETH × 2 exchanges → ~75M events/day → ~2.25B/month → ~135 GB compressed in QuestDB

### Theme B — Feature Engineering (Candle Service Changes)

| # | Title | Core Concept |
|---|---|---|
| 32 | C4 Dual-Mode | Same Go feature functions for offline batch (QuestDB) and online streaming (Redis). Train/inference parity by construction |
| 43 | Spread + Imbalance First-Class | Compute `spread_bps` and `ob_imbalance` per tick. Track `spread_variance_1s` — intra-second spike = momentary liquidity crisis |
| 47 | Hawkes Intensity | Add `hawkesDecaySum float64`, `hawkesLastTsMs int64` to accumulator. In `Apply()`: `decay = decay × exp(-10×dt) + 0.8`. Emit `HawkesIntensity = 0.02 + decay`. O(1) per tick, ~10 lines |
| 48 | Microprice in CurrentBar() | At flush: `microprice = (bestAsk×bidL1 + bestBid×askL1) / (bidL1+askL1)`. Also emit `MicropriceMidDelta = microprice - mid`. Delta is the directional signal |
| 49 | Arrival by Event Class | Split `bidOrderArrivals` into `bidNewLimitCount` + `bidModifyCount`. Route by `tick.EventClass`. `new_limit_rate / cancel_rate` = direct spoofing signal |
| 50 | Trade Aggressiveness | Extend `BestQuote` with `BidSize/AskSize`. In `Apply()` for trades: `aggrScore = tradeSize / l1Size`. Accumulate mean. Scores >1.0 = trade swept through L1 |
| 51 | Cancel Bias | In `CurrentBar()`: `cancelBias = (bidCancels - askCancels) / (bidCancels + askCancels + 1)`. Free from existing counters. Positive = bearish (bids pulled) |
| 52 | Depth at 5 Levels | Extend `DepthSnapshot`: `BidP1..BidP5`, `BidL1..BidL5`, `AskP1..AskP5`, `AskL1..AskL5`. 20 new columns in `snapshot_1s`. Enables depth slope in Python |
| 53 | Regime Classifier | New goroutine in candle service. Reads 1m bar on flush. Rule-based: `ofi_sign_consistency`, `buy_vol_ratio`, `rolling_vol`, `spread_mean` → 5 regimes. Publishes `regime:{exchange}:{symbol}` |

**snapshot_1s new columns:** `microprice_close`, `microprice_mid_delta`, `hawkes_intensity`, `cancel_bias`, `trade_aggressiveness`, `bid_new_limit_count`, `ask_new_limit_count`, `bid_modify_count`, `ask_modify_count`, `bid_p1..p5`, `ask_p1..p5`, `bid_l3..l5`, `ask_l3..l5` (~20 new columns)

### Theme C — OB Depth & Queue Features

| # | Title | Core Concept |
|---|---|---|
| 19 | Level Age Proxy | Track when price level first appeared after being zero. `level_age_ms` = duration at level = queue position proxy. Older = nearer front of queue |
| 20 | Queue Depletion Velocity | `dQ/dt = (bid_l1_open - bid_l1_close) / 1000`. Already derivable from existing depth open/close columns |
| 21 | Depth Slope | Linear regression on 5-level depth: `β = polyfit([0,1,2,3,4], [l1..l5], 1)[0]`. Steep = thin near-touch. Flat = deep market. Computed in Python at training time |
| 22 | Book Pressure Ratio | `book_pressure = bid_top10 / ask_top10`. Values >1.2 = bid-heavy. Combine with OFI for static+dynamic signal |
| 23 | Liquidity Concentration HHI | `HHI = Σ(vol_i / total_vol)²` across 5 levels. High HHI = fragile book. Computed per side in Python |
| 24 | Arrival-Cancel by Price Distance | Segment arrivals/cancels into [0-1bp, 1-5bp, 5-20bp, 20bp+] buckets. Far-out cancel spikes = spoofing signature |
| 25 | Hidden Liquidity Score | Count level drop-to-zero + immediate refill events per second. `iceberg_refresh_count / total_arrivals`. Requires tick_events data |

**Note:** Ideas #19, #24, #25 require `tick_events` table. Ideas #21, #22, #23 derivable from 5-level depth at Python training time.

### Theme D — Python ML Service — Foundation + Training Pipeline

| # | Title | Core Concept |
|---|---|---|
| 8 | Two-Pass Build | Pass 1: tick_events → `tick_features_1s` QuestDB. Pass 2: JOIN with `snapshot_1s` → unified Parquet per symbol-month |
| 9 | Label Engineering | Forward return (Ns), realised vol, large trade within 5s, mid-price direction. Label choice defines what clusters mean |
| 10 | Symbol-Regime Segmentation | Don't cluster BTC+ETH together. Group by regime first. HDBSCAN runs 5×: once per regime per symbol |
| 11 | Rolling Window Tensor | One row = K=30 seconds of features (K×F flattened). XGBoost learns which lags matter via feature importance |
| 12 | Parquet Feature Store | Hive-partitioned: `features/exchange=.../symbol=.../date=.../part.parquet`. DuckDB reads with predicate pushdown. Training = one SQL query |
| 13 | Gap Masking | Drop rows where `gap_count > 0`. Keep `gap_mask` column. Gap-adjacent bars = separate analysis (institutional trading around reconnects?) |
| 14 | Symbol-Local Z-Score | Rolling 24h mean+std per feature per symbol. Stationary features across price regimes. Online inference uses same rolling stats |
| 15 | PCA on Tick Features Only | PCA the ~15 tick-derived features → 4–5 components. Append raw candle features. Input to HDBSCAN = [PCA components] + [candle features] = 12–13 dims |
| 16 | Incremental Retraining | XGBoost warm-start on new week's data. Only refit PCA+HDBSCAN if AMI drops below 0.7. Alert via `ai:model:drift` Redis stream |
| 17 | Purged Walk-Forward CV | Train weeks 1–3, val week 4, test final 3 days. Gap = 2×prediction_horizon between train/val to prevent label leakage |
| 18 | Feature Importance Pruning | After first fit per cluster: drop features with cumulative importance <5%. Refit. Each cluster gets its own minimal feature set |
| 41 | Regime Classifier Rules | 5 regimes: TRENDING_UP, TRENDING_DOWN, HIGH_VOL, THIN_BOOK, RANGING. Rule-based thresholds on 60-second rolling stats |
| 42 | Regime-Gated Feature Store | `regime` column in every feature row. HDBSCAN runs independently per regime. Separate XGBoost per regime×cluster combination |

### Theme E — ML Service — Architecture & Inference

| # | Title | Core Concept |
|---|---|---|
| 1–7 | Data Ingestion Axes | A1: QuestDB batch. A2: Redis candle stream. A3: Redis OB stream. A4: new tick capture. A5: dual-mode. A6: Parquet cold storage |
| 26–29 | Tick Logger Decision | Go standalone service (winner). Same Redis consumer group pattern as candle service. Python logger feasible but GIL risk at burst |
| 33 | Feature Service Integration | **No new service.** Aggregator: event class + tick_events. Candle service: Hawkes + microprice + arrival types + aggressiveness + cancel bias + depth 5L + regime |
| 34–40 | Real-Time Feature Computation | Arrival intensity, signed OFI, trade aggressiveness, cancel bias, Hawkes, microprice, queue sizes — all in accumulator pattern |

### Theme F — Dashboard ML Page

| # | Title | Core Concept |
|---|---|---|
| 54 | ML Nav Page | `dbc.NavLink("ML", href="/ml")`. Tabs: Data / Clusters / Models / Live. `layout_ml.py` + `callbacks_ml.py`. Same dark theme |
| **DATA TAB** | | |
| 55 | Feature Correlation Heatmap | Pearson matrix across ~80 features. Hierarchically ordered. Click cell → scatter plot |
| 56 | Feature Time Series Explorer | Z-score normalised multi-feature overlay on candlestick. Time range slider |
| 57 | Regime Timeline | Color band behind candlestick, one color per regime. Reads `regime:{exchange}:{symbol}` Redis stream |
| 58 | Tick Event Rate Panel | 4-gauge real-time: new_limit/s, cancel/s, modify/s, trade/s. `cancel_rate / new_limit_rate` = spoofing gauge |
| 59 | Depth Profile Visualiser | Bid (left/green) vs ask (right/red) 5-level depth bar chart. Slope visible at a glance. Updates 1s |
| 60 | Microprice vs Mid Divergence | Two overlaid lines + delta bar chart below. Positive delta = bullish queue pressure |
| **CLUSTERS TAB** | | |
| 61 | PCA 2D Scatter | Historical cluster assignments. One trace per cluster. Hover: timestamp, regime, forward return |
| 62 | Cluster Return Distribution | Violin/box per cluster at selectable forward horizons (5s/30s/5m). Non-overlapping = real alpha |
| 63 | Cluster Transition Matrix | Markov heatmap. Sticky diagonal = regimes persist. Strong off-diagonal = sequential signals |
| 64 | Feature Importance Per Cluster | Horizontal bar chart top-10 features per cluster model. Reveals what each cluster "is about" |
| 65 | Cluster Timeline on Candlestick | Same pattern as regime timeline but with cluster labels. Validates/invalidates clustering visually |
| **MODELS TAB** | | |
| 66 | Rolling Model Accuracy | 24h rolling accuracy line per cluster. Dotted baseline at 50%. Red alert band below threshold |
| 67 | Confusion Matrix | `go.Heatmap` predicted vs actual. Catches "predict all neutral" accuracy inflation |
| 68 | Model Version Registry | `dash_table.DataTable`: model_id, symbol, cluster, trained_date, val_acc, test_acc, status. Click → load importances |
| **LIVE TAB** | | |
| 69 | Live Signal Feed | Real-time table: last 50 `ai:{symbol}:signals` entries. Auto-scroll. Color by direction |
| 70 | Live Feature Gauges | Microprice delta, Hawkes intensity, cancel bias, OB imbalance — all as gauges. Current regime + cluster badges |
| 71 | Live PCA Position | Historical scatter background + current position as moving dot. Watch market drift between cluster regions |

---

## Idea Organization and Prioritization

### Prioritisation: Impact × Feasibility × Dependency Order

| Priority | Epic | Theme | Effort | Blocker For |
|---|---|---|---|---|
| 1 | Aggregator tick events | A | Small | Everything tick-level |
| 2 | Candle service features | B | Medium | ML training data quality |
| 3 | 1-month data collection | — | Time (passive) | Epic D/E |
| 4 | ML service foundation | D | Large | Training pipeline |
| 5 | ML training pipeline | D | Large | Inference |
| 6 | ML inference pipeline | E | Medium | Live signals |
| 7 | Dashboard ML page | F | Medium | Visualization |

### Quick Wins (can start immediately)

1. **Microprice in CurrentBar()** — zero new state, pure derivation, ~5 lines in accumulator
2. **Cancel bias** — free from existing `bidCancelCount/askCancelCount`, ~2 lines in `CurrentBar()`
3. **tick_events ILP writer** — extend existing writer, same fire-and-forget pattern
4. **ML nav page skeleton** — `layout_ml.py` with tabs + placeholder charts, no data needed yet

### Breakthrough Concepts

1. **EventClass inference in aggregator pre-apply** — unlocks true event type breakdown for all downstream consumers at zero runtime cost
2. **Regime-gated HDBSCAN** — separate cluster taxonomies per regime; each model is excellent within its context rather than mediocre across all
3. **Hawkes intensity in accumulator** — O(1) self-exciting behavior estimate in ~10 lines of Go; most retail systems never implement this
4. **Live PCA position** — watching the market drift between cluster regions in real-time is operationally useful AND visually compelling

---

## Action Plans — Epic Breakdown

---

### EPIC A — Aggregator: Tick Events Capture

**Goal:** Persist individual tick events with inferred event class to QuestDB. Zero new service.

**Stories:**
1. `A.1` Expose `orderbook.Level(side, price) float64` method
2. `A.2` Add `EventClass` field to `exchange.Tick` struct; infer in `handleTick()` pre-apply
3. `A.3` Create `tick_events` QuestDB migration (schema below)
4. `A.4` Extend `ILPWriter` to write `tick_events` on every live tick

**tick_events schema:**
```sql
CREATE TABLE IF NOT EXISTS tick_events (
    ts          TIMESTAMP,
    ts_local    TIMESTAMP,
    exchange    SYMBOL CAPACITY 8 INDEX,
    symbol      SYMBOL CAPACITY 256 INDEX,
    event_class SYMBOL CAPACITY 8,   -- new_limit, cancel, modify, trade, snapshot
    side        SYMBOL CAPACITY 4,   -- bid, ask
    price       DOUBLE,
    size        DOUBLE,
    seq         LONG
) TIMESTAMP(ts) PARTITION BY DAY TTL 35d WAL
DEDUP UPSERT KEYS(ts, exchange, symbol, seq);
```

**Success:** `tick_events` accumulates ~75M rows/day for BTC+ETH × 2 exchanges. Event class breakdown visible in QuestDB UI.

---

### EPIC B — Candle Service: Extended Microstructure Features

**Goal:** Add ~20 new columns to `snapshot_1s`. No new service.

**Stories:**
1. `B.1` Extend `BestQuote` with `BidSize/AskSize`; extend `DepthSnapshot` to 5 levels per side
2. `B.2` Add Hawkes intensity to accumulator (`hawkesDecaySum`, `hawkesLastTsMs`, emit in `CurrentBar()`)
3. `B.3` Add microprice + microprice_mid_delta to `CurrentBar()`
4. `B.4` Split arrival counters by event class (`bidNewLimitCount`, `bidModifyCount`); add `tradeAggressiveness`
5. `B.5` Add `cancelBias` to `CurrentBar()`
6. `B.6` Write `snapshot_1s` migration for new columns
7. `B.7` Add `RegimeClassifier` goroutine to candle service (reads 1m flush, publishes `regime:{exchange}:{symbol}`)

**Success:** `snapshot_1s` rows contain `hawkes_intensity`, `microprice_close`, `microprice_mid_delta`, `cancel_bias`, `trade_aggressiveness`, 5-level depth per side, new limit + modify counts.

---

### EPIC C — Data Collection (Passive)

**Goal:** Collect 1 month of BTC+ETH tick data on Bybit + KuCoin.

**Stories:**
1. `C.1` Deploy Epic A changes to production
2. `C.2` Verify tick_events write rate matches expected ~75M rows/day
3. `C.3` Monitor QuestDB disk usage (target: ~135 GB at 35 days)
4. `C.4` Verify `snapshot_1s` extended columns populating correctly

**Success:** 30 days of `tick_events` + `snapshot_1s` for BTCUSDT + ETHUSDT on Bybit + KuCoin. Data quality report confirms <0.1% gap rate.

---

### EPIC D — Python ML Service: Foundation + Training Pipeline

**Goal:** New `ml-service/` Python service. Reads QuestDB → builds feature matrix → trains models → saves to disk.

**Stories:**
1. `D.1` Service scaffold: `config.py` (pydantic-settings), `Dockerfile`, docker-compose entry (profile `ml`)
2. `D.2` QuestDB reader: `data/reader.py` — DuckDB-over-HTTP queries, returns DataFrames
3. `D.3` Parquet feature store: `data/store.py` — Hive-partitioned write/read, gap masking
4. `D.4` Feature pipeline: `features/pipeline.py` — z-score normalise, rolling windows, depth slope, HHI, PCA
5. `D.5` Regime splitter: `training/regime.py` — load data, split by `regime` column
6. `D.6` HDBSCAN clustering: `training/cluster.py` — PCA tick features + raw candle features, per-regime
7. `D.7` Supervised models: `training/models.py` — LR + XGBoost + RF per cluster, purged walk-forward CV
8. `D.8` Model registry: `training/registry.py` — save models to `models/` dir, write `models.json` manifest
9. `D.9` Training entrypoint: `train.py` — full pipeline from QuestDB → trained models on disk

**Feature groups for training:**

```python
TICK_FEATURES = [
    'hawkes_intensity', 'cancel_bias', 'trade_aggressiveness',
    'bid_new_limit_count', 'ask_new_limit_count',
    'bid_modify_count', 'ask_modify_count',
    'ofi', 'ofi_l1', 'microprice_mid_delta',
    'quote_stuff_ratio', 'trade_sign_autocorr',
]

CANDLE_FEATURES = [
    'log_return',        # derived: log(close/lag_close)
    'realized_vol',
    'volume_zscore',     # derived: rolling
    'buy_vol_ratio',     # derived: buy_volume/volume
    'wick_ratio_upper',  # derived
    'wick_ratio_lower',  # derived
    'momentum_30s',      # derived: lag 30
]

OB_FEATURES = [
    'ob_imbalance_l1',   # derived
    'spread_bps',        # derived
    'book_pressure',     # derived
    'depth_slope_bid',   # derived from l1..l5
    'depth_slope_ask',
    'microprice_close',
    'bid_depth_l1_close', 'ask_depth_l1_close',
    'liquidity_hhi_bid', # derived
    'liquidity_hhi_ask',
]
```

**Success:** `python train.py --symbol BTCUSDT --exchange bybit --days 30` completes without error, writes model files and `models.json`.

---

### EPIC E — Python ML Service: Inference Pipeline

**Goal:** Live inference consuming Redis feature stream → signals to `ai:{symbol}:signals`.

**Stories:**
1. `E.1` Redis feature consumer: `inference/consumer.py` — subscribes to `features:{exchange}:{symbol}` OR polls `snapshot_1s` every 1s
2. `E.2` Regime loader: `inference/regime.py` — subscribes to `regime:{exchange}:{symbol}`, maintains current regime per symbol
3. `E.3` Model loader: `inference/loader.py` — loads correct model from registry by `(symbol, regime, cluster)`
4. `E.4` Inference engine: `inference/engine.py` — applies feature pipeline, predicts cluster, runs cluster model, emits signal
5. `E.5` Signal publisher: `inference/publisher.py` — writes to `ai:{symbol}:signals` Redis stream with confidence + metadata
6. `E.6` FastAPI health endpoint: `/health`, `/metrics` (Prometheus)

**Signal schema (Redis stream):**
```
ai:{symbol}:signals fields:
  ts, exchange, symbol, regime, cluster,
  signal (BUY/SELL/NEUTRAL), confidence,
  hawkes_intensity, microprice_mid_delta,
  model_id, feature_count
```

**Success:** Signals appear in `ai:BTCUSDT:signals` within 2 seconds of each new `snapshot_1s` row.

---

### EPIC F — Dashboard: ML Page

**Goal:** 4-tab ML page in existing Dash app. Data / Clusters / Models / Live.

**Stories:**
1. `F.1` Page scaffold: `layout_ml.py` (4 tabs, placeholders), `callbacks_ml.py` (skeleton), add nav link
2. `F.2` Data tab: feature correlation heatmap + regime timeline + tick event rate gauges
3. `F.3` Data tab: depth profile visualiser + microprice divergence chart
4. `F.4` Clusters tab: PCA 2D scatter + cluster return distributions
5. `F.5` Clusters tab: cluster transition matrix + feature importance per cluster
6. `F.6` Clusters tab: cluster timeline overlay on candlestick
7. `F.7` Models tab: rolling accuracy + confusion matrix + model version registry table
8. `F.8` Live tab: signal feed + live feature gauges + live PCA position

**Success:** `/ml` page renders in browser with real data for BTCUSDT. Live tab updates every 5 seconds.

---

## Session Summary and Insights

### Key Achievements

- **71 breakthrough ideas** generated across data capture, feature engineering, ML pipeline design, and visualisation
- **Zero new Go services** required — all Go changes integrate into aggregator coordinator Worker and candle service accumulator
- **Complete architecture** from raw WebSocket ticks → feature vector → trained model → live signal → dashboard visualisation
- **Implementation-ready epic breakdown** with concrete story list, schemas, and code patterns

### Creative Breakthroughs

1. **EventClass inference is free** — the coordinator Worker already has pre-apply and post-apply book state on every tick. One `w.book.Level()` call infers NEW_LIMIT/CANCEL/MODIFY with no architectural overhead.

2. **Hawkes process in 10 lines of Go** — most microstructure literature treats Hawkes as complex. The recursive decay formula is O(1) per event and slots directly into the accumulator's existing Welford-pattern state.

3. **Regime-first clustering** — clustering across all market regimes produces a "garbage cluster" problem. Running HDBSCAN independently per regime produces models that are excellent within their context. This is the key architectural decision that separates mediocre ML from tradeable ML.

4. **The data already flows** — 85% of the desired features are derivable from data already in `snapshot_1s` or trivially extendable in the accumulator. The 15% requiring new infrastructure (`tick_events`) unlocks the deepest UHF features (Hawkes real, queue depletion, hidden liquidity).

### Implementation Sequence

```
Week 1–2:   EPIC A (aggregator tick events) — small, foundational
Week 2–3:   EPIC B (candle service features) — parallel-able with A
Month 1:    EPIC C (passive data collection) — deploy and wait
Month 2:    EPIC D (ML service training) — 1 month data now available
Month 2–3:  EPIC E (inference pipeline) — models trained
Ongoing:    EPIC F (dashboard) — can build UI shells during C
```

### What Makes This Architecture Strong

- **No new Go services** — aggregator and candle service absorb all new Go computation
- **Train/inference parity** — same feature definitions in both paths (shared `features/` package)
- **Regime isolation** — separate model per regime prevents cross-contamination of market states
- **Incremental by design** — can start trading on `snapshot_1s` features (no tick_events needed) while waiting for 1 month of tick data
- **Observable** — every layer has a corresponding dashboard view; pipeline health is visually verifiable
