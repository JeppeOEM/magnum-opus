---
id: 37-3
title: Label generator with Hurst gate and Kalman hedge ratio
epic: 37
status: ready-for-dev
---

# Story 37-3: Label Generator with Hurst Gate and Kalman Hedge Ratio

## Context

Two label types:
1. **Per-symbol reversion labels** at N=3 and N=10 horizons, gated by Hurst exponent — only REVERT/TREND labels emitted when H < 0.5 confirms mean-reverting conditions.
2. **Stat arb spread labels** using a Kalman-filter hedge ratio (dynamic, not static log ratio) and rolling ADF p-value to gate the cointegration relationship.

Both N=3 and N=10 labels are generated in one pass so the trainer can select the horizon with best CV score.

## What to build

### `ml-service/ml_service/labels/generator.py`

```python
from __future__ import annotations
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import linregress
import structlog

log = structlog.get_logger()


def _revert_label(delta: float, fwd_delta: float) -> str:
    if abs(delta) < 1e-10:
        return "FLAT"
    ratio = abs(fwd_delta) / abs(delta)
    if ratio < 0.5:
        return "REVERT"
    if ratio > 1.2:
        return "TREND"
    return "FLAT"


def generate_reversion_labels(
    df: pd.DataFrame,
    horizons: list[int],
    threshold_bps: float = 1.0,
) -> pd.DataFrame:
    """Add label_revert_{N} columns gated by hurst_60 < 0.5."""
    mmd = df["microprice_mid_delta"].fillna(0).values
    hurst = df.get("hurst_60", pd.Series(0.5, index=df.index)).fillna(0.5).values

    for N in horizons:
        labels = np.full(len(df), "FLAT", dtype=object)
        for i in range(len(df) - N):
            if abs(mmd[i]) < threshold_bps:
                continue  # deviation too small — FLAT regardless
            if hurst[i] >= 0.5:
                continue  # not mean-reverting window — stay FLAT
            labels[i] = _revert_label(mmd[i], mmd[i + N])
        df[f"label_revert_{N}"] = labels
    return df


# ── Kalman filter for dynamic hedge ratio ─────────────────────────────────────

def _kalman_hedge(log_btc: np.ndarray, log_eth: np.ndarray) -> np.ndarray:
    """
    1D Kalman filter estimating β_t in: log_btc = β_t × log_eth + ε
    Returns array of hedge ratios, same length as inputs.
    """
    n = len(log_btc)
    beta = np.zeros(n)
    P = 1.0      # state variance
    Q = 1e-5     # process noise
    R = 1e-3     # observation noise

    beta[0] = log_btc[0] / log_eth[0] if log_eth[0] != 0 else 1.0

    for t in range(1, n):
        # Predict
        P_pred = P + Q
        # Update
        H = log_eth[t]
        S = H * P_pred * H + R
        K = P_pred * H / S if S != 0 else 0.0
        y_hat = beta[t-1] * H
        beta[t] = beta[t-1] + K * (log_btc[t] - y_hat)
        P = (1 - K * H) * P_pred

    return beta


def _rolling_adf_pvalue(spread: np.ndarray, window: int = 60) -> np.ndarray:
    """Rolling ADF p-value. Uses OLS regression for speed (approximate ADF)."""
    from statsmodels.tsa.stattools import adfuller
    pvals = np.full(len(spread), 1.0)
    for i in range(window, len(spread)):
        chunk = spread[i-window:i]
        try:
            result = adfuller(chunk, maxlag=1, regression="c", autolag=None)
            pvals[i] = result[1]
        except Exception:
            pvals[i] = 1.0
    return pvals


def generate_spread_labels(
    df_btc: pd.DataFrame,
    df_eth: pd.DataFrame,
    horizon: int = 10,
    zscore_threshold: float = 1.5,
    adf_threshold: float = 0.1,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Join BTC and ETH DataFrames on ts (1s tolerance), compute Kalman hedge ratio,
    rolling ADF, and CONVERGE/DIVERGE/NEUTRAL labels. Returns both DataFrames
    with spread columns and labels added.
    """
    # Merge on nearest ts
    df_btc = df_btc.sort_values("ts").reset_index(drop=True)
    df_eth = df_eth.sort_values("ts").reset_index(drop=True)
    merged = pd.merge_asof(df_btc, df_eth, on="ts", suffixes=("_btc", "_eth"), tolerance=pd.Timedelta("1s"))
    merged = merged.dropna(subset=["best_bid_btc", "best_bid_eth"])

    mid_btc = (merged["best_bid_btc"] + merged["best_ask_btc"]) / 2
    mid_eth = (merged["best_bid_eth"] + merged["best_ask_eth"]) / 2
    log_btc = np.log(mid_btc.values)
    log_eth = np.log(mid_eth.values)

    beta = _kalman_hedge(log_btc, log_eth)
    spread = log_btc - beta * log_eth

    merged["hedge_ratio_kalman"] = beta
    merged["spread"] = spread

    # Rolling z-score (60 bars)
    s = pd.Series(spread)
    roll_mean = s.rolling(60).mean()
    roll_std  = s.rolling(60).std()
    merged["spread_zscore"] = (s - roll_mean) / roll_std.replace(0, np.nan)

    # Rolling ADF p-value
    merged["adf_pvalue_60"] = _rolling_adf_pvalue(spread, window=60)

    # Labels
    labels = np.full(len(merged), "NEUTRAL", dtype=object)
    zscore = merged["spread_zscore"].values
    adf    = merged["adf_pvalue_60"].values

    for i in range(len(merged) - horizon):
        if adf[i] > adf_threshold:
            continue  # spread not cointegrated — NEUTRAL
        if abs(zscore[i]) <= zscore_threshold:
            continue  # not extended enough — NEUTRAL
        # Check if spread reverted
        fwd_zscore = zscore[i + horizon]
        if abs(fwd_zscore) < abs(zscore[i]) * 0.5:
            labels[i] = "CONVERGE"
        elif abs(fwd_zscore) > abs(zscore[i]) * 1.2:
            labels[i] = "DIVERGE"

    merged["label_spread"] = labels

    # Write spread columns back to original DataFrames (aligned by ts)
    # Return merged for storage
    return merged


def add_labels_to_parquet(
    feature_store_path: str,
    exchange: str,
    symbol: str,
    start_date: str,
    end_date: str,
    horizons: list[int],
    threshold_bps: float,
) -> dict:
    stats = {"files_updated": 0}
    base = Path(feature_store_path) / f"exchange={exchange}" / f"symbol={symbol}"
    for parquet_file in sorted(base.glob("date=*/part.parquet")):
        date_str = parquet_file.parent.name.replace("date=", "")
        if not (start_date <= date_str <= end_date):
            continue
        df = duckdb.execute(f"SELECT * FROM '{parquet_file}'").df()
        df["ts"] = pd.to_datetime(df["ts"])
        df = generate_reversion_labels(df, horizons, threshold_bps)
        duckdb.execute(f"COPY (SELECT * FROM df) TO '{parquet_file}' (FORMAT PARQUET, COMPRESSION SNAPPY)")
        stats["files_updated"] += 1
    return stats
```

