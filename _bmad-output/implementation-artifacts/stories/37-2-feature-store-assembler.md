---
id: 37-2
title: Feature store assembler with rolling statistical features
epic: 37
status: ready-for-dev
---

# Story 37-2: Feature Store Assembler with Rolling Statistical Features

## Context

The feature store pulls `snapshot_1s` from QuestDB and enriches it with Python-side rolling features that capture time-series dynamics crucial for mean reversion: Hurst exponent (confirms mean-reversion regime), OU half-life (how fast reversion happens), Kyle's lambda (informed trading intensity), and interaction features. Savitzky-Golay smoothing on OFI and spread reduces noise before these derivatives are computed (5-17% accuracy gain per paper).

Output: Hive-partitioned Parquet at `{ML_FEATURE_STORE_PATH}/exchange={e}/symbol={s}/date={d}/part.parquet`.

## What to build

### `ml-service/ml_service/features/extractor.py`

```python
from __future__ import annotations
import os
from datetime import date, timedelta
from pathlib import Path

import duckdb
import httpx
import numpy as np
import pandas as pd
from scipy.signal import savgol_filter
from scipy.stats import linregress
import structlog

log = structlog.get_logger()

QUESTDB_PAGE = 50_000  # rows per HTTP request

def _fetch_day(questdb_url: str, exchange: str, symbol: str, day: date) -> pd.DataFrame:
    """Fetch one day of snapshot_1s from QuestDB HTTP API."""
    start = f"{day}T00:00:00Z"
    end   = f"{day + timedelta(days=1)}T00:00:00Z"
    sql = (
        f"SELECT * FROM snapshot_1s "
        f"WHERE exchange='{exchange}' AND symbol='{symbol}' "
        f"AND ts >= '{start}' AND ts < '{end}' "
        f"ORDER BY ts"
    )
    rows, offset = [], 0
    while True:
        resp = httpx.get(
            questdb_url + "/exec",
            params={"query": sql, "limit": f"{offset},{QUESTDB_PAGE}"},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        cols = [c["name"] for c in data["columns"]]
        batch = data["dataset"]
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < QUESTDB_PAGE:
            break
        offset += QUESTDB_PAGE
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=cols)
    df["ts"] = pd.to_datetime(df["ts"])
    return df

def _hurst(series: np.ndarray) -> float:
    """Hurst exponent via R/S analysis. Returns 0.5 on insufficient data."""
    n = len(series)
    if n < 20:
        return 0.5
    lags, rs_vals = [], []
    for lag in [4, 8, 16, min(32, n // 2)]:
        if lag >= n:
            continue
        chunks = [series[i:i+lag] for i in range(0, n - lag, lag)]
        rs_list = []
        for c in chunks:
            mean, std = c.mean(), c.std()
            if std < 1e-10:
                continue
            cumdev = np.cumsum(c - mean)
            rs_list.append((cumdev.max() - cumdev.min()) / std)
        if rs_list:
            lags.append(np.log(lag))
            rs_vals.append(np.log(np.mean(rs_list)))
    if len(lags) < 2:
        return 0.5
    slope, *_ = linregress(lags, rs_vals)
    return float(np.clip(slope, 0.0, 1.0))

def _ou_halflife(series: np.ndarray) -> float:
    """OU half-life from AR(1) fit. Returns 60.0 (1-minute) as safe default."""
    if len(series) < 10:
        return 60.0
    y, x = series[1:], series[:-1]
    slope, *_ = linregress(x, y)
    if slope <= 0 or slope >= 1:
        return 60.0
    hl = -np.log(2) / np.log(slope)
    return float(np.clip(hl, 1.0, 300.0))

def _kyle_lambda(delta_mid: np.ndarray, ofi: np.ndarray) -> float:
    """Kyle's lambda: slope of Δmid ~ λ × OFI regression."""
    mask = np.isfinite(delta_mid) & np.isfinite(ofi)
    if mask.sum() < 10:
        return 0.0
    slope, *_ = linregress(ofi[mask], delta_mid[mask])
    return float(slope)

def _add_rolling_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add all rolling statistical and interaction features."""
    mid = (df["best_bid"].fillna(method="ffill") + df["best_ask"].fillna(method="ffill")) / 2

    # Savitzky-Golay smoothing (window=5, polyorder=2) — reduces noise
    if len(df) >= 5:
        df["ofi_sg"]    = savgol_filter(df["ofi_l1"].fillna(0).values, 5, 2)
        df["spread_sg"] = savgol_filter(df["spread_mean"].fillna(0).values, 5, 2)
    else:
        df["ofi_sg"]    = df["ofi_l1"]
        df["spread_sg"] = df["spread_mean"]

    # Net buy fraction — free from existing columns
    vol = df["volume"].fillna(0)
    bvol = df["buy_volume"].fillna(0)
    df["net_buy_fraction"] = np.where(vol > 0, bvol / vol, 0.5)

    # Depth imbalance L1
    bl1 = df["bid_depth_l1_close"].fillna(0)
    al1 = df["ask_depth_l1_close"].fillna(0)
    df["depth_imbalance_l1"] = (bl1 - al1) / (bl1 + al1 + 1e-9)

    # Rolling Hurst (20 and 60 bars)
    log_ret = np.log(mid.replace(0, np.nan)).diff().fillna(0).values
    df["hurst_20"] = pd.Series(log_ret).rolling(20).apply(
        lambda x: _hurst(x.values), raw=False
    ).values
    df["hurst_60"] = pd.Series(log_ret).rolling(60).apply(
        lambda x: _hurst(x.values), raw=False
    ).values

    # Rolling OU half-life on microprice_mid_delta (60 bars)
    mmd = df.get("microprice_mid_delta", pd.Series(0.0, index=df.index)).fillna(0).values
    df["ou_halflife"] = pd.Series(mmd).rolling(60).apply(
        lambda x: _ou_halflife(x.values), raw=False
    ).values

    # Rolling Kyle's lambda (60 bars): Δmid vs ofi_l1
    delta_mid = np.diff(mid.values, prepend=mid.values[0])
    ofi = df["ofi_sg"].values
    df["kyle_lambda_60"] = pd.Series(list(zip(delta_mid, ofi))).rolling(60).apply(
        lambda x: _kyle_lambda(
            np.array([v[0] for v in x]),
            np.array([v[1] for v in x]),
        ), raw=False
    ).values

    # Interaction features
    df["ofi_x_spread"]       = df["ofi_sg"] * df["spread_sg"]
    df["microprice_x_hawkes"] = (
        df.get("microprice_mid_delta", 0).fillna(0) *
        df.get("hawkes_intensity", 0).fillna(0)
    )

    # Fallback derivation when Epic 35 columns missing
    if "microprice_mid_delta" not in df.columns or df["microprice_mid_delta"].isna().all():
        # Derive from raw columns
        mp = (df["best_ask"] * bl1 + df["best_bid"] * al1) / (bl1 + al1 + 1e-9)
        df["microprice_mid_delta"] = np.where(mid > 0, (mp - mid) / mid * 10000, 0)
        df.attrs["microprice_derived"] = True

    if "cancel_bias" not in df.columns or df["cancel_bias"].isna().all():
        bc = df.get("bid_cancel_count", 0).fillna(0)
        ac = df.get("ask_cancel_count", 0).fillna(0)
        df["cancel_bias"] = (bc - ac) / (bc + ac + 1.0)
        df.attrs["cancel_bias_derived"] = True

    return df

def extract(
    questdb_url: str,
    feature_store_path: str,
    exchange: str,
    symbol: str,
    start_date: date,
    end_date: date,
) -> dict:
    stats = {"days_written": 0, "rows_total": 0}
    current = start_date
    while current <= end_date:
        df = _fetch_day(questdb_url, exchange, symbol, current)
        if df.empty:
            log.warning("no_data_for_day", date=str(current))
            current += timedelta(days=1)
            continue
        df = _add_rolling_features(df)
        out_path = (
            Path(feature_store_path)
            / f"exchange={exchange}" / f"symbol={symbol}" / f"date={current}"
        )
        out_path.mkdir(parents=True, exist_ok=True)
        parquet_file = out_path / "part.parquet"
        duckdb.execute(f"COPY (SELECT * FROM df) TO '{parquet_file}' (FORMAT PARQUET, COMPRESSION SNAPPY)")
        stats["days_written"] += 1
        stats["rows_total"] += len(df)
        log.info("day_extracted", date=str(current), rows=len(df))
        current += timedelta(days=1)
    return stats
```

