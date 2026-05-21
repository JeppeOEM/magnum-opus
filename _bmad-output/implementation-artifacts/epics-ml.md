---
project: magnum-opus
type: epics
stepsCompleted: [1, 2, 3]
epic_range: 35–39
---

# ML Service Epics (35–39): Mean Reversion & Statistical Arbitrage

## Overview

**Project**: magnum-opus ML service — Python prediction layer over 1-second microstructure data
**Scope**: Epics 35–39 add microstructure intelligence to the candle service, build a full ML training + inference pipeline, and extend the dashboard with an ML analytics page.
**Strategy focus**: Fast mean reversion and statistical arbitrage (BTC/ETH) using microprice deviation signals and spread z-score labels.
**Data source**: `snapshot_1s` QuestDB table only (no tick-level capture — deferred to UHF sprint).

---

## Requirements Inventory

### Functional Requirements

- FR1: Candle service emits Hawkes intensity, microprice, microprice_mid_delta, cancel_bias, trade_aggressiveness per bar
- FR2: Candle service emits 5-level depth (L3/L4/L5) per side at open and close
- FR3: Candle service emits buy_vwap_deviation_bps and sell_vwap_deviation_bps (VWAP per side vs mid) per bar
- FR4: Candle service publishes 5-regime classification to `regime:{exchange}:{symbol}` Redis key every minute
- FR5: Data quality gate validates ≥28 days of coverage, <5% null rate for ML columns
- FR6: ML training pipeline assembles Hive-partitioned Parquet feature store from QuestDB via DuckDB
- FR7: Feature assembly computes Python-side rolling features: Hurst exponent, OU half-life, Kyle's lambda, net_buy_fraction
- FR8: Labels: microprice deviation reversion (REVERT/TREND/FLAT) at N=3 and N=10 horizons; spread z-score convergence (CONVERGE/DIVERGE/NEUTRAL)
- FR9: Stat arb labels use rolling ADF p-value gate and dynamic Kalman-filter hedge ratio
- FR10: Regime isolation: HDBSCAN clustering per regime, separate XGBoost/RF/LR per (symbol, regime, cluster)
- FR11: XGBoost uses multi:softprob objective, SHAP TreeExplainer output stored with each model artifact
- FR12: Savitzky-Golay smoothing applied to OFI and spread features as preprocessing step before StandardScaler
- FR13: Purged walk-forward CV for all model evaluation (no random splits on time-series)
- FR14: Model registry persists scaler + PCA + model weights + SHAP summary + metadata as models.json on disk
- FR15: Inference engine polls snapshot_1s every 1s, reads regime from Redis, publishes signals to `ai:{symbol}:signals`
- FR16: Stat arb: BTC/ETH spread z-score JOIN at inference time; convergence signal published to `ai:BTC-ETH-spread:signals`
- FR17: Dashboard /ml page with 4 tabs: Data, Clusters, Models, Live

### Non-Functional Requirements

- NFR1: No new Go services — all Go changes in existing candle-service
- NFR2: Hawkes intensity update O(1) per tick (decay formula, no loop)
- NFR3: Inference loop latency < 500ms per symbol (QuestDB poll + model predict)
- NFR4: RegimeClassifier lives in internal/regime/ — no IO, no goroutines inside the struct; goroutine lives in cmd/
- NFR5: All Python uses pydantic-settings for config, structlog for logging, no exchange deps in ml-service
- NFR6: microprice NaN guard: when bid_l1 + ask_l1 = 0, fall back to mid-price

### Additional Requirements

- AR1: Model hot-reload on SIGHUP (inference engine reloads models.json without restart)
- AR2: Cross-asset JOIN requires both BTC + ETH snapshot_1s rows within 1s of each other
- AR3: RegimeClassifier uses simple heuristics (not ML) — threshold-based rules over 1m candle stream
- AR4: XGBoost class weights: REVERT and TREND upweighted 2× vs FLAT (FLAT is majority class — prevent model always predicting FLAT)
- AR5: Feature interactions: `ofi_l1 × spread_mean` and `microprice_mid_delta × hawkes_intensity` added as explicit engineered columns
- AR6: Rolling Hurst exponent used as gating condition in label generator — only emit REVERT label when H < 0.5 in that window

---

## FR Coverage Map

| FR   | Epic 35 | Epic 36 | Epic 37 | Epic 38 | Epic 39 |
|------|---------|---------|---------|---------|---------|
| FR1  | ✅      |         |         |         |         |
| FR2  | ✅      |         |         |         |         |
| FR3  | ✅      |         |         |         |         |
| FR4  | ✅      |         |         |         |         |
| FR5  |         | ✅      |         |         |         |
| FR6  |         |         | ✅      |         |         |
| FR7  |         |         | ✅      |         |         |
| FR8  |         |         | ✅      |         |         |
| FR9  |         |         | ✅      |         |         |
| FR10 |         |         | ✅      |         |         |
| FR11 |         |         | ✅      |         |         |
| FR12 |         |         | ✅      |         |         |
| FR13 |         |         | ✅      |         |         |
| FR14 |         |         | ✅      |         |         |
| FR15 |         |         |         | ✅      |         |
| FR16 |         |         |         | ✅      |         |
| FR17 |         |         |         |         | ✅      |

