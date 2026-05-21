from __future__ import annotations

import math

import pandas as pd
import pytest

from bot_service.strategy.signals import HOLD_INSUFFICIENT, SignalResult
from bot_service.strategy.signals.funding_rate_arb import funding_rate_arb_signal
from bot_service.strategy.signals.ma_cross import ma_cross_signal
from bot_service.strategy.signals.ofi_signal import ofi_signal
from bot_service.strategy.signals.rsi import rsi_signal


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_df(n: int, close: list[float] | None = None, ofi: list[float] | None = None) -> pd.DataFrame:
    if close is None:
        close = [100.0 + i * 0.01 for i in range(n)]
    if ofi is None:
        ofi = [0.0] * n
    return pd.DataFrame({"close": close[:n], "ofi": ofi[:n]})


def _cross_up_df(fast: int = 20, slow: int = 50) -> pd.DataFrame:
    """Flat price for `slow` bars, then a single spike — forces fast EMA above slow EMA on the last bar."""
    prices = [100.0] * slow + [500.0]
    return _make_df(slow + 1, close=prices)


def _cross_down_df(fast: int = 20, slow: int = 50) -> pd.DataFrame:
    """Flat price for `slow` bars, then a single drop — forces fast EMA below slow EMA on the last bar."""
    prices = [100.0] * slow + [1.0]
    return _make_df(slow + 1, close=prices)


# ── SignalResult tests ────────────────────────────────────────────────────────

@pytest.mark.l1
def test_signal_result_frozen() -> None:
    result = SignalResult(action="buy", confidence=0.8, reason="test")
    with pytest.raises((TypeError, AttributeError)):
        result.action = "sell"  # type: ignore[misc]


@pytest.mark.l1
def test_hold_insufficient_constant() -> None:
    assert HOLD_INSUFFICIENT.action == "hold"
    assert HOLD_INSUFFICIENT.confidence == 0.0
    assert HOLD_INSUFFICIENT.reason == "insufficient_data"


@pytest.mark.l1
def test_signal_result_confidence_out_of_range() -> None:
    with pytest.raises(ValueError):
        SignalResult(action="buy", confidence=1.5, reason="bad")


# ── ma_cross_signal tests ─────────────────────────────────────────────────────

@pytest.mark.l1
def test_ma_cross_insufficient_data() -> None:
    df = _make_df(10)
    result = ma_cross_signal(df, fast=5, slow=50)
    assert result == HOLD_INSUFFICIENT


@pytest.mark.l1
def test_ma_cross_no_close_column() -> None:
    df = pd.DataFrame({"ofi": [1.0] * 60})
    result = ma_cross_signal(df)
    assert result == HOLD_INSUFFICIENT


@pytest.mark.l1
def test_ma_cross_invalid_span() -> None:
    df = _make_df(60)
    assert ma_cross_signal(df, fast=0, slow=50) == HOLD_INSUFFICIENT
    assert ma_cross_signal(df, fast=20, slow=0) == HOLD_INSUFFICIENT


@pytest.mark.l1
def test_ma_cross_nan_rows() -> None:
    df = _make_df(60)
    df["close"] = float("nan")
    result = ma_cross_signal(df, fast=10, slow=50)
    assert result == HOLD_INSUFFICIENT


@pytest.mark.l1
def test_ma_cross_buy_signal() -> None:
    df = _cross_up_df()
    result = ma_cross_signal(df)
    assert result.action == "buy"
    assert result.confidence > 0.0


@pytest.mark.l1
def test_ma_cross_sell_signal() -> None:
    df = _cross_down_df()
    result = ma_cross_signal(df)
    assert result.action == "sell"
    assert result.confidence > 0.0


@pytest.mark.l1
def test_ma_cross_hold_no_cross() -> None:
    prices = [100.0] * 80
    df = _make_df(80, close=prices)
    result = ma_cross_signal(df)
    assert result.action == "hold"


# ── ofi_signal tests ──────────────────────────────────────────────────────────

@pytest.mark.l1
def test_ofi_insufficient_data() -> None:
    df = _make_df(10)
    result = ofi_signal(df, lookback=30)
    assert result == HOLD_INSUFFICIENT


@pytest.mark.l1
def test_ofi_no_ofi_column() -> None:
    df = pd.DataFrame({"close": [100.0] * 60})
    result = ofi_signal(df, lookback=30)
    assert result == HOLD_INSUFFICIENT


@pytest.mark.l1
def test_ofi_trailing_nan_returns_insufficient() -> None:
    ofi_vals = [1.0] * 30 + [float("nan")]
    df = _make_df(31, ofi=ofi_vals)
    result = ofi_signal(df, lookback=30)
    assert result == HOLD_INSUFFICIENT


