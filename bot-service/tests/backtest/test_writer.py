from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bot_service.backtest.writer import BacktestResultWriter, _CostBasisTracker


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


# ---------------------------------------------------------------------------
# Story 29-1: BacktestResultWriter TCP connection reuse
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_writer_context_manager_opens_and_closes_sender() -> None:
    """open() creates exactly one Sender; close() calls flush+close."""
    mock_sender = MagicMock()
    with patch("bot_service.backtest.writer.Sender") as mock_sender_cls:
        mock_sender_cls.from_conf.return_value = mock_sender

        writer = BacktestResultWriter("localhost:9009", "Test", "bybit", "BTCUSDT")
        with writer:
            assert mock_sender_cls.from_conf.call_count == 1
            assert writer._sender is mock_sender

        # After exit: close() called flush + close
        mock_sender.flush.assert_called_once()
        mock_sender.close.assert_called_once()
        assert writer._sender is None


@pytest.mark.l1
def test_writer_open_is_idempotent() -> None:
    """Calling open() twice does not create a second Sender."""
    mock_sender = MagicMock()
    with patch("bot_service.backtest.writer.Sender") as mock_sender_cls:
        mock_sender_cls.from_conf.return_value = mock_sender

        writer = BacktestResultWriter("localhost:9009", "Test", "bybit", "BTCUSDT")
        writer.open()
        writer.open()  # second call is no-op
        assert mock_sender_cls.from_conf.call_count == 1


@pytest.mark.l1
def test_writer_close_is_safe_when_not_open() -> None:
    """close() on an unopened writer does not raise."""
    writer = BacktestResultWriter("localhost:9009", "Test", "bybit", "BTCUSDT")
    writer.close()  # must not raise


@pytest.mark.l1
def test_writer_reuses_sender_across_multiple_writes() -> None:
    """_sync_ilp_write called 3 times uses the same sender (not 3 separate connections)."""
    mock_sender = MagicMock()
    with patch("bot_service.backtest.writer.Sender") as mock_sender_cls:
        mock_sender_cls.from_conf.return_value = mock_sender

        writer = BacktestResultWriter("localhost:9009", "Test", "bybit", "BTCUSDT")
        with writer:
            for _ in range(3):
                writer._sync_ilp_write({
                    "order_id": "x", "client_order_id": "y", "strategy": "Test",
                    "exchange": "bybit", "symbol": "BTCUSDT", "market_type": "spot",
                    "side": "buy", "order_type": "market", "status": "filled",
                    "fee_currency": "USDT", "signal_type": "",
                    "limit_price": 0.0, "stop_price": 0.0, "take_profit_price": 0.0,
                    "requested_size": 1.0, "filled_size": 1.0, "remaining_size": 0.0,
                    "avg_fill_price": 50000.0, "fee": 0.0, "realized_pnl": 0.0,
                    "slippage": 0.0, "position_size_after": 1.0,
                    "paper_trading": False, "backtest": True,
                    "ts_placed": 0, "ts_exchange": 0,
                })

        # Only one Sender.from_conf call regardless of 3 writes
        assert mock_sender_cls.from_conf.call_count == 1
        # sender.row called 3 times
        assert mock_sender.row.call_count == 3


@pytest.mark.l1
def test_writer_sync_ilp_write_without_open_logs_critical() -> None:
    """_sync_ilp_write without open() logs CRITICAL and does not raise."""
    writer = BacktestResultWriter("localhost:9009", "Test", "bybit", "BTCUSDT")
    # sender is None — must not raise
    writer._sync_ilp_write({"strategy": "Test", "ts_exchange": 0})
