from __future__ import annotations

import re
from datetime import datetime
from typing import Any

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
    tf: str | None = None,
) -> pd.DataFrame:
    """Fetch OHLCV rows from any snapshot table.

    When ``tf`` is provided a ``AND tf = '{tf}'`` filter is added (needed for
    multi-TF tables snapshot_1m and snapshot_15m which store multiple TFs).
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware datetimes")
    _validate_ident(exchange, "exchange")
    _validate_ident(symbol, "symbol")
    _validate_ident(table, "table")
    ts_start = int(start.timestamp() * 1_000_000)
    ts_end = int(end.timestamp() * 1_000_000)
    tf_filter = f" AND tf = '{tf}'" if tf and tf != "1s" else ""
    query = (
        f"SELECT * FROM {table} "
        f"WHERE exchange = '{exchange}' AND symbol = '{symbol}' "
        f"AND ts >= {ts_start} AND ts < {ts_end}"
        f"{tf_filter} "
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
    return df


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
    """Backtrader data feed backed by QuestDB snapshot_1s.

    Gap bars (gap_count > 0) are replaced with NaN rows so strategy
    next() can detect them via math.isnan(self.data.close[0]).
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
        df = _fetch_snapshot(questdb_http_addr, table, exchange, symbol, start, end, tf=tf)
        if df.empty:
            raise InsufficientHistoryError(exchange, symbol, start, end)
        df = _replace_gap_rows(df)
        df = df.drop(columns=["exchange", "symbol", "tf"], errors="ignore")
        # backtrader metaclass sets self.p before __init__; assign dataname
        # directly so PandasData.start() finds the pre-fetched DataFrame.
        self.p.dataname = df
        super().__init__(**kwargs)