@pytest.mark.l1
def test_ofi_buy_signal() -> None:
    ofi_vals = [0.0] * 29 + [10.0]
    df = _make_df(30, ofi=ofi_vals)
    result = ofi_signal(df, lookback=30, threshold=0.5)
    assert result.action == "buy"
    assert result.confidence > 0.0


@pytest.mark.l1
def test_ofi_sell_signal() -> None:
    ofi_vals = [0.0] * 29 + [-10.0]
    df = _make_df(30, ofi=ofi_vals)
    result = ofi_signal(df, lookback=30, threshold=0.5)
    assert result.action == "sell"
    assert result.confidence > 0.0


@pytest.mark.l1
def test_ofi_zero_variance() -> None:
    ofi_vals = [5.0] * 30
    df = _make_df(30, ofi=ofi_vals)
    result = ofi_signal(df, lookback=30)
    assert result.action == "hold"
    assert result.reason == "zero_variance"


# ── rsi_signal tests ─────────────────────────────────────────────────────────

def _rsi_df(rsi_values: list[float], period: int = 14) -> pd.DataFrame:
    """Build a DataFrame with a pre-computed RSI column (bypasses pandas-ta)."""
    col = f"RSI_{period}"
    return pd.DataFrame({col: rsi_values})


@pytest.mark.l1
def test_rsi_missing_column() -> None:
    df = pd.DataFrame({"close": [50.0] * 20})
    assert rsi_signal(df) == HOLD_INSUFFICIENT


@pytest.mark.l1
def test_rsi_insufficient_rows() -> None:
    df = _rsi_df([50.0] * 10)
    assert rsi_signal(df, period=14) == HOLD_INSUFFICIENT


@pytest.mark.l1
def test_rsi_all_nan() -> None:
    df = _rsi_df([float("nan")] * 20)
    assert rsi_signal(df) == HOLD_INSUFFICIENT


@pytest.mark.l1
def test_rsi_trailing_nan() -> None:
    values = [50.0] * 14 + [float("nan")]
    df = _rsi_df(values)
    assert rsi_signal(df) == HOLD_INSUFFICIENT


@pytest.mark.l1
def test_rsi_buy_oversold_recovery() -> None:
    # RSI crosses up from 28 → 32 (crosses through 30)
    values = [50.0] * 13 + [28.0, 32.0]
    df = _rsi_df(values)
    result = rsi_signal(df, period=14, oversold=30.0)
    assert result.action == "buy"
    assert result.reason == "rsi_oversold_recovery"
    assert result.confidence > 0.0


@pytest.mark.l1
def test_rsi_sell_overbought_reversal() -> None:
    # RSI crosses down from 72 → 68 (crosses through 70)
    values = [50.0] * 13 + [72.0, 68.0]
    df = _rsi_df(values)
    result = rsi_signal(df, period=14, overbought=70.0)
    assert result.action == "sell"
    assert result.reason == "rsi_overbought_reversal"
    assert result.confidence > 0.0


@pytest.mark.l1
def test_rsi_hold_mid_range() -> None:
    values = [50.0] * 14 + [51.0]
    df = _rsi_df(values)
    result = rsi_signal(df)
    assert result.action == "hold"
    assert result.reason == "rsi_no_cross"


@pytest.mark.l1
def test_rsi_hold_already_oversold_no_cross() -> None:
    # RSI stays below oversold — no crossover yet
    values = [50.0] * 13 + [25.0, 27.0]
    df = _rsi_df(values)
    result = rsi_signal(df, oversold=30.0)
    assert result.action == "hold"


@pytest.mark.l1
def test_rsi_hold_already_overbought_no_cross() -> None:
    # RSI stays above overbought — no crossover yet
    values = [50.0] * 13 + [75.0, 78.0]
    df = _rsi_df(values)
    result = rsi_signal(df, overbought=70.0)
    assert result.action == "hold"


@pytest.mark.l1
def test_rsi_confidence_capped_at_one() -> None:
    # Huge RSI jump — confidence must not exceed 1.0
    values = [50.0] * 13 + [1.0, 95.0]
    df = _rsi_df(values)
    result = rsi_signal(df, oversold=30.0)
    assert result.action == "buy"
    assert result.confidence <= 1.0


# ── funding_rate_arb_signal test ──────────────────────────────────────────────

@pytest.mark.l1
@pytest.mark.l1
def test_funding_rate_arb_high_positive_rate_returns_sell() -> None:
    result = funding_rate_arb_signal(0.002)
    assert result.action == "sell"
    assert result.reason == "high_positive_funding"
