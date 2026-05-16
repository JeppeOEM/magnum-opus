from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock

import pandas as pd
import pytest

from bot_service.strategy.signals.ma_cross import compute_ma_cross_signal, ma_cross_signal


# ── T1: compute_ma_cross_signal alias ────────────────────────────────────────

@pytest.mark.l1
def test_compute_ma_cross_signal_is_alias() -> None:
    assert compute_ma_cross_signal is ma_cross_signal


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_settings() -> MagicMock:
    s = MagicMock()
    s.questdb_http_addr = "http://localhost:9000"
    s.bot_subscribe_timeout_s = 30
    return s


def _import_ma_cross_bot() -> type:
    from ma_cross_bot import MACrossBot  # type: ignore[import]
    return MACrossBot


def _make_golden_cross_df() -> pd.DataFrame:
    # 50 rows at low price warm up EMAs; final row at high price triggers
    # fast EMA to jump above slow EMA at the last bar
    prices = [90.0] * 50 + [300.0]
    return pd.DataFrame({"close": prices})


def _make_death_cross_df() -> pd.DataFrame:
    # 50 rows at high price; final row at very low price triggers death cross
    prices = [300.0] * 50 + [10.0]
    return pd.DataFrame({"close": prices})


# ── T2: MACrossBot properties ─────────────────────────────────────────────────

@pytest.mark.l1
def test_ma_cross_bot_properties() -> None:
    MACrossBot = _import_ma_cross_bot()
    bot = MACrossBot("MACrossBot", _make_settings())
    assert bot.min_lookback == 50
    assert bot.max_position_pct == 0.05
    assert bot.stop_loss_pct == 0.03
    assert bot.paper_trading is True
    assert bot.bus_timeout_seconds == 300
    assert bot.close_on_bus_timeout is False


# ── T3: MACrossBot _on_bar posts order ───────────────────────────────────────

@pytest.mark.l1
def test_ma_cross_bot_posts_order_on_buy_signal() -> None:
    MACrossBot = _import_ma_cross_bot()
    bot = MACrossBot("MACrossBot", _make_settings())
    mock_worker = MagicMock()
    bot._order_worker = mock_worker
    bot._exchange = "kucoin"

    df = _make_golden_cross_df()
    bot._on_bar(df)

    mock_worker.post.assert_called_once()
    req = mock_worker.post.call_args[0][0]
    assert req.side == "buy"
    assert req.paper_trading is True
    assert req.symbol == "BTCUSDT"


@pytest.mark.l1
def test_ma_cross_bot_posts_order_on_sell_signal() -> None:
    MACrossBot = _import_ma_cross_bot()
    bot = MACrossBot("MACrossBot", _make_settings())
    mock_worker = MagicMock()
    bot._order_worker = mock_worker
    bot._exchange = "kucoin"

    df = _make_death_cross_df()
    bot._on_bar(df)

    mock_worker.post.assert_called_once()
    req = mock_worker.post.call_args[0][0]
    assert req.side == "sell"
    assert req.paper_trading is True


@pytest.mark.l1
def test_ma_cross_bot_blocks_on_gap() -> None:
    MACrossBot = _import_ma_cross_bot()
    bot = MACrossBot("MACrossBot", _make_settings())
    mock_worker = MagicMock()
    bot._order_worker = mock_worker
    bot._signal_invalid["BTCUSDT"] = True

    df = _make_golden_cross_df()
    bot._on_bar(df)

    mock_worker.post.assert_not_called()


@pytest.mark.l1
def test_ma_cross_bot_hold_on_no_cross() -> None:
    MACrossBot = _import_ma_cross_bot()
    bot = MACrossBot("MACrossBot", _make_settings())
    mock_worker = MagicMock()
    bot._order_worker = mock_worker

    # Steady trend: fast and slow EMAs track together, no crossover at last two bars
    prices = [float(i) for i in range(60)]
    df = pd.DataFrame({"close": prices})
    bot._on_bar(df)

    mock_worker.post.assert_not_called()