---

## Epic List

| #  | Title                                      | Goal                                                                                    |
|----|--------------------------------------------|-----------------------------------------------------------------------------------------|
| 35 | Candle Service Microstructure Intelligence | Hawkes, microprice_mid_delta, cancel_bias, trade_aggressiveness, 5-level depth, RegimeClassifier |
| 36 | Data Collection Readiness                  | Validate 30 days of quality snapshot_1s data before ML training begins                  |
| 37 | ML Training Pipeline                       | ml-service: DuckDB feature assembly, label generation, clustering, per-cluster training  |
| 38 | ML Inference Pipeline                      | Real-time prediction loop publishing to ai:{symbol}:signals Redis streams               |
| 39 | Dashboard ML Page                          | 4-tab /ml page: Data, Clusters, Models, Live                                            |

---

## Epic 35: Candle Service Microstructure Intelligence

**Goal**: Add 7 new microstructure features to `snapshot_1s` and extend depth to 5 levels. Add a RegimeClassifier that publishes market regime to Redis every minute.

### Story 35-1: Hawkes Intensity Accumulator

As a trading system developer,
I want the candle service to emit a Hawkes process intensity per 1-second bar,
So that the ML model can measure self-exciting order arrival bursts as a feature.

**Acceptance Criteria:**

**Given** a fresh Accumulator with no prior ticks
**When** Apply() is called for the first tick
**Then** hawkesDecaySum = 0 + α = 0.8 (β=10.0, α=0.8)

**Given** two Apply() calls separated by dt seconds
**When** the second tick arrives at time t+dt
**Then** hawkesDecaySum = prev × exp(-10.0 × dt) + 0.8

**Given** any number of ticks in a bar
**When** CurrentBar() is called
**Then** Bar.HawkesIntensity is non-nil and equals the current decaySum

**Given** BarReset() is called at the second boundary
**When** the first tick of the next bar arrives
**Then** hawkesDecaySum is NOT reset — it carries over between bars (continuous process)

**Given** a migration is applied to a running QuestDB
**When** the snapshot_1s table is queried
**Then** a `hawkes_intensity` DOUBLE column exists (nullable, default null)

**And** the QuestDB ILP writer writes hawkes_intensity for every bar where HawkesIntensity != nil

### Story 35-2: Microprice and microprice_mid_delta

As a trading system developer,
I want the candle service to emit microprice and microprice_mid_delta per bar,
So that the ML model has a fair-value reference and deviation signal for mean reversion.

**Acceptance Criteria:**

**Given** bid depth > 0 and ask depth > 0
**When** Microprice() is computed
**Then** microprice = (best_ask × bid_depth_l1 + best_bid × ask_depth_l1) / (bid_depth_l1 + ask_depth_l1)

**Given** bid_depth_l1 + ask_depth_l1 = 0 (empty book edge case)
**When** Microprice() is called
**Then** it returns mid-price = (best_bid + best_ask) / 2 as fallback (no NaN, no panic)

**Given** a valid microprice and mid-price
**When** MicropriceMidDelta() is computed
**Then** result = (microprice - mid) / mid × 10000.0 (in basis points)
**And** positive values mean microprice > mid (book weighted toward upward reversion)

**Given** no close OB quote exists (hasCloseQuote=false)
**When** CurrentBar() is called
**Then** Bar.Microprice and Bar.MicropriceMidDelta are nil

**And** `microprice` DOUBLE and `microprice_mid_delta` DOUBLE columns exist in snapshot_1s DDL
**And** the ILP writer emits both columns

### Story 35-3: Cancel Bias and Trade Aggressiveness

As a trading system developer,
I want cancel_bias and trade_aggressiveness in every 1-second bar,
So that the ML model can detect spoofing risk and trade pressure relative to available liquidity.

**Acceptance Criteria:**

**Given** bidCancelCount=6, askCancelCount=2
**When** CurrentBar() is called
**Then** cancel_bias = (6 - 2) / (6 + 2 + 1) = 0.444...

**Given** bidCancelCount=0, askCancelCount=0
**When** CurrentBar() is called
**Then** cancel_bias = 0.0 / 1.0 = 0.0 (no panic, no NaN)

**Given** total volume = V, bid_depth_l1_close = D
**When** CurrentBar() is called
**Then** trade_aggressiveness = V / (D + 1e-9)

