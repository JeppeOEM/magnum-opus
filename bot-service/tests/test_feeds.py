from __future__ import annotations

import math
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from bot_service.backtest.feeds import (
    InsufficientHistoryError,
    QuestDBFeed,
    _TF_TABLE,
    _fetch_snapshot,
    _fetch_snapshot_1s,
    _replace_gap_rows,
)


# ── helpers ───────────────────────────────────────────────────────────────────

_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
_END = datetime(2024, 1, 2, tzinfo=timezone.utc)
_ADDR = "http://localhost:9000"


def _make_ts(hour: int) -> str:
    return f"2024-01-01T{hour:02d}:00:00.000000Z"


def _questdb_resp(rows: list[list[object]], cols: list[str]) -> dict[str, object]:
    return {
        "columns": [{"name": c} for c in cols],
        "dataset": rows,
    }


def _minimal_cols() -> list[str]:
    return ["ts", "exchange", "symbol", "open", "high", "low", "close",
            "volume", "gap_count"]


def _full_df(n: int = 5) -> pd.DataFrame:
    """Full DataFrame matching all QuestDBFeed columns for cerebro compatibility."""
    ts_idx = pd.date_range("2024-01-01", periods=n, freq="s", tz=timezone.utc)
    data: dict[str, object] = {
        "exchange": ["kucoin"] * n,
        "symbol": ["BTCUSDT"] * n,
        "open": [100.0] * n,
        "high": [101.0] * n,
        "low": [99.0] * n,
        "close": [100.5] * n,
        "volume": [10.0] * n,
        "quote_volume": [1000.0] * n,
        "trade_count": [5] * n,
        "twap": [100.2] * n,
        "mid_price_open": [100.1] * n,
        "mid_price_high": [100.5] * n,
        "mid_price_low": [99.5] * n,
        "vwmp": [100.2] * n,
        "spread_high": [0.1] * n,
        "spread_low": [0.05] * n,
        "spread_mean": [0.07] * n,
        "effective_spread": [0.08] * n,
        "best_bid_open": [99.9] * n,
        "best_ask_open": [100.1] * n,
        "best_bid": [99.9] * n,
        "best_ask": [100.1] * n,
        "bid_depth_l1_open": [50.0] * n,
        "ask_depth_l1_open": [50.0] * n,
        "bid_depth_l2_open": [100.0] * n,
        "ask_depth_l2_open": [100.0] * n,
        "bid_depth_top10_open": [500.0] * n,
        "ask_depth_top10_open": [500.0] * n,
        "bid_depth_total_open": [1000.0] * n,
        "ask_depth_total_open": [1000.0] * n,
        "bid_depth_l1_close": [50.0] * n,
        "ask_depth_l1_close": [50.0] * n,
        "bid_depth_l2_close": [100.0] * n,
        "ask_depth_l2_close": [100.0] * n,
        "bid_depth_top10_close": [500.0] * n,
        "ask_depth_top10_close": [500.0] * n,
        "bid_depth_total_close": [1000.0] * n,
        "ask_depth_total_close": [1000.0] * n,
        "weighted_bid_price": [99.9] * n,
        "weighted_ask_price": [100.1] * n,
        "depth_to_1pct_bid": [200.0] * n,
        "depth_to_1pct_ask": [200.0] * n,
        "ofi": [0.1] * n,
        "ofi_l1": [0.1] * n,
        "buy_volume": [5.0] * n,
        "buy_count": [3] * n,
        "block_buy_volume": [0.0] * n,
        "block_sell_volume": [0.0] * n,
        "max_trade_size": [2.0] * n,
        "large_bid_orders": [0] * n,
        "large_ask_orders": [0] * n,
        "first_trade_offset_ms": [100] * n,
        "last_trade_offset_ms": [900] * n,
        "trade_clustering": [0.5] * n,
        "max_consecutive_run": [2] * n,
        "realized_vol": [0.01] * n,
        "realized_skewness": [0.0] * n,
        "uptick_count": [3] * n,
        "downtick_count": [2] * n,
        "bid_order_arrivals": [10] * n,
        "ask_order_arrivals": [10] * n,
        "bid_cancel_count": [2] * n,
        "ask_cancel_count": [2] * n,
        "ob_modify_count": [5] * n,
        "avg_bid_order_size": [10.0] * n,
        "avg_ask_order_size": [10.0] * n,
        "best_bid_changes": [3] * n,
        "best_ask_changes": [3] * n,
        "quote_stuff_ratio": [0.1] * n,
        "trade_sign_autocorr": [0.0] * n,
        "inter_trade_interval_std_ms": [50.0] * n,
        "num_trade_price_levels": [3] * n,
        "is_partial": [False] * n,
        "gap_count": [0] * n,
        "bar_count": [1] * n,
    }
    return pd.DataFrame(data, index=ts_idx)


