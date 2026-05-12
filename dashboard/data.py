import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta

import pandas as pd
import redis as redis_lib
import requests

logger = logging.getLogger(__name__)

_SAFE_IDENT = re.compile(r'^[A-Za-z0-9._\-]+$')
_SAFE_TS = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$')

_redis_client = redis_lib.from_url(os.environ.get("REDIS_URL", "redis://redis:6379"))


def _decode(v) -> str:
    return v.decode() if isinstance(v, bytes) else v


# (source_table, pandas_resample_freq | None for direct, n_base_rows_per_bar)
_TF_INFO: dict[str, tuple[str, str | None, int]] = {
    "1s":  ("snapshot_1s",  None,    1),
    "1m":  ("snapshot_1m",  None,    1),
    "2m":  ("snapshot_1m",  "2min",  2),
    "3m":  ("snapshot_1m",  "3min",  3),
    "4m":  ("snapshot_1m",  "4min",  4),
    "6m":  ("snapshot_1m",  "6min",  6),
    "10m": ("snapshot_1m",  "10min", 10),
    "12m": ("snapshot_1m",  "12min", 12),
    "15m": ("snapshot_15m", None,    1),
    "30m": ("snapshot_15m", "30min", 2),
    "45m": ("snapshot_15m", "45min", 3),
    "1h":  ("snapshot_15m", "1h",    4),
    "2h":  ("snapshot_15m", "2h",    8),
    "3h":  ("snapshot_15m", "3h",    12),
    "4h":  ("snapshot_15m", "4h",    16),
    "6h":  ("snapshot_15m", "6h",    24),
    "8h":  ("snapshot_15m", "8h",    32),
    "12h": ("snapshot_15m", "12h",   48),
    "1d":  ("snapshot_15m", "1D",    96),
    "1w":  ("snapshot_15m", "W-MON", 672),
}

_TF_DURATION: dict[str, timedelta] = {
    "1s": timedelta(seconds=1),
    "1m": timedelta(minutes=1),
    "2m": timedelta(minutes=2),
    "3m": timedelta(minutes=3),
    "4m": timedelta(minutes=4),
    "6m": timedelta(minutes=6),
    "10m": timedelta(minutes=10),
    "12m": timedelta(minutes=12),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "45m": timedelta(minutes=45),
    "1h": timedelta(hours=1),
    "2h": timedelta(hours=2),
    "3h": timedelta(hours=3),
    "4h": timedelta(hours=4),
    "6h": timedelta(hours=6),
    "8h": timedelta(hours=8),
    "12h": timedelta(hours=12),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}


def _merge_footprint_jsons(fps: list[str | None]) -> str | None:
    merged: dict[str, dict] = {}
    for fp_str in fps:
        if not fp_str:
            continue
        try:
            fp = json.loads(fp_str)
            if not isinstance(fp, dict):
                continue
            for price, cell in fp.items():
                if not isinstance(cell, dict):
                    continue
                if price not in merged:
                    merged[price] = {"b": 0.0, "s": 0.0}
                merged[price]["b"] = merged[price]["b"] + float(cell.get("b") or 0)
                merged[price]["s"] = merged[price]["s"] + float(cell.get("s") or 0)
        except Exception:
            continue
    return json.dumps(merged) if merged else None


def _merge_single_print_levels(spl_list: list[str | None]) -> str | None:
    merged: set[str] = set()
    for spl_str in spl_list:
        if not spl_str:
            continue
        try:
            levels = json.loads(spl_str)
            if isinstance(levels, list):
                merged.update(str(p) for p in levels)
        except Exception:
            continue
    return json.dumps(sorted(merged)) if merged else None


