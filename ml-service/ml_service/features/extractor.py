"""Feature store assembler: pulls snapshot_1s from QuestDB, adds rolling features,
writes Hive-partitioned Parquet at {store}/exchange={e}/symbol={s}/date={d}/part.parquet."""
from __future__ import annotations

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
    """Fetch one day of snapshot_1s from QuestDB HTTP API, paginating in 50k-row batches."""
    start = f"{day}T00:00:00Z"
    end = f"{day + timedelta(days=1)}T00:00:00Z"
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
        chunks = [series[i : i + lag] for i in range(0, n - lag, lag)]
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
    """OU half-life from AR(1) fit: halflife = -ln(2)/ln(ϕ). Returns 60.0 as safe default."""
    if len(series) < 10:
        return 60.0
    y, x = series[1:], series[:-1]
    # Guard: linregress raises on constant x (all identical values)
    if np.ptp(x) < 1e-10:
        return 60.0
    slope, *_ = linregress(x, y)
    if slope <= 0 or slope >= 1:
        return 60.0
    hl = -np.log(2) / np.log(slope)
    return float(np.clip(hl, 1.0, 300.0))


def _kyle_lambda(delta_mid: np.ndarray, ofi: np.ndarray) -> float:
    """Kyle's lambda: OLS slope of Δmid ~ λ × OFI."""
    mask = np.isfinite(delta_mid) & np.isfinite(ofi)
    if mask.sum() < 10:
        return 0.0
    slope, *_ = linregress(ofi[mask], delta_mid[mask])
    return float(slope)


def _add_rolling_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add all rolling statistical and interaction features in-place."""
    bid = df["best_bid"].ffill()
    ask = df["best_ask"].ffill()
    mid = (bid + ask) / 2

    # Savitzky-Golay smoothing (window=5, polyorder=2) — reduces noise before derivatives
    if len(df) >= 5:
        df["ofi_sg"] = savgol_filter(df["ofi_l1"].fillna(0).values, 5, 2)
        df["spread_sg"] = savgol_filter(df["spread_mean"].fillna(0).values, 5, 2)
    else:
        df["ofi_sg"] = df.get("ofi_l1", pd.Series(0.0, index=df.index))
        df["spread_sg"] = df.get("spread_mean", pd.Series(0.0, index=df.index))

    # Net buy fraction
    vol = df.get("volume", pd.Series(0.0, index=df.index)).fillna(0)
    bvol = df.get("buy_volume", pd.Series(0.0, index=df.index)).fillna(0)
    df["net_buy_fraction"] = np.where(vol > 0, bvol / vol, 0.5)

    # Depth imbalance L1
    bl1 = df.get("bid_depth_l1_close", pd.Series(0.0, index=df.index)).fillna(0)
    al1 = df.get("ask_depth_l1_close", pd.Series(0.0, index=df.index)).fillna(0)
    df["depth_imbalance_l1"] = (bl1 - al1) / (bl1 + al1 + 1e-9)

    # Rolling Hurst exponent (20 and 60 bars) on mid-price log-returns
    log_ret = np.log(mid.replace(0, np.nan)).diff().fillna(0).values
    df["hurst_20"] = (
        pd.Series(log_ret).rolling(20).apply(lambda x: _hurst(x.values), raw=False).values
    )
    df["hurst_60"] = (
        pd.Series(log_ret).rolling(60).apply(lambda x: _hurst(x.values), raw=False).values
    )

    # Rolling OU half-life on microprice_mid_delta (60 bars)
    mmd = df.get("microprice_mid_delta", pd.Series(0.0, index=df.index)).fillna(0).values
    df["ou_halflife"] = (
        pd.Series(mmd).rolling(60).apply(lambda x: _ou_halflife(x.values), raw=False).values
    )

    # Rolling Kyle's lambda (60 bars): Δmid vs smoothed OFI
    delta_mid = np.diff(mid.values, prepend=mid.values[0] if len(mid) > 0 else 0.0)
    ofi = df["ofi_sg"].values

    def _kyle_window(x):
        # x is a Series of (delta_mid, ofi) tuples encoded as floats via index
        # Use the positional index to look up the actual delta_mid and ofi values
        idx = x.index
        return _kyle_lambda(delta_mid[idx], ofi[idx])

    df["kyle_lambda_60"] = (
        pd.Series(range(len(df))).rolling(60).apply(
            lambda x: _kyle_lambda(delta_mid[x.astype(int)], ofi[x.astype(int)]),
            raw=True,
        ).values
    )

    # Interaction features
    df["ofi_x_spread"] = df["ofi_sg"] * df["spread_sg"]
    _zero = pd.Series(0.0, index=df.index)
    mmd_s = df["microprice_mid_delta"].fillna(0) if "microprice_mid_delta" in df.columns else _zero
    haw_s = df["hawkes_intensity"].fillna(0) if "hawkes_intensity" in df.columns else _zero
    df["microprice_x_hawkes"] = mmd_s * haw_s

    # Client-side fallback: derive microprice_mid_delta when Epic 35 not yet in production
    mp_col = df.get("microprice_mid_delta")
    if mp_col is None or mp_col.isna().all():
        mp = (ask * bl1 + bid * al1) / (bl1 + al1 + 1e-9)
        df["microprice_mid_delta"] = np.where(mid > 0, (mp - mid) / mid * 10000, 0)
        df.attrs["microprice_derived"] = True

    cancel_col = df.get("cancel_bias")
    if cancel_col is None or cancel_col.isna().all():
        bc = df.get("bid_cancel_count", pd.Series(0, index=df.index)).fillna(0)
        ac = df.get("ask_cancel_count", pd.Series(0, index=df.index)).fillna(0)
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
    """Extract snapshot_1s data for the given date range and write Parquet feature files."""
    stats: dict = {"days_written": 0, "rows_total": 0}
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
            / f"exchange={exchange}"
            / f"symbol={symbol}"
            / f"date={current}"
        )
        out_path.mkdir(parents=True, exist_ok=True)
        parquet_file = out_path / "part.parquet"
        duckdb.execute(
            f"COPY (SELECT * FROM df) TO '{parquet_file}' (FORMAT PARQUET, COMPRESSION SNAPPY)"
        )
        stats["days_written"] += 1
        stats["rows_total"] += len(df)
        log.info("day_extracted", date=str(current), rows=len(df))
        current += timedelta(days=1)
    return stats
