# ML Service

## Overview

The ML service (Epic 37–39) trains per-regime, per-cluster XGBoost classifiers on 1-second microstructure data from `snapshot_1s` and publishes real-time trading signals via Redis streams.

## Data Collection Requirement

The ML training pipeline requires **≥28 days** of quality `snapshot_1s` data from the candle service.

**Wait time**: ~30 days after deploying Epic 35 (candle-service microstructure intelligence).

## Checking Readiness

Before starting ML training, run the readiness gate:

```bash
make check-data-ready SYMBOL=BTCUSDT EXCHANGE=bybit
```

Exits 0 when all conditions are met:
- ≥28 days with ≥80,000 rows each (in last 35 days)
- <5% null rate for all ML-critical columns (last 7 days)
- No gap > 6 hours in the last 29 days

Exits 1 with `FAIL: <reason>` lines for each failing condition.

## Monitoring Collection Progress

Run anytime to see current data health:

```bash
python3 scripts/check_data_quality.py --symbol BTCUSDT --exchange bybit
```

Output:
- Total row count
- Per-day row counts (last 35 days) with `⚠` when < 80,000 rows
- Null rates for all 11 ML-critical columns with `⚠` when > 5%
- Date gaps > 1 hour (last 7 days)

## ML-Critical Columns (Epic 35 additions)

| Column | Source | Description |
|---|---|---|
| `hawkes_intensity` | 35-1 | Self-exciting order arrival intensity (O(1) decay) |
| `microprice_mid_delta` | 35-2 | Depth-weighted fair value deviation from mid (bps) |
| `cancel_bias` | 35-3 | Directional cancel imbalance (-1 to +1) |
| `trade_aggressiveness` | 35-3 | Volume vs bid depth ratio |
| `buy_vwap_deviation_bps` | 35-6 | Buy VWAP deviation from mid (bps) — **#3 SHAP feature** |
| `sell_vwap_deviation_bps` | 35-6 | Sell VWAP deviation from mid (bps) |
| `bid_depth_l3_close` | 35-4 | Cumulative bid depth through 3rd level (book shape) |
| `realized_vol` | existing | Intra-bar mid-price return volatility |
| `ofi` | existing | Order flow imbalance |
| `ofi_l1` | existing | OFI L1 (best bid/ask only) |
| `spread_mean` | existing | Mean bid-ask spread this bar |

## Regime Classification

The `regime:{exchange}:{symbol}` Redis key (TTL 180s) is written by the candle service every 60 seconds. Five regimes:

| Regime | Condition | ML behaviour |
|---|---|---|
| `RANGING` | Default | Mean reversion applies — models trained and used |
| `THIN_BOOK` | Spread > 2× median | Mean reversion applies — models used |
| `HIGH_VOL` | `realized_vol > 0.002` | Mean reversion applies — models used |
| `TRENDING_UP` | 5-bar price rise > 0.1% | **TRENDING — skipped in training and inference** |
| `TRENDING_DOWN` | 5-bar price fall > 0.1% | **TRENDING — skipped in training and inference** |

## Pipeline Overview

```
snapshot_1s (QuestDB)
    ↓
Feature Store (Parquet, Hive-partitioned)
    ↓  [Epic 37-2: assembler + rolling features]
Labels (REVERT / FLAT / TREND)
    ↓  [Epic 37-3: Hurst gate, Kalman hedge ratio]
HDBSCAN clusters per regime
    ↓  [Epic 37-4]
XGBoost per (regime, cluster)
    ↓  [Epic 37-5: purged walk-forward CV + SHAP]
Model registry (models.json)
    ↓  [Epic 38-1: inference engine]
ai:{symbol}:signals  (Redis stream, 1s)
ai:BTC-ETH-spread:signals  (Redis stream, stat arb)
```