def _safe_float(v) -> float | None:
    try:
        f = float(v)
        import math
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _aggregate_rows(rows: list[dict], freq: str, tf: str) -> list[dict]:
    if not rows:
        return []
    df = pd.DataFrame(rows)
    if "ts" not in df.columns:
        return rows

    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts"]).sort_values("ts")
    df = df.set_index("ts")

    now = datetime.now(timezone.utc)
    duration = _TF_DURATION.get(tf)

    # Numeric sum columns
    sum_cols = [c for c in ["volume", "quote_volume", "buy_volume", "sell_volume",
                             "trade_count", "buy_count", "block_buy_volume", "block_sell_volume",
                             "bid_order_arrivals", "ask_order_arrivals", "bid_cancel_count",
                             "ask_cancel_count", "ob_modify_count", "best_bid_changes",
                             "best_ask_changes", "uptick_count", "downtick_count",
                             "imbalance_buy_count", "imbalance_sell_count",
                             "imbalance_stack_buy", "imbalance_stack_sell",
                             "single_print_count", "large_bid_orders", "large_ask_orders",
                             "gap_count", "bar_count"] if c in df.columns]

    # Last-value columns (carry-forward state or close-only values)
    last_cols = [c for c in ["close", "best_bid", "best_ask", "vwmp",
                              "spread_mean", "effective_spread", "bid_depth_l1_close",
                              "ask_depth_l1_close", "bid_depth_l2_close", "ask_depth_l2_close",
                              "bid_depth_top10_close", "ask_depth_top10_close",
                              "bid_depth_total_close", "ask_depth_total_close",
                              "weighted_bid_price", "weighted_ask_price",
                              "depth_to_1pct_bid", "depth_to_1pct_ask",
                              "cum_delta", "cvd_divergence", "poc_price",
                              "value_area_high", "value_area_low", "poc_volume",
                              "imbalance_ratio", "unfinished_top", "unfinished_bottom",
                              "footprint_delta_divergence", "iceberg_price",
                              "last_trade_offset_ms", "exchange", "symbol"] if c in df.columns]

    # First-value columns
    first_cols = [c for c in ["open", "best_bid_open", "best_ask_open", "mid_price_open",
                               "bid_depth_l1_open", "ask_depth_l1_open",
                               "bid_depth_l2_open", "ask_depth_l2_open",
                               "bid_depth_top10_open", "ask_depth_top10_open",
                               "bid_depth_total_open", "ask_depth_total_open",
                               "first_trade_offset_ms"] if c in df.columns]

    # Max columns
    max_cols = [c for c in ["high", "mid_price_high", "spread_high", "max_trade_size",
                             "max_consecutive_run"] if c in df.columns]

    # Min columns
    min_cols = [c for c in ["low", "mid_price_low", "spread_low"] if c in df.columns]

    # Any columns (boolean)
    any_cols = [c for c in ["absorption_detected", "iceberg_bid_detected",
                             "iceberg_ask_detected"] if c in df.columns]

    for col in df.columns:
        if col not in (sum_cols + last_cols + first_cols + max_cols + min_cols + any_cols +
                       ["footprint_json", "single_print_levels_json", "twap", "ofi", "ofi_l1",
                        "trade_sign_autocorr", "inter_trade_interval_std_ms",
                        "num_trade_price_levels", "realized_vol", "realized_skewness",
                        "trade_clustering", "avg_bid_order_size", "avg_ask_order_size",
                        "quote_stuff_ratio", "is_partial"]):
            if col not in last_cols:
                last_cols.append(col)

    agg_spec: dict = {}
    for c in sum_cols:
        agg_spec[c] = "sum"
    for c in last_cols:
        agg_spec[c] = "last"
    for c in first_cols:
        if c not in agg_spec:
            agg_spec[c] = "first"
    for c in max_cols:
        if c not in agg_spec:
            agg_spec[c] = "max"
    for c in min_cols:
        if c not in agg_spec:
            agg_spec[c] = "min"
    for c in any_cols:
        if c not in agg_spec:
            agg_spec[c] = "max"

    # Footprint and single_print handled manually
    for c in ["footprint_json", "single_print_levels_json", "is_partial",
              "twap", "ofi", "ofi_l1", "trade_sign_autocorr",
              "inter_trade_interval_std_ms", "num_trade_price_levels",
              "realized_vol", "realized_skewness", "trade_clustering",
              "avg_bid_order_size", "avg_ask_order_size", "quote_stuff_ratio"]:
        if c in df.columns and c not in agg_spec:
            agg_spec[c] = "last"

    grouped = df.resample(freq, label="left", closed="left")
    agg_df = grouped.agg({k: v for k, v in agg_spec.items() if k in df.columns})

    # Merge footprint JSONs per group
    if "footprint_json" in df.columns:
        fp_merged = grouped["footprint_json"].apply(
            lambda s: _merge_footprint_jsons(list(s))
        )
        agg_df["footprint_json"] = fp_merged

    # Union-merge single-print level arrays per group
    if "single_print_levels_json" in df.columns:
        spl_merged = grouped["single_print_levels_json"].apply(
            lambda s: _merge_single_print_levels(list(s))
        )
        agg_df["single_print_levels_json"] = spl_merged

    agg_df = agg_df.reset_index()
    agg_df["ts"] = agg_df["ts"].dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    # Drop incomplete current window (window open time + TF duration > now)
    if duration is not None:
        def _is_complete(row_ts_str: str) -> bool:
            try:
                t = pd.Timestamp(row_ts_str, tz="UTC").to_pydatetime()
                return t + duration <= now
            except Exception:
                return True
        agg_df = agg_df[agg_df["ts"].apply(_is_complete)]

    return agg_df.to_dict(orient="records")