# ── InsufficientHistoryError tests ───────────────────────────────────────────

@pytest.mark.l1
def test_insufficient_history_error() -> None:
    err = InsufficientHistoryError("kucoin", "BTCUSDT", _START, _END)
    assert err.exchange == "kucoin"
    assert err.symbol == "BTCUSDT"
    assert "kucoin" in str(err)
    assert "BTCUSDT" in str(err)


# ── _fetch_snapshot_1s tests ──────────────────────────────────────────────────

@pytest.mark.l1
def test_fetch_returns_dataframe_sorted_by_ts() -> None:
    cols = _minimal_cols()
    rows = [
        [_make_ts(2), "kucoin", "BTCUSDT", 101.0, 102.0, 100.0, 101.5, 10.0, 0],
        [_make_ts(0), "kucoin", "BTCUSDT", 100.0, 101.0, 99.0, 100.5, 10.0, 0],
        [_make_ts(1), "kucoin", "BTCUSDT", 100.5, 101.5, 99.5, 101.0, 10.0, 0],
    ]
    mock_resp = MagicMock()
    mock_resp.json.return_value = _questdb_resp(rows, cols)

    with patch("bot_service.backtest.feeds.httpx.get", return_value=mock_resp):
        df = _fetch_snapshot_1s(_ADDR, "kucoin", "BTCUSDT", _START, _END)

    assert isinstance(df.index, pd.DatetimeIndex)
    assert list(df.index) == sorted(df.index)
    assert len(df) == 3


@pytest.mark.l1
def test_fetch_empty_returns_empty_dataframe() -> None:
    cols = _minimal_cols()
    mock_resp = MagicMock()
    mock_resp.json.return_value = _questdb_resp([], cols)

    with patch("bot_service.backtest.feeds.httpx.get", return_value=mock_resp):
        df = _fetch_snapshot_1s(_ADDR, "kucoin", "BTCUSDT", _START, _END)

    assert df.empty


# ── _replace_gap_rows tests ───────────────────────────────────────────────────

@pytest.mark.l1
def test_gap_rows_replaced_with_nan() -> None:
    ts = pd.date_range("2024-01-01", periods=3, freq="s", tz=timezone.utc)
    df = pd.DataFrame(
        {"close": [100.0, 101.0, 102.0], "gap_count": [0, 1, 0]},
        index=ts,
    )
    result = _replace_gap_rows(df)
    assert result["close"].iloc[0] == 100.0
    assert math.isnan(result["close"].iloc[1])
    assert result["close"].iloc[2] == 102.0
    assert list(result.index) == list(ts)


@pytest.mark.l1
def test_no_gap_rows_unchanged() -> None:
    ts = pd.date_range("2024-01-01", periods=3, freq="s", tz=timezone.utc)
    df = pd.DataFrame(
        {"close": [1.0, 2.0, 3.0], "gap_count": [0, 0, 0]},
        index=ts,
    )
    result = _replace_gap_rows(df)
    assert list(result["close"]) == [1.0, 2.0, 3.0]


# ── QuestDBFeed tests ─────────────────────────────────────────────────────────

@pytest.mark.l1
def test_questdbfeed_raises_on_empty_data() -> None:
    with patch(
        "bot_service.backtest.feeds._fetch_snapshot",
        return_value=pd.DataFrame(),
    ):
        with pytest.raises(InsufficientHistoryError):
            QuestDBFeed(_ADDR, "kucoin", "BTCUSDT", _START, _END)


@pytest.mark.l1
def test_questdbfeed_has_close_line() -> None:
    df = _full_df()
    with patch("bot_service.backtest.feeds._fetch_snapshot", return_value=df):
        feed = QuestDBFeed(_ADDR, "kucoin", "BTCUSDT", _START, _END)
    assert hasattr(feed, "close")
    assert hasattr(feed, "lines")


