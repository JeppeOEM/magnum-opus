from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Union

import backtrader as bt
import httpx
import pandas as pd

_IDENT_RE = re.compile(r"^[A-Za-z0-9_\-\.]+$")

_TF_TABLE: dict[str, str] = {
    "1s": "snapshot_1s",
    "1m": "snapshot_1m",
    "5m": "snapshot_1m",
    "15m": "snapshot_15m",
    "1h": "snapshot_15m",
    "4h": "snapshot_15m",
    "1d": "snapshot_15m",
    "1w": "snapshot_15m",
}


def _validate_ident(value: str, field: str) -> None:
    if not _IDENT_RE.match(value):
        raise ValueError(f"Invalid {field}: {value!r}")


class InsufficientHistoryError(RuntimeError):
    def __init__(
        self, exchange: str, symbol: str, start: datetime, end: datetime
    ) -> None:
        super().__init__(
            f"No snapshot_1s data for {exchange}:{symbol} [{start} → {end}]"
        )
        self.exchange = exchange
        self.symbol = symbol
        self.start = start
        self.end = end


def _fetch_snapshot(
    questdb_http_addr: str,
    table: str,
    exchange: str,
    symbol: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Fetch OHLCV rows from any snapshot table.

    Table routing (snapshot_1s / snapshot_1m / snapshot_15m) is done by the
    caller via _TF_TABLE. No per-row tf filter is needed — each table stores
    exactly one timeframe.

    QuestDB returns SQL NULL as Python ``None`` in JSON.  All non-string
    columns are coerced to float so backtrader's linebuffer never receives
    ``None`` (which raises ``TypeError: must be real number, not NoneType``
    on Python 3.12+).
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware datetimes")
    _validate_ident(exchange, "exchange")
    _validate_ident(symbol, "symbol")
    _validate_ident(table, "table")
    ts_start = int(start.timestamp() * 1_000_000)
    ts_end = int(end.timestamp() * 1_000_000)
    query = (
        f"SELECT * FROM {table} "
        f"WHERE exchange = '{exchange}' AND symbol = '{symbol}' "
        f"AND ts >= {ts_start} AND ts < {ts_end} "
        f"ORDER BY ts ASC"
    )
    resp = httpx.get(
        f"{questdb_http_addr}/exec",
        params={"query": query},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"QuestDB error: {data['error']}")
    cols = [c["name"] for c in data.get("columns", [])]
    rows = data.get("dataset", [])
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows, columns=cols)
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").sort_index()

    # Replace None (QuestDB NULL) with NaN in every column that should be
    # numeric.  Text/varchar columns (exchange, symbol, tf, *_json) are
    # left as-is; everything else is coerced to float64.
    _TEXT_COLS = {"exchange", "symbol", "tf", "footprint_json", "single_print_levels_json"}
    for col in df.columns:
        if col not in _TEXT_COLS and df[col].dtype == object:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def _find_data_segments(df: pd.DataFrame) -> list[dict]:
    """Detect contiguous runs of valid bars (non-null close, gap_count == 0).

    Returns a list of dicts, one per segment::

        {"start": "YYYY-MM-DDTHH:MM:SS", "end": "...", "bars": N}

    Null-OHLCV bars (startup partial candles) and gap bars (service restarts)
    are treated as breaks between segments.  The caller can derive gap
    durations by comparing adjacent segment end/start timestamps.
    """
    if df.empty:
        return []

    close = pd.to_numeric(df.get("close", pd.Series(dtype=float)), errors="coerce")
    gap_cnt = pd.to_numeric(
        df.get("gap_count", pd.Series(0, index=df.index)), errors="coerce"
    ).fillna(0)
    valid: list[bool] = (close.notna() & (gap_cnt == 0)).tolist()
    timestamps: list[Any] = df.index.tolist()

    segments: list[dict] = []
    seg_start: int | None = None

    for i, ok in enumerate(valid):
        if ok and seg_start is None:
            seg_start = i
        elif not ok and seg_start is not None:
            segments.append(
                {
                    "start": str(timestamps[seg_start])[:19].replace(" ", "T"),
                    "end": str(timestamps[i - 1])[:19].replace(" ", "T"),
                    "bars": i - seg_start,
                }
            )
            seg_start = None

    if seg_start is not None:
        segments.append(
            {
                "start": str(timestamps[seg_start])[:19].replace(" ", "T"),
                "end": str(timestamps[-1])[:19].replace(" ", "T"),
                "bars": len(timestamps) - seg_start,
            }
        )

    return segments