### `ml-service/ml_service/main.py` — add endpoint

```python
from datetime import date
from pydantic import BaseModel
from ml_service.features.extractor import extract as _extract_features
from ml_service.config import get_settings

class ExtractRequest(BaseModel):
    exchange: str
    symbol: str
    start_date: date
    end_date: date

@app.post("/features/extract")
async def features_extract(req: ExtractRequest):
    cfg = get_settings()
    stats = _extract_features(
        cfg.questdb_http_addr, cfg.ml_feature_store_path,
        req.exchange, req.symbol, req.start_date, req.end_date,
    )
    return stats
```

### `ml-service/tests/test_extractor.py`

```python
import numpy as np
from ml_service.features.extractor import _hurst, _ou_halflife, _kyle_lambda, _add_rolling_features
import pandas as pd

def test_hurst_random_walk_near_half():
    rng = np.random.default_rng(42)
    rw = np.cumsum(rng.standard_normal(200))
    h = _hurst(np.diff(rw))
    assert 0.3 < h < 0.7  # random walk ≈ 0.5

def test_hurst_mean_reverting_below_half():
    # AR(1) with negative autocorrelation → H < 0.5
    x = np.zeros(200)
    for i in range(1, 200):
        x[i] = -0.7 * x[i-1] + np.random.randn() * 0.1
    h = _hurst(x)
    assert h < 0.5

def test_ou_halflife_formula():
    # AR(1) ϕ=0.9 → halflife = -ln(2)/ln(0.9) ≈ 6.58
    x = np.zeros(200)
    for i in range(1, 200):
        x[i] = 0.9 * x[i-1] + np.random.randn() * 0.01
    hl = _ou_halflife(x)
    assert 4.0 < hl < 12.0

def test_add_rolling_features_columns_present():
    # minimal DataFrame with required columns
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=100, freq="1s"),
        "best_bid": 100.0, "best_ask": 100.1,
        "ofi_l1": np.random.randn(100) * 10,
        "spread_mean": 0.1, "volume": 1.0,
        "buy_volume": 0.5, "bid_depth_l1_close": 10.0,
        "ask_depth_l1_close": 10.0,
    })
    df = _add_rolling_features(df)
    for col in ["ofi_sg", "spread_sg", "net_buy_fraction", "depth_imbalance_l1",
                "hurst_20", "hurst_60", "ou_halflife", "ofi_x_spread"]:
        assert col in df.columns, f"missing {col}"

def test_savitzky_golay_reduces_variance():
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=100, freq="1s"),
        "best_bid": 100.0, "best_ask": 100.1,
        "ofi_l1": np.random.randn(100) * 100,
        "spread_mean": np.random.randn(100) * 0.01 + 0.1,
        "volume": 1.0, "buy_volume": 0.5,
        "bid_depth_l1_close": 10.0, "ask_depth_l1_close": 10.0,
    })
    df = _add_rolling_features(df)
    assert df["ofi_sg"].std() < df["ofi_l1"].std()
```