@pytest.mark.l1
def test_ma_cross_bot_drops_order_when_worker_none() -> None:
    MACrossBot = _import_ma_cross_bot()
    bot = MACrossBot("MACrossBot", _make_settings())
    bot._order_worker = None  # not injected

    df = _make_golden_cross_df()
    bot._on_bar(df)
    # Should log a warning and not raise


# ── T4: Position flip logic ───────────────────────────────────────────────────

@pytest.mark.l1
def test_ma_cross_bot_hold_when_already_long() -> None:
    MACrossBot = _import_ma_cross_bot()
    bot = MACrossBot("MACrossBot", _make_settings())
    mock_worker = MagicMock()
    bot._order_worker = mock_worker
    bot._exchange = "kucoin"
    bot._current_side = "long"  # already long

    df = _make_golden_cross_df()  # buy signal
    bot._on_bar(df)

    mock_worker.post.assert_not_called()


@pytest.mark.l1
def test_ma_cross_bot_hold_when_already_short() -> None:
    MACrossBot = _import_ma_cross_bot()
    bot = MACrossBot("MACrossBot", _make_settings())
    mock_worker = MagicMock()
    bot._order_worker = mock_worker
    bot._exchange = "kucoin"
    bot._current_side = "short"  # already short

    df = _make_death_cross_df()  # sell signal
    bot._on_bar(df)

    mock_worker.post.assert_not_called()


@pytest.mark.l1
def test_ma_cross_bot_flip_long_to_short_posts_exit_then_entry() -> None:
    MACrossBot = _import_ma_cross_bot()
    bot = MACrossBot("MACrossBot", _make_settings())
    mock_worker = MagicMock()
    bot._order_worker = mock_worker
    bot._exchange = "kucoin"
    bot._current_side = "long"

    df = _make_death_cross_df()  # sell signal → should flip
    bot._on_bar(df)

    assert mock_worker.post.call_count == 2
    exit_req = mock_worker.post.call_args_list[0][0][0]
    entry_req = mock_worker.post.call_args_list[1][0][0]
    assert exit_req.side == "sell"
    assert exit_req.order_role == "exit"
    assert entry_req.side == "sell"
    assert entry_req.order_role == "entry"
    assert bot._current_side == "short"


@pytest.mark.l1
def test_ma_cross_bot_flip_short_to_long_posts_exit_then_entry() -> None:
    MACrossBot = _import_ma_cross_bot()
    bot = MACrossBot("MACrossBot", _make_settings())
    mock_worker = MagicMock()
    bot._order_worker = mock_worker
    bot._exchange = "kucoin"
    bot._current_side = "short"

    df = _make_golden_cross_df()  # buy signal → should flip
    bot._on_bar(df)

    assert mock_worker.post.call_count == 2
    exit_req = mock_worker.post.call_args_list[0][0][0]
    entry_req = mock_worker.post.call_args_list[1][0][0]
    assert exit_req.side == "buy"
    assert exit_req.order_role == "exit"
    assert entry_req.side == "buy"
    assert entry_req.order_role == "entry"
    assert bot._current_side == "long"


@pytest.mark.l1
def test_ma_cross_bot_gap_resets_current_side() -> None:
    from bot_service.bus.event_types import GapMarker
    MACrossBot = _import_ma_cross_bot()
    bot = MACrossBot("MACrossBot", _make_settings())
    bot._current_side = "long"

    gap = GapMarker(exchange="kucoin", symbol="BTCUSDT", gap_cause="external_disconnect", ts=0)
    bot.handle_gap(gap)

    assert bot._current_side is None
    assert bot._signal_invalid.get("BTCUSDT") is True


# ── T5: Result files ──────────────────────────────────────────────────────────

@pytest.mark.l1
def test_fee_impact_result_exists_and_passes() -> None:
    p = pathlib.Path("_results/MACrossBot/fee_impact.json")
    if not p.exists():
        pytest.skip("Result file not generated yet — run story 15.3 T3 script from bot-service/")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["passes"] is True


@pytest.mark.l1
def test_validation_report_exists_and_passes() -> None:
    p = pathlib.Path("_results/MACrossBot/validation_report.json")
    if not p.exists():
        pytest.skip("Result file not generated yet — run story 15.3 T3 script from bot-service/")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["passes"] is True