### FastAPI endpoint additions to `main.py`

```python
class LabelRequest(BaseModel):
    exchange: str
    symbol: str
    start_date: str  # "2026-05-01"
    end_date: str
    horizons: list[int] = [3, 10]
    threshold_bps: float = 1.0

@app.post("/labels/generate")
async def labels_generate(req: LabelRequest):
    cfg = get_settings()
    return add_labels_to_parquet(
        cfg.ml_feature_store_path, req.exchange, req.symbol,
        req.start_date, req.end_date, req.horizons, req.threshold_bps,
    )
```

### `ml-service/tests/test_labels.py`

```python
import numpy as np
import pandas as pd
from ml_service.labels.generator import generate_reversion_labels, _kalman_hedge

def test_revert_label_when_deviation_closes():
    df = pd.DataFrame({
        "microprice_mid_delta": [5.0, 1.0] + [0.0]*10,
        "hurst_60": [0.3] * 12,  # mean-reverting
        "ts": pd.date_range("2026-01-01", periods=12, freq="1s"),
    })
    df = generate_reversion_labels(df, horizons=[1])
    assert df["label_revert_1"].iloc[0] == "REVERT"

def test_flat_when_hurst_above_half():
    df = pd.DataFrame({
        "microprice_mid_delta": [5.0, 0.0] + [0.0]*10,
        "hurst_60": [0.6] * 12,  # trending — label suppressed
        "ts": pd.date_range("2026-01-01", periods=12, freq="1s"),
    })
    df = generate_reversion_labels(df, horizons=[1])
    assert df["label_revert_1"].iloc[0] == "FLAT"

def test_trend_label_when_deviation_widens():
    df = pd.DataFrame({
        "microprice_mid_delta": [2.0, 5.0] + [0.0]*10,
        "hurst_60": [0.3] * 12,
        "ts": pd.date_range("2026-01-01", periods=12, freq="1s"),
    })
    df = generate_reversion_labels(df, horizons=[1])
    assert df["label_revert_1"].iloc[0] == "TREND"

def test_kalman_hedge_converges():
    # Synthetic cointegrated pair: btc = 2*eth + noise
    rng = np.random.default_rng(0)
    eth = np.log(np.cumsum(rng.standard_normal(200) * 0.01) + 100)
    btc = 2.0 * eth + rng.standard_normal(200) * 0.001
    beta = _kalman_hedge(btc, eth)
    # After warm-up, beta should converge near 2.0
    assert 1.5 < beta[-1] < 2.5

def test_tail_rows_are_null_or_flat():
    df = pd.DataFrame({
        "microprice_mid_delta": [5.0] * 15,
        "hurst_60": [0.3] * 15,
        "ts": pd.date_range("2026-01-01", periods=15, freq="1s"),
    })
    df = generate_reversion_labels(df, horizons=[10])
    # Last 10 rows should be FLAT (no lookahead)
    assert all(df["label_revert_10"].iloc[-10:] == "FLAT")
```

## Acceptance Criteria

1. `label_revert_3` and `label_revert_10` columns added to Parquet.
2. Only rows with `hurst_60 < 0.5` and `|microprice_mid_delta| > threshold_bps` get non-FLAT labels.
3. REVERT when `|mmd[t+N]| < |mmd[t]| × 0.5`; TREND when `> 1.2`; FLAT otherwise.
4. Last N rows of each horizon have FLAT labels (no lookahead).
5. Kalman hedge ratio β converges within 200 bars for a synthetic cointegrated pair (1.5 < β < 2.5 for true β=2).
6. Spread labels are NEUTRAL when `adf_pvalue > 0.1`.
7. `hedge_ratio_kalman` and `adf_pvalue_60` stored in the merged spread Parquet.
8. All 5 unit tests pass.

## Dev Notes

- `statsmodels` is needed for `adfuller` — add to `pyproject.toml`: `"statsmodels>=0.14"`.
- The Kalman filter uses a simple 1D state (scalar β). Process noise Q=1e-5 controls how quickly β can drift.
- `merge_asof` tolerance=1s: if BTC and ETH bars are more than 1s apart, that row is dropped.
- Both N=3 and N=10 labels generated in one pass — trainer can select best horizon via CV score.
