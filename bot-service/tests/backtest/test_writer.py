from __future__ import annotations

import pytest

from bot_service.backtest.writer import _CostBasisTracker


# ---------------------------------------------------------------------------
# _CostBasisTracker unit tests — Story 25-3
# ---------------------------------------------------------------------------


def test_tracker_buy_opens_zero_pnl() -> None:
    t = _CostBasisTracker()
    pnl = t.record("BTCUSDT", "buy", 1.0, 100.0)
    assert pnl == 0.0
    assert t._qty["BTCUSDT"] == 1.0
    assert t._avg["BTCUSDT"] == pytest.approx(100.0)


def test_tracker_sell_profitable() -> None:
    t = _CostBasisTracker()
    t.record("BTCUSDT", "buy", 1.0, 100.0)
    pnl = t.record("BTCUSDT", "sell", 1.0, 110.0)
    assert pnl == pytest.approx(10.0)
    assert t._qty["BTCUSDT"] == 0.0
    assert t._avg["BTCUSDT"] == 0.0


def test_tracker_sell_at_loss() -> None:
    t = _CostBasisTracker()
    t.record("BTCUSDT", "buy", 1.0, 100.0)
    pnl = t.record("BTCUSDT", "sell", 1.0, 90.0)
    assert pnl == pytest.approx(-10.0)


def test_tracker_partial_close() -> None:
    t = _CostBasisTracker()
    t.record("BTCUSDT", "buy", 2.0, 100.0)
    pnl = t.record("BTCUSDT", "sell", 1.0, 110.0)
    assert pnl == pytest.approx(10.0)
    assert t._qty["BTCUSDT"] == pytest.approx(1.0)
    assert t._avg["BTCUSDT"] == pytest.approx(100.0)


def test_tracker_multiple_symbols_independent() -> None:
    t = _CostBasisTracker()
    t.record("BTCUSDT", "buy", 1.0, 100.0)
    t.record("ETHUSDT", "buy", 1.0, 10.0)
    pnl_btc = t.record("BTCUSDT", "sell", 1.0, 110.0)
    pnl_eth = t.record("ETHUSDT", "sell", 1.0, 8.0)
    assert pnl_btc == pytest.approx(10.0)
    assert pnl_eth == pytest.approx(-2.0)


def test_tracker_sell_before_buy_returns_zero() -> None:
    """Sell with no open position returns 0 (cur_avg == 0 guard)."""
    t = _CostBasisTracker()
    pnl = t.record("BTCUSDT", "sell", 1.0, 100.0)
    assert pnl == 0.0