## Acceptance Criteria

1. `POST /features/extract` writes Parquet to `{store}/exchange={e}/symbol={s}/date={d}/part.parquet`.
2. Rolling features computed: `ofi_sg`, `spread_sg`, `net_buy_fraction`, `depth_imbalance_l1`, `hurst_20`, `hurst_60`, `ou_halflife`, `kyle_lambda_60`, `ofi_x_spread`, `microprice_x_hawkes`.
3. Savitzky-Golay smoothing reduces variance of `ofi_l1` and `spread_mean`.
4. Hurst of random walk input is in range (0.3, 0.7).
5. OU half-life of AR(1) ϕ=0.9 series is in range (4, 12) bars.
6. When `microprice_mid_delta` is all-null, client-side derivation runs and `df.attrs["microprice_derived"]` is set.
7. Calling extract twice on the same date range overwrites the Parquet (idempotent).
8. Rows with insufficient rolling history (< window size) have null for that rolling feature.
9. All 4 unit tests pass.

## Dev Notes

- QuestDB HTTP paginates at `limit=offset,count` syntax — tested against QuestDB 8.2.1.
- `duckdb.execute("COPY (SELECT * FROM df) ...")` writes Parquet in-process — no separate DuckDB file needed.
- `scipy.signal.savgol_filter` requires at least `window_length` data points — guard with `len(df) >= 5`.
- Rolling `.apply()` with `raw=False` receives a Series — access `.values` inside the lambda.