def fetch_history(exchange: str, symbol: str, questdb_url: str, limit: int = 500, tf: str = "1s") -> list[dict]:
    if not _SAFE_IDENT.match(exchange) or not _SAFE_IDENT.match(symbol):
        logger.error("fetch_history: unsafe exchange=%r symbol=%r rejected", exchange, symbol)
        return []

    table, freq, n = _TF_INFO.get(tf, ("snapshot_1s", None, 1))
    fetch_limit = limit if freq is None else min(limit * n + 10, 50_000)

    query = (
        f"SELECT * FROM {table} "
        f"WHERE exchange='{exchange}' AND symbol='{symbol}' "
        f"ORDER BY ts DESC LIMIT {fetch_limit}"
    )
    try:
        resp = requests.get(f"{questdb_url}/exec", params={"query": query}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("QuestDB history fetch failed exchange=%s symbol=%s: %s", exchange, symbol, e)
        return []

    try:
        body = resp.json()
    except ValueError as e:
        logger.error("QuestDB response not JSON exchange=%s symbol=%s: %s", exchange, symbol, e)
        return []

    cols = [c["name"] for c in body.get("columns", [])]
    rows = [dict(zip(cols, row)) for row in body.get("dataset", [])]
    rows.reverse()  # ORDER BY ts DESC → ascending (newest last)

    if freq is not None:
        rows = _aggregate_rows(rows, freq, tf)

    return rows[-limit:]


def fetch_new_candles(exchange: str, symbol: str, last_ts: str, questdb_url: str, tf: str = "1s") -> list[dict]:
    if not _SAFE_IDENT.match(exchange) or not _SAFE_IDENT.match(symbol):
        logger.error("fetch_new_candles: unsafe exchange=%r symbol=%r", exchange, symbol)
        return []
    if not last_ts:
        logger.error("fetch_new_candles: last_ts is None or empty")
        return []
    if not _SAFE_TS.match(last_ts):
        logger.error("fetch_new_candles: unsafe last_ts=%r", last_ts)
        return []

    table, freq, _ = _TF_INFO.get(tf, ("snapshot_1s", None, 1))

    if freq is None:
        # Direct table: simple incremental fetch
        query = (
            f"SELECT * FROM {table} "
            f"WHERE exchange='{exchange}' AND symbol='{symbol}' AND ts > '{last_ts}' "
            f"ORDER BY ts ASC LIMIT 100"
        )
        try:
            resp = requests.get(f"{questdb_url}/exec", params={"query": query}, timeout=5)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("QuestDB live poll failed exchange=%s symbol=%s: %s", exchange, symbol, e)
            return []
        try:
            body = resp.json()
        except ValueError as e:
            logger.error("QuestDB live poll response not JSON exchange=%s symbol=%s: %s", exchange, symbol, e)
            return []
        cols = [c["name"] for c in body.get("columns", [])]
        return [dict(zip(cols, row)) for row in body.get("dataset", [])]

    # Aggregated TF: fetch base rows from last_ts onwards, aggregate, return complete bars.
    query = (
        f"SELECT * FROM {table} "
        f"WHERE exchange='{exchange}' AND symbol='{symbol}' AND ts > '{last_ts}' "
        f"ORDER BY ts ASC LIMIT 2000"
    )
    try:
        resp = requests.get(f"{questdb_url}/exec", params={"query": query}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("QuestDB live poll failed exchange=%s symbol=%s: %s", exchange, symbol, e)
        return []
    try:
        body = resp.json()
    except ValueError as e:
        logger.error("QuestDB live poll response not JSON exchange=%s symbol=%s: %s", exchange, symbol, e)
        return []

    cols = [c["name"] for c in body.get("columns", [])]
    rows = [dict(zip(cols, row)) for row in body.get("dataset", [])]
    if not rows:
        return []
    return _aggregate_rows(rows, freq, tf)


def fetch_ob_snapshot(exchange: str, symbol: str) -> str:
    stream_key = f"ob_features:{exchange}:{symbol}"
    try:
        entries = _redis_client.xrevrange(stream_key, count=1)
        if entries:
            return _decode(entries[0][0])
        return '0'
    except redis_lib.RedisError as e:
        logger.error("ob_features cursor seed failed %s: %s", stream_key, e)
        return '0'


def fetch_ob_live(exchange: str, symbol: str, cursor_id: str) -> tuple[list[dict], str]:
    stream_key = f"ob_features:{exchange}:{symbol}"
    try:
        result = _redis_client.xread({stream_key: cursor_id}, count=100)
        if not result:
            return [], cursor_id
        entries_raw = result[0][1]
        entries = []
        new_cursor = cursor_id
        for entry_id, fields in entries_raw:
            entry_id_str = _decode(entry_id)
            row = {_decode(k): _decode(v) for k, v in fields.items()}
            row["_id"] = entry_id_str
            entries.append(row)
            new_cursor = entry_id_str
        return entries, new_cursor
    except redis_lib.RedisError as e:
        logger.error("ob_features XREAD failed %s cursor=%s: %s", stream_key, cursor_id, e)
        return [], cursor_id