**Given** no OB activity this bar (hasOBActivity=false)
**When** CurrentBar() is called
**Then** Bar.CancelBias is nil

**Given** no close depth snapshot (hasCloseDepth=false)
**When** CurrentBar() is called
**Then** Bar.TradeAggressiveness is nil

**And** `cancel_bias` DOUBLE and `trade_aggressiveness` DOUBLE columns exist in snapshot_1s DDL
**And** ILP writer emits both columns

### Story 35-4: 5-Level Depth Snapshot Extension

As a trading system developer,
I want the candle service to capture depth at L3, L4, and L5 in addition to L1, L2, top10,
So that the ML model has fine-grained order book shape as features.

**Acceptance Criteria:**

**Given** a book with ≥5 bid levels and ≥5 ask levels
**When** ComputeDepthSnapshot() is called
**Then** DepthSnapshot.BidL3 = cumulative bid volume through level 3
**And** DepthSnapshot.BidL4 = cumulative bid volume through level 4
**And** DepthSnapshot.BidL5 = cumulative bid volume through level 5
**And** same for AskL3, AskL4, AskL5

**Given** a book with only 2 bid levels
**When** ComputeDepthSnapshot() is called
**Then** BidL3 = BidL4 = BidL5 = BidTotal (cap at total, same behaviour as existing L2 capping)

**Given** DepthSnapshot is computed at bar open and bar close
**When** CurrentBar() is called
**Then** Bar has BidDepthL3Open, BidDepthL3Close, AskDepthL3Open, AskDepthL3Close (and L4, L5 variants — 12 new fields total)

**And** 12 new DOUBLE columns added to snapshot_1s DDL migration
**And** ILP writer emits all 12 new depth columns

### Story 35-5: RegimeClassifier — 5-Regime Redis Publisher

As a trading system developer,
I want the candle service to classify market regime each minute and publish it to Redis,
So that the ML inference engine can load the correct per-regime model without running the classifier itself.

**Acceptance Criteria:**

**Given** a Classifier reading from `candles:close:{exchange}:{symbol}` Redis stream (1m bars)
**When** spread_mean of last bar > 2 × rolling median spread
**Then** regime = THIN_BOOK

**Given** realized_vol of last 5 bars > configurable threshold (default 0.002)
**When** Classify() is called
**Then** regime = HIGH_VOL (takes precedence over RANGING, not over THIN_BOOK)

**Given** |close_change_5bar| / mid > configurable threshold (default 0.001)
**When** Classify() is called
**Then** regime = TRENDING_UP if change positive, TRENDING_DOWN if negative

**Given** none of the above conditions met
**When** Classify() is called
**Then** regime = RANGING

**Given** Classify() produces a regime string
**When** Publish() is called
**Then** Redis SET `regime:{exchange}:{symbol}` = regime string with TTL 180 seconds

**Given** `candles:close:{exchange}:{symbol}` stream has fewer than 5 entries
**When** Classify() is called
**Then** regime = RANGING (insufficient data fallback)

**And** Classifier struct lives in `candle-service/internal/regime/` (no goroutines inside the struct)
**And** a single goroutine started from `cmd/candle/main.go` runs the classify-and-publish loop
**And** config settings: `CANDLE_REGIME_INTERVAL_S` (default 60), `CANDLE_REGIME_VOL_THRESHOLD`, `CANDLE_REGIME_TREND_THRESHOLD`

### Story 35-6: Buy/Sell VWAP Deviation from Mid

As a trading system developer,
I want the candle service to emit buy-side and sell-side VWAP deviation from mid per bar,
So that the ML model has the #3 SHAP feature (per arxiv:2602.00776) — asymmetric price pressure signals for mean reversion.

**Acceptance Criteria:**

**Given** trade ticks arrive with isTrade=true and side="buy" or side="sell"
**When** Apply() processes each trade tick
**Then** buyVwapNumer += tradePrice × tradeSize; buyVwapDenom += tradeSize (for buy side)
**And** sellVwapNumer += tradePrice × tradeSize; sellVwapDenom += tradeSize (for sell side)

**Given** buyVwapDenom > 0 at CurrentBar() time
**When** Bar is computed
**Then** buy_vwap = buyVwapNumer / buyVwapDenom
**And** buy_vwap_deviation_bps = (buy_vwap - mid) / mid × 10000

**Given** sellVwapDenom > 0 at CurrentBar() time
**When** Bar is computed
**Then** sell_vwap = sellVwapNumer / sellVwapDenom
**And** sell_vwap_deviation_bps = (sell_vwap - mid) / mid × 10000

**Given** no buy trades in the bar (buyVwapDenom = 0)
**When** Bar is computed
**Then** Bar.BuyVwapDeviationBps is nil (no division by zero)

