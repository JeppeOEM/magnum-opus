"""Label generator: per-symbol reversion labels and stat-arb spread labels.

Reversion labels (label_revert_N) are gated by hurst_60 < 0.5 so they fire
only in confirmed mean-reverting windows.

Spread labels (label_spread) use a 1D Kalman-filter hedge ratio plus a rolling
ADF p-value gate so they fire only when the pair is cointegrated.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import structlog

log = structlog.get_logger()


# ── Per-symbol reversion labels ───────────────────────────────────────────────

def _revert_label(delta: float, fwd_delta: float) -> str:
    """Classify one bar: REVERT when spread closes, TREND when it widens, FLAT otherwise."""
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
    """Add label_revert_{N} columns gated by hurst_60 < 0.5.

    Only rows where |microprice_mid_delta| > threshold_bps AND hurst_60 < 0.5
    receive non-FLAT labels. The last N rows of each horizon are always FLAT
    (no lookahead).
    """
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
    """1D Kalman filter estimating β_t in: log_btc = β_t × log_eth + ε.

    Returns array of hedge ratios, same length as inputs.
    Process noise Q=1e-5 controls β drift speed.
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
        y_hat = beta[t - 1] * H
        beta[t] = beta[t - 1] + K * (log_btc[t] - y_hat)
        P = (1 - K * H) * P_pred

    return beta


def _rolling_adf_pvalue(spread: np.ndarray, window: int = 60) -> np.ndarray:
    """Rolling ADF p-value over a fixed window. Returns 1.0 for insufficient data.

    Uses statsmodels adfuller with maxlag=1 for speed. Catches all exceptions
    (e.g. constant spread window) and returns 1.0 (not cointegrated).
    """
    from statsmodels.tsa.stattools import adfuller  # lazy import — heavy dep

    pvals = np.full(len(spread), 1.0)
    for i in range(window, len(spread)):
        chunk = spread[i - window : i]
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
) -> pd.DataFrame:
    """Join BTC and ETH DataFrames on ts (1s tolerance), compute Kalman hedge ratio,
    rolling ADF, and CONVERGE/DIVERGE/NEUTRAL labels.

    Returns the merged DataFrame with spread columns and labels added.
    Columns added: hedge_ratio_kalman, spread, spread_zscore, adf_pvalue_60,
    label_spread.
    """
    df_btc = df_btc.sort_values("ts").reset_index(drop=True)
    df_eth = df_eth.sort_values("ts").reset_index(drop=True)
    merged = pd.merge_asof(
        df_btc,
        df_eth,
        on="ts",
        suffixes=("_btc", "_eth"),
        tolerance=pd.Timedelta("1s"),
    )
    merged = merged.dropna(subset=["best_bid_btc", "best_bid_eth"]).reset_index(drop=True)

    mid_btc = (merged["best_bid_btc"] + merged["best_ask_btc"]) / 2
    mid_eth = (merged["best_bid_eth"] + merged["best_ask_eth"]) / 2
    log_btc = np.log(mid_btc.clip(lower=1e-10).values)
    log_eth = np.log(mid_eth.clip(lower=1e-10).values)

    beta = _kalman_hedge(log_btc, log_eth)
    spread = log_btc - beta * log_eth

    merged["hedge_ratio_kalman"] = beta
    merged["spread"] = spread

    # Rolling z-score (60 bars)
    s = pd.Series(spread)
    roll_mean = s.rolling(60).mean()
    roll_std = s.rolling(60).std()
    merged["spread_zscore"] = (s - roll_mean) / roll_std.replace(0, np.nan)

    # Rolling ADF p-value
    merged["adf_pvalue_60"] = _rolling_adf_pvalue(spread, window=60)

    # Labels
    labels = np.full(len(merged), "NEUTRAL", dtype=object)
    zscore = merged["spread_zscore"].values
    adf = merged["adf_pvalue_60"].values

    for i in range(len(merged) - horizon):
        if adf[i] > adf_threshold:
            continue  # spread not cointegrated — NEUTRAL
        if abs(zscore[i]) <= zscore_threshold:
            continue  # not extended enough — NEUTRAL
        fwd_zscore = zscore[i + horizon]
        if not np.isfinite(fwd_zscore):
            continue
        if abs(fwd_zscore) < abs(zscore[i]) * 0.5:
            labels[i] = "CONVERGE"
        elif abs(fwd_zscore) > abs(zscore[i]) * 1.2:
            labels[i] = "DIVERGE"

    merged["label_spread"] = labels
    return merged


# ── Parquet update helpers ─────────────────────────────────────────────────────

def add_labels_to_parquet(
    feature_store_path: str,
    exchange: str,
    symbol: str,
    start_date: str,
    end_date: str,
    horizons: list[int],
    threshold_bps: float,
) -> dict:
    """Read Hive-partitioned Parquet files, add reversion labels, overwrite in place.

    Only files whose date= partition falls within [start_date, end_date] are
    processed. Idempotent — re-running overwrites with the same labels.
    """
    stats: dict = {"files_updated": 0}
    base = Path(feature_store_path) / f"exchange={exchange}" / f"symbol={symbol}"
    for parquet_file in sorted(base.glob("date=*/part.parquet")):
        date_str = parquet_file.parent.name.replace("date=", "")
        if not (start_date <= date_str <= end_date):
            continue
        df = duckdb.execute(f"SELECT * FROM read_parquet('{parquet_file}')").df()
        df["ts"] = pd.to_datetime(df["ts"])
        df = generate_reversion_labels(df, horizons, threshold_bps)
        duckdb.execute(
            f"COPY (SELECT * FROM df) TO '{parquet_file}' (FORMAT PARQUET, COMPRESSION SNAPPY)"
        )
        stats["files_updated"] += 1
        log.info("labels_added", file=str(parquet_file), horizons=horizons)
    return stats