def _fetch_snapshot_1s(
    questdb_http_addr: str,
    exchange: str,
    symbol: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    return _fetch_snapshot(questdb_http_addr, "snapshot_1s", exchange, symbol, start, end)


def _replace_gap_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Replace rows where gap_count > 0 with NaN, preserving the ts index.

    Excludes bool columns (cannot hold float NaN) and gap_count itself
    (retained so callers can still read the original gap count value).
    The canonical gap-bar sentinel for Backtrader strategies is
    math.isnan(self.data.close[0]).
    """
    if "gap_count" not in df.columns:
        return df
    gap_mask = df["gap_count"] > 0
    # bool columns cannot hold NaN; gap_count is kept for diagnostic purposes.
    numeric_cols = df.select_dtypes(include=["number"]).columns.difference(["gap_count"])
    df = df.copy()
    df.loc[gap_mask, numeric_cols] = float("nan")
    return df


# All non-OHLCV, non-identity columns from snapshot_1s as custom Backtrader lines.
_CUSTOM_LINES: tuple[str, ...] = (
    "quote_volume",
    "trade_count",
    "twap",
    "mid_price_open",
    "mid_price_high",
    "mid_price_low",
    "vwmp",
    "spread_high",
    "spread_low",
    "spread_mean",
    "effective_spread",
    "best_bid_open",
    "best_ask_open",
    "best_bid",
    "best_ask",
    "bid_depth_l1_open",
    "ask_depth_l1_open",
    "bid_depth_l2_open",
    "ask_depth_l2_open",
    "bid_depth_top10_open",
    "ask_depth_top10_open",
    "bid_depth_total_open",
    "ask_depth_total_open",
    "bid_depth_l1_close",
    "ask_depth_l1_close",
    "bid_depth_l2_close",
    "ask_depth_l2_close",
    "bid_depth_top10_close",
    "ask_depth_top10_close",
    "bid_depth_total_close",
    "ask_depth_total_close",
    "weighted_bid_price",
    "weighted_ask_price",
    "depth_to_1pct_bid",
    "depth_to_1pct_ask",
    "ofi",
    "ofi_l1",
    "buy_volume",
    "buy_count",
    "block_buy_volume",
    "block_sell_volume",
    "max_trade_size",
    "large_bid_orders",
    "large_ask_orders",
    "first_trade_offset_ms",
    "last_trade_offset_ms",
    "trade_clustering",
    "max_consecutive_run",
    "realized_vol",
    "realized_skewness",
    "uptick_count",
    "downtick_count",
    "bid_order_arrivals",
    "ask_order_arrivals",
    "bid_cancel_count",
    "ask_cancel_count",
    "ob_modify_count",
    "avg_bid_order_size",
    "avg_ask_order_size",
    "best_bid_changes",
    "best_ask_changes",
    "quote_stuff_ratio",
    "trade_sign_autocorr",
    "inter_trade_interval_std_ms",
    "num_trade_price_levels",
    "is_partial",
    "gap_count",
    "bar_count",
)

_STANDARD_PARAMS: tuple[tuple[str, str | int | None], ...] = (
    ("datetime", None),
    ("open", "open"),
    ("high", "high"),
    ("low", "low"),
    ("close", "close"),
    ("volume", "volume"),
    ("openinterest", -1),
)
_CUSTOM_PARAMS: tuple[tuple[str, str], ...] = tuple(
    (col, col) for col in _CUSTOM_LINES
)


class QuestDBFeed(bt.feeds.PandasData):  # type: ignore[misc]
    """Backtrader data feed backed by QuestDB snapshot tables.

    Gap bars (gap_count > 0) and null-OHLCV bars (startup partial candles)
    are replaced with NaN rows so strategy next() can detect them via
    ``math.isnan(self.data.close[0])``.

    After construction the attribute ``data_segments`` contains a list of
    dicts describing the contiguous valid-data segments found in the fetched
    window::

        [{"start": "2026-05-22T17:59:30", "end": "2026-05-22T18:10:00", "bars": 631}, ...]

    This is exposed on BacktestResult so the dashboard can show the user
    exactly which time windows the backtest actually ran on.
    """

    lines: tuple[str, ...] = _CUSTOM_LINES
    params: tuple[tuple[str, str | int | None], ...] = (
        _STANDARD_PARAMS + _CUSTOM_PARAMS
    )

    def __init__(
        self,
        questdb_http_addr: str,
        exchange: str,
        symbol: str,
        start: datetime,
        end: datetime,
        tf: str = "1s",
        **kwargs: Any,
    ) -> None:
        table = _TF_TABLE.get(tf, "snapshot_1s")
        df = _fetch_snapshot(questdb_http_addr, table, exchange, symbol, start, end)
        if df.empty:
            raise InsufficientHistoryError(exchange, symbol, start, end)
        # Detect segments BEFORE replacing gap rows with NaN (gap_count intact).
        self.data_segments: list[dict] = _find_data_segments(df)
        df = _replace_gap_rows(df)
        # Drop every row where close is NaN — these are gap bars (gap_count > 0)
        # and null startup bars (partial candles with no OHLCV data).
        # Backtrader market orders execute at the NEXT bar's open; a NaN open
        # corrupts the broker state (NaN fill price → NaN portfolio value).
        # Removing these rows is safe: backtrader only cares about timestamps
        # for ordering, not continuity, so the equity curve is still correct.
        df = df.dropna(subset=["close"])
        if df.empty:
            raise InsufficientHistoryError(exchange, symbol, start, end)
        df = df.drop(columns=["exchange", "symbol", "tf"], errors="ignore")
        # backtrader metaclass sets self.p before __init__; assign dataname
        # directly so PandasData.start() finds the pre-fetched DataFrame.
        self.p.dataname = df
        super().__init__(**kwargs)