**Given** no close OB quote exists (mid not available)
**When** Bar is computed
**Then** Bar.BuyVwapDeviationBps and Bar.SellVwapDeviationBps are nil

**And** BarReset() zeroes buyVwapNumer, buyVwapDenom, sellVwapNumer, sellVwapDenom
**And** `buy_vwap_deviation_bps` DOUBLE and `sell_vwap_deviation_bps` DOUBLE columns added to snapshot_1s DDL
**And** ILP writer emits both columns

---

## Epic 36: Data Collection Readiness

**Goal**: Validate that 30 days of quality snapshot_1s data has been collected before ML training begins. Epic 36 is a passive wait + tooling epic — the stories produce scripts and documentation, not production services.

### Story 36-1: Data Quality Monitoring Script

As a developer,
I want a script that reports snapshot_1s data quality metrics,
So that I can monitor collection progress and catch gaps or null explosions early.

**Acceptance Criteria:**

**Given** `python3 scripts/check_data_quality.py --symbol BTCUSDT --exchange bybit`
**When** the script runs
**Then** it prints: total row count, rows per day (last 35 days), null rate for each ML-critical column, and any date gaps > 1 hour

**Given** a QuestDB instance at `QUESTDB_HTTP_ADDR` (default http://localhost:9000)
**When** the script queries snapshot_1s
**Then** ML-critical columns checked are: `hawkes_intensity`, `microprice_mid_delta`, `cancel_bias`, `trade_aggressiveness`, `bid_depth_l3_close`, `realized_vol`, `ofi`, `spread_mean`

**Given** date gaps larger than 1 hour exist
**When** the script runs
**Then** each gap is printed with start time, end time, and duration in hours

**And** script exits with code 0 on success, 1 on connection error

### Story 36-2: Collection Readiness Gate

As a developer,
I want a CLI gate that exits 0 when data is ready for ML training,
So that the training pipeline can be gated on data quality in Makefile / CI.

**Acceptance Criteria:**

**Given** `python3 scripts/data_readiness_gate.py --symbol BTCUSDT --exchange bybit`
**When** ≥28 calendar days have ≥80,000 rows each
**And** null rate for all ML-critical columns is < 5%
**And** no single date gap > 6 hours
**Then** script prints "READY" and exits 0

**Given** any condition fails
**When** the script runs
**Then** it prints the specific failing condition and exits 1

**And** `make check-data-ready SYMBOL=BTCUSDT` runs the gate via Makefile target
**And** `docs/ml.md` documents the 30-day collection requirement and gate usage

---

## Epic 37: ML Training Pipeline

**Goal**: Build the `ml-service/` Python service with DuckDB-backed feature store, label generation, HDBSCAN clustering per regime, and per-cluster model training with purged walk-forward CV.

### Story 37-1: ml-service Scaffold

As a developer,
I want a runnable ml-service with health endpoint and structured config,
So that subsequent ML stories have a stable service foundation.

**Acceptance Criteria:**

**Given** `docker compose up ml-service`
**When** the container starts
**Then** `GET /health` returns `{"status": "ok"}` with HTTP 200

**Given** environment variables (or .env file)
**When** ml-service starts
**Then** pydantic-settings resolves: `QUESTDB_HTTP_ADDR`, `REDIS_URL`, `ML_FEATURE_STORE_PATH`, `ML_MODEL_REGISTRY_PATH`, `LOG_LEVEL`

**Given** any log output
**When** LOG_LEVEL=info
**Then** structlog emits JSON lines with `timestamp`, `level`, `event` fields

**And** `ml-service/pyproject.toml` declares dependencies: fastapi, uvicorn[standard], pydantic-settings, structlog, duckdb, pandas, pyarrow, scikit-learn, xgboost, hdbscan, httpx
**And** `ml-service/Dockerfile` uses python:3.12-slim, non-root user
**And** `ml-service` service added to `docker-compose.yml` with correct `depends_on: [questdb, redis]`
**And** `ml-service/tests/test_health.py` — L1 test verifying /health returns 200

### Story 37-2: QuestDB → DuckDB → Parquet Feature Store with Rolling Features

As a data engineer,
I want a feature extraction endpoint that reads snapshot_1s from QuestDB, computes rolling statistical features, and saves Parquet partitions,
So that training data captures both instantaneous microstructure and time-series dynamics proven important for mean reversion.

**Acceptance Criteria:**

**Given** `POST /features/extract {"exchange": "bybit", "symbol": "BTCUSDT", "start_date": "2026-05-01", "end_date": "2026-05-31"}`
**When** the endpoint runs
**Then** QuestDB HTTP API queried: `SELECT * FROM snapshot_1s WHERE exchange=? AND symbol=? AND ts >= ? AND ts < ?`
**And** data written to `{ML_FEATURE_STORE_PATH}/exchange=bybit/symbol=BTCUSDT/date=2026-05-01/part.parquet` (one file per day, Hive partitioned)

**Given** raw snapshot_1s data loaded into DataFrame
**When** rolling features are computed
**Then** the following columns are added:
  - `net_buy_fraction` = `buy_volume / volume` (free from existing columns; zero-safe)
  - `depth_imbalance_l1` = `(bid_depth_l1_close - ask_depth_l1_close) / (bid_depth_l1_close + ask_depth_l1_close + 1e-9)`
  - `hurst_20` = rolling Hurst exponent over 20 bars (R/S analysis on mid-price log-returns)
  - `hurst_60` = rolling Hurst exponent over 60 bars
  - `ou_halflife` = rolling OU half-life over 60 bars: fit AR(1) to `microprice_mid_delta`, `halflife = -ln(2) / ln(ϕ)` (capped at [1, 300] bars)
  - `kyle_lambda_60` = rolling 60-bar OLS slope of `Δmid_price ~ ofi_l1` (price impact per unit OFI)
  - `ofi_x_spread` = `ofi_l1 × spread_mean` (interaction feature)
  - `microprice_x_hawkes` = `microprice_mid_delta × hawkes_intensity` (interaction feature)
  - `ofi_sg` = Savitzky-Golay smoothed `ofi_l1` (window=5, polyorder=2)
  - `spread_sg` = Savitzky-Golay smoothed `spread_mean` (window=5, polyorder=2)

**Given** Epic 35 columns not yet in production (null in QuestDB)
**When** the extractor runs
**Then** it derives `microprice_mid_delta` and `cancel_bias` from raw columns client-side as fallback
**And** derived fallback columns are flagged with `_derived=True` in Parquet metadata

**Given** a date range already partially extracted
**When** extraction is called again with overlapping dates
**Then** existing Parquet files for that date are overwritten (idempotent)

**Given** fewer than 20 rows available for a rolling window at the start of the series
**When** rolling features are computed
**Then** rows with insufficient history have null for that rolling feature (no forward-fill across day boundaries)

**And** DuckDB used for writing Parquet with snappy compression
**And** `ml_service/features/extractor.py` handles QuestDB HTTP pagination (daily batch queries)
**And** L1 tests: verify Hurst = 0.5 for random walk input; verify ou_halflife formula; verify SG smoothing reduces variance; verify interaction features computed correctly

### Story 37-3: Label Generator with Hurst Gate and Stat Arb Cointegration

As a data scientist,
I want a label generator that produces forward-reversion labels gated by Hurst exponent, and spread labels with dynamic hedge ratio and ADF validation,
So that the training pipeline only labels time windows where mean reversion is structurally active.

**Acceptance Criteria:**

**Given** a Parquet partition with `microprice_mid_delta`, `hurst_60` columns
**When** `POST /labels/generate {"symbol": "BTCUSDT", "horizons": [3, 10], "threshold_bps": 1.0}`
**Then** for each horizon N in [3, 10], for each row t:
  - Only generate a non-FLAT label when `hurst_60[t] < 0.5` (mean-reverting window confirmed)
  - `label_revert_N` = REVERT if `|microprice_mid_delta[t+N]| < |microprice_mid_delta[t]| × 0.5`
  - `label_revert_N` = TREND  if `|microprice_mid_delta[t+N]| > |microprice_mid_delta[t]| × 1.2`
  - `label_revert_N` = FLAT   otherwise (including all rows where hurst_60 >= 0.5)
**And** `label_revert_3` and `label_revert_10` columns appended to Parquet

**Given** BTC and ETH Parquet files for stat arb labeling
**When** `POST /labels/generate {"spread_pair": ["bybit:BTCUSDT", "bybit:ETHUSDT"], "hedge_method": "kalman"}`
**Then** Kalman filter run on `log(btc_mid) / log(eth_mid)` to estimate time-varying hedge ratio β_t
**And** spread = `log(btc_mid) - β_t × log(eth_mid)`
**And** rolling 60-bar ADF test computed on spread; rows where `adf_pvalue > 0.1` get label_spread = NEUTRAL (spread not cointegrated — don't trade)
**And** for rows where `adf_pvalue <= 0.1`: rolling 60-bar zscore computed
**And** `label_spread` = CONVERGE if `|zscore| > 1.5` and spread reverts within N bars; DIVERGE if extends; NEUTRAL otherwise

**Given** Kalman filter initialised at start of date range
**When** processing begins
**Then** filter warms up over first 60 bars; labels for warm-up period are null

**Given** fewer than N+1 rows at series tail
**When** labels are computed
**Then** tail rows have null labels (no lookahead)

**And** `adf_pvalue_60` and `hedge_ratio_kalman` columns stored in Parquet for use as inference features
**And** L1 tests: Hurst > 0.5 → FLAT label regardless of deviation; ADF p > 0.1 → NEUTRAL spread; Kalman hedge ratio converges on synthetic data

### Story 37-4: Regime Split + HDBSCAN Clustering per Regime

As a data scientist,
I want the training pipeline to cluster bars per regime independently,
So that models are fit on structurally homogeneous market conditions.

**Acceptance Criteria:**

**Given** Parquet feature files with a `regime` column (populated from `regime:{exchange}:{symbol}` Redis history or fallback RANGING)
**When** `POST /cluster {"exchange": "bybit", "symbol": "BTCUSDT", "start_date": "...", "end_date": "..."}`
**Then** data is split by `regime` before clustering (5 separate subsets)
**And** for each regime subset: StandardScaler → PCA (n_components=10 default) → HDBSCAN (min_cluster_size=50 default)
**And** cluster assignments (-1 = noise) written back to the Parquet files as `cluster` column

**Given** HDBSCAN random_state fixed (42)
**When** the same data is clustered twice
**Then** cluster assignments are identical (deterministic)

**Given** a regime subset with fewer than 50 rows
**When** HDBSCAN is run
**Then** all rows assigned cluster=-1 (noise), logged as warning, no crash

**And** PCA loadings saved to `{ML_MODEL_REGISTRY_PATH}/{exchange}/{symbol}/{regime}/pca.pkl`
**And** L1 test: small synthetic dataframe, verify cluster column added and noise flag works

### Story 37-5: Per-Cluster Model Training with Purged Walk-Forward CV and SHAP

As a data scientist,
I want XGBoost, RF, and LR trained per (regime, cluster) with purged CV, class balancing, and SHAP explainability,
So that each model is evaluated honestly and its predictions are interpretable.

**Acceptance Criteria:**

**Given** Parquet data with `regime`, `cluster`, `label_revert_3` or `label_revert_10` columns
**When** `POST /train {"exchange": "bybit", "symbol": "BTCUSDT", "target": "label_revert_10"}`
**Then** for each unique (regime, cluster) group where cluster != -1:
  - Features: all numeric columns except label*, ts, regime, cluster columns
  - Preprocessing pipeline: StandardScaler (fit on train only)
  - Class weights: REVERT=2.0, TREND=2.0, FLAT=1.0 (combat majority-class FLAT predictions)
  - 3 models trained:
    - `XGBClassifier(objective="multi:softprob", num_class=3, n_estimators=500, max_depth=6, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, reg_lambda=1.0, early_stopping_rounds=50, eval_metric="mlogloss")`
    - `RandomForestClassifier(n_estimators=200, max_depth=8, class_weight="balanced")`
    - `LogisticRegression(max_iter=1000, class_weight="balanced", multi_class="multinomial")`
  - Purged walk-forward CV (5 splits, purge_gap=5 bars between train/test)
  - Best model selected by mean F1-macro (weighted) across folds

**Given** XGBoost selected as best model for a (regime, cluster)
**When** final model trained on full dataset
**Then** `shap.TreeExplainer(model).shap_values(X_train[:500])` computed
**And** mean absolute SHAP per feature stored as `shap_summary: {feature: mean_abs_shap}` in model artifact

**Given** purged walk-forward CV
**When** folds are constructed
**Then** no test row has a training row within `purge_gap` bars of its timestamp (no leakage)

**Given** a (regime, cluster) group with fewer than 200 rows
**When** training is called
**Then** that group is skipped with WARNING log `insufficient_training_data`

**And** training metrics stored: cv_score, n_samples, feature_names, shap_summary, best_model_type
**And** L1 tests: purge gap respected (no adjacent ts across fold boundary); class weights applied to XGBClassifier sample_weight; SHAP summary has same keys as feature_names

### Story 37-6: Model Registry

As a developer,
I want a model registry that persists artifacts and exposes them via API,
So that the inference engine can load the right model by (symbol, regime, cluster).

**Acceptance Criteria:**

**Given** `POST /train` completes for a (symbol, regime, cluster)
**When** artifacts are saved
**Then** files written: `{ML_MODEL_REGISTRY_PATH}/{exchange}/{symbol}/{regime}/{cluster}/scaler.pkl`, `pca.pkl`, `model.pkl`
**And** `models.json` updated with entry: `{id, exchange, symbol, regime, cluster, model_type, cv_score, created_at, artifact_dir}`

**Given** `GET /registry`
**When** called
**Then** returns list of all model entries from models.json

**Given** `DELETE /registry/{id}`
**When** called
**Then** removes entry from models.json and deletes artifact directory

**Given** process receives SIGHUP
**When** signal handler fires
**Then** models.json reloaded from disk without restart; newly trained models available to inference without downtime

**And** concurrent reads of models.json are safe (lock-free read, single-writer on train/reload)
**And** L1 test: write 2 entries, GET returns both, DELETE removes one, SIGHUP reloads updated json

---

## Epic 38: ML Inference Pipeline

**Goal**: A real-time inference loop that polls `snapshot_1s` every second, reads current regime from Redis, selects the right model, and publishes signals to `ai:{symbol}:signals` Redis streams.

### Story 38-1: Inference Engine — Core Prediction Logic

As a developer,
I want a pure prediction function that takes a feature row and returns signal + confidence,
So that the inference loop can be tested independently of IO.

**Acceptance Criteria:**

**Given** a loaded model entry (scaler + PCA + classifier) and a feature dict
**When** `InferenceEngine.predict(row: dict) -> Signal` is called
**Then** applies scaler.transform → pca.transform → model.predict_proba
**And** returns Signal(label=REVERT/TREND/FLAT, confidence=max_proba, model_id=entry.id)

**Given** model for (regime, cluster) not found in registry
**When** predict() is called
**Then** returns Signal(label=FLAT, confidence=0.0, model_id=None) (safe default, no crash)

**Given** feature row has missing columns (None / NaN)
**When** predict() is called
**Then** missing values imputed with feature mean from training set (stored in scaler metadata)

**And** `ml_service/inference/engine.py` has zero asyncio / no IO — pure CPU path
**And** L1 test: mock scaler/PCA/clf, verify predict output shape, verify missing-column fallback

### Story 38-2: Live Signal Loop + Redis Publisher

As a developer,
I want an asyncio loop that polls QuestDB every second and publishes signals to Redis,
So that the bot service can consume ML signals in real time.

**Acceptance Criteria:**

**Given** `POST /inference/start {"symbols": [{"exchange": "bybit", "symbol": "BTCUSDT"}]}`
**When** loop starts
**Then** every ~1s: query QuestDB for latest snapshot_1s row → read Redis `regime:bybit:BTCUSDT` → call engine.predict() → publish to `ai:BTCUSDT:signals`

**Given** a prediction ready to publish
**When** XADD is called
**Then** stream entry fields: `ts`, `exchange`, `symbol`, `regime`, `cluster`, `signal`, `confidence`, `model_id`, `deviation_bps`, `latency_ms`
**And** stream maxlen=10000 (approximate trim)

**Given** QuestDB poll returns no new row (same ts as last publish)
**When** loop iteration runs
**Then** no duplicate publish (idempotent — skip if ts unchanged)

**Given** `POST /inference/stop`
**When** called
**Then** loop cancels gracefully within 2 seconds

**Given** Redis unavailable for 3 consecutive publishes
**When** loop runs
**Then** error logged, loop continues (no crash)

**And** `POST /inference/status` returns running symbols and last-published ts per symbol
**And** L1 test: mock QuestDB + Redis, verify correct stream key and field names

### Story 38-3: Stat Arb Spread Inference (BTC/ETH)

As a developer,
I want the inference loop to compute a BTC/ETH spread z-score and publish convergence signals,
So that the stat arb strategy can consume ML-qualified spread signals.

**Acceptance Criteria:**

**Given** latest BTCUSDT and ETHUSDT snapshot_1s rows fetched within 1 second of each other
**When** spread inference runs
**Then** `spread = log(btc_mid) - log(eth_mid)`
**And** rolling 60-bar mean and std tracked in-memory (no QuestDB history needed at inference)

**Given** `|z_score| > 1.5`
**When** spread model loaded from registry (trained in 37-5 with label_spread target)
**Then** predict CONVERGE / DIVERGE / NEUTRAL with confidence
**And** publish to `ai:BTC-ETH-spread:signals` with fields: `ts`, `spread`, `zscore`, `signal`, `confidence`, `model_id`

**Given** BTC or ETH latest row is more than 5 seconds stale
**When** spread inference runs
**Then** skip publish, log WARNING `spread_inference_stale_data`

**Given** `|z_score| <= 1.5`
**When** spread inference runs
**Then** publish NEUTRAL with confidence=1.0 (no model call needed for neutral zone)

**And** spread inference included in `/inference/start` symbols list when `spread_pair` specified
**And** L1 test: mock both symbol rows, verify z_score computation, verify stale-data skip

---

## Epic 39: Dashboard ML Page

**Goal**: Add a `/ml` page to the Dash dashboard with 4 tabs — Data, Clusters, Models, Live — giving full visibility into regime state, cluster structure, model registry, and live signals.

### Story 39-1: /ml Page Routing and 4-Tab Scaffold

As a dashboard user,
I want to navigate to /ml and see a 4-tab page,
So that all ML views are accessible from the existing nav.

**Acceptance Criteria:**

**Given** user navigates to `/ml` in the browser
**When** `render_page` callback fires
**Then** `layout_ml.ml_page_layout` is returned

**Given** the /ml page renders
**When** the page loads
**Then** 4 dcc.Tabs are visible: "Data", "Clusters", "Models", "Live"
**And** each tab shows placeholder content (at minimum a heading) — full content in subsequent stories

**And** NavLink "ML" added to `dashboard/layout.py` nav bar alongside Charts/Bots/Backtests
**And** `dashboard/layout_ml.py` created with `ml_page_layout` component
**And** `dashboard/callbacks.py` `render_page` handles `/ml` pathname
**And** `dashboard/app.py` imports `layout_ml` so component IDs register at startup

### Story 39-2: Data Tab — Regime Timeline and Microprice Deviation

As a dashboard user,
I want to see current regime classification and microprice deviation over time,
So that I can assess data quality and the mean-reversion signal landscape.

**Acceptance Criteria:**

**Given** a symbol is selected (symbol dropdown added to /ml page)
**When** the Data tab is active
**Then** a regime timeline bar chart shows coloured bands: TRENDING_UP=green, TRENDING_DOWN=red, HIGH_VOL=orange, THIN_BOOK=yellow, RANGING=grey

**Given** snapshot_1s data fetched from QuestDB for the last 24 hours
**When** the microprice deviation chart renders
**Then** a line chart shows `microprice_mid_delta` (bps) vs time with a zero-line reference
**And** RANGING regime periods highlighted with light grey background bands

**Given** the tab is active
**When** the 30-second interval fires
**Then** both charts refresh with latest data

**And** all charts use the existing `_DARK` theme from `charts.py`
**And** data fetched via QuestDB HTTP using `QUESTDB_HTTP_ADDR` env var

### Story 39-3: Clusters Tab — PCA Scatter and Feature Importances

As a data scientist,
I want to visualise cluster structure and feature importances from the model registry,
So that I can evaluate cluster quality and understand what drives each signal.

**Acceptance Criteria:**

**Given** the Clusters tab is active
**When** the page loads
**Then** a dropdown lets the user select (symbol, regime) combination

**Given** a (symbol, regime) selected with trained models
**When** charts render
**Then** a 2D scatter plot shows PCA projection of training data, coloured by cluster label
**And** a horizontal bar chart shows top-20 feature importances (XGBoost gain if available, else RF)

**Given** no trained models exist for the selected (symbol, regime)
**When** charts render
**Then** message "No models trained for this selection" shown (no crash)

**And** data fetched from ml-service `GET /registry` with `ML_SERVICE_URL` env var (default http://ml-service:8000)
**And** SHAP summary loaded from `shap_summary` field in registry entry (no additional endpoint needed — already in models.json)

### Story 39-4: Models Tab — Registry Table and CV Scores

As a developer,
I want a table of all trained models with their metrics,
So that I can track model versions and select the best per (symbol, regime, cluster).

**Acceptance Criteria:**

**Given** the Models tab is active
**When** the page loads
**Then** a DataTable shows columns: Symbol, Exchange, Regime, Cluster, Model Type, CV Score (F1), Created At

**Given** `GET /registry` returns model entries
**When** the table renders
**Then** rows sorted by Created At descending
**And** CV Score formatted to 3 decimal places

**Given** the 60-second interval fires
**When** table refreshes
**Then** new models trained since last refresh appear

**And** row click triggers a detail panel showing: n_samples, feature_names list, artifact_dir path

### Story 39-5: Live Tab — Signal Feed and Deviation Gauge

As a trader,
I want to see real-time ML signals and microprice deviation in the dashboard,
So that I can monitor what the ML model is currently predicting.

**Acceptance Criteria:**

**Given** the Live tab is active and inference loop is running
**When** the 1-second interval fires
**Then** a table shows the last 20 entries from `ai:{symbol}:signals` with columns: Time, Signal, Confidence, Regime, Cluster, Deviation bps, Model ID

**Given** signal = REVERT
**When** the row renders
**Then** row background is green (rgba(0,200,0,0.15))

**Given** signal = TREND
**When** the row renders
**Then** row background is red (rgba(200,0,0,0.15))

**Given** `ai:BTC-ETH-spread:signals` stream has entries
**When** the Live tab is active
**Then** a secondary line chart shows the last 60 spread z-score values with ±1.5σ reference lines

**Given** inference loop not running (no recent signals in stream)
**When** Live tab renders
**Then** message "Inference loop not running — start with POST /inference/start" shown

**And** signal data fetched from Redis via QuestDB proxy or direct Redis read using `REDIS_URL`
**And** deviation gauge (plotly indicator) shows current microprice_mid_delta bps with range ±5 bps

---

## Template Reference

```
### Story {N}.{M}: {story_title}

As a {user_type},
I want {capability},
So that {value_benefit}.

**Acceptance Criteria:**

**Given** {precondition}
**When** {action}
**Then** {expected_outcome}
**And** {additional_criteria}
```