@pytest.mark.l1
def test_questdbfeed_cerebro_runs() -> None:
    import backtrader as bt  # type: ignore[import]

    df = _full_df(10)
    with patch("bot_service.backtest.feeds._fetch_snapshot", return_value=df):
        feed = QuestDBFeed(_ADDR, "kucoin", "BTCUSDT", _START, _END)

    cerebro = bt.Cerebro()
    cerebro.adddata(feed)
    cerebro.run()


@pytest.mark.l1
def test_fetch_raises_on_naive_datetime() -> None:
    naive = datetime(2024, 1, 1)  # no tzinfo
    with pytest.raises(ValueError, match="timezone-aware"):
        _fetch_snapshot_1s(_ADDR, "kucoin", "BTCUSDT", naive, _END)


@pytest.mark.l1
def test_fetch_raises_on_questdb_error_response() -> None:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"error": "table 'snapshot_1s' does not exist"}

    with patch("bot_service.backtest.feeds.httpx.get", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="QuestDB error"):
            _fetch_snapshot_1s(_ADDR, "kucoin", "BTCUSDT", _START, _END)


# ── TF → table routing ────────────────────────────────────────────────────────

@pytest.mark.parametrize("tf,expected_table", [
    ("1s",  "snapshot_1s"),
    ("1m",  "snapshot_1m"),
    ("5m",  "snapshot_1m"),
    ("15m", "snapshot_15m"),
    ("4h",  "snapshot_15m"),
    ("unknown", "snapshot_1s"),
])
@pytest.mark.l1
def test_tf_table_mapping(tf: str, expected_table: str) -> None:
    assert _TF_TABLE.get(tf, "snapshot_1s") == expected_table


@pytest.mark.parametrize("tf,expected_table", [
    ("1s",  "snapshot_1s"),
    ("1m",  "snapshot_1m"),
    ("15m", "snapshot_15m"),
])
@pytest.mark.l1
def test_questdbfeed_queries_correct_table_for_tf(
    tf: str, expected_table: str
) -> None:
    """QuestDBFeed passes the correct table name to _fetch_snapshot."""
    queried: list[str] = []

    def _spy_fetch(addr: str, table: str, exchange: str, symbol: str,
                   start: object, end: object, **kw: object) -> object:
        queried.append(table)
        return _full_df(5)

    with patch("bot_service.backtest.feeds._fetch_snapshot", side_effect=_spy_fetch):
        QuestDBFeed(_ADDR, "kucoin", "BTCUSDT", _START, _END, tf=tf)

    assert queried == [expected_table]


@pytest.mark.l1
def test_questdbfeed_unknown_tf_falls_back_to_snapshot_1s() -> None:
    queried: list[str] = []

    def _spy_fetch(addr: str, table: str, exchange: str, symbol: str,
                   start: object, end: object, **kw: object) -> object:
        queried.append(table)
        return _full_df(5)

    with patch("bot_service.backtest.feeds._fetch_snapshot", side_effect=_spy_fetch):
        QuestDBFeed(_ADDR, "kucoin", "BTCUSDT", _START, _END, tf="3d")

    assert queried == ["snapshot_1s"]


# ── CLI ───────────────────────────────────────────────────────────────────────

@pytest.mark.l1
def test_cli_help_exits_zero(tmp_path: object) -> None:
    from bot_service.backtest.cli import main
    import pytest as _pytest
    with _pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


@pytest.mark.l1
def test_cli_missing_required_arg_exits_nonzero() -> None:
    from bot_service.backtest.cli import main
    import pytest as _pytest
    with _pytest.raises(SystemExit) as exc_info:
        main(["--symbol", "BTCUSDT"])  # missing --strategy and --start and --end
    assert exc_info.value.code != 0


@pytest.mark.l1
def test_cli_strategy_not_found_returns_1(tmp_path: "Path") -> None:
    from bot_service.backtest.cli import main
    result = main([
        "--strategy", "NonExistent",
        "--symbol", "BTCUSDT",
        "--start", "2026-01-01",
        "--end", "2026-02-01",
        "--strategies-dir", str(tmp_path),
    ])
    assert result == 1
