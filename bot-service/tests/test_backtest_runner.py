from __future__ import annotations

import math
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bot_service.backtest.equity import EquitySampler, collect_equity
from bot_service.backtest.runner import BacktestResult, hash_file, load_strategy_class


# ── hash_file ─────────────────────────────────────────────────────────────────

@pytest.mark.l1
def test_hash_file_returns_16_hex_chars(tmp_path: Path) -> None:
    f = tmp_path / "strat.py"
    f.write_bytes(b"hello world")
    h = hash_file(f)
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)


@pytest.mark.l1
def test_hash_file_deterministic(tmp_path: Path) -> None:
    f = tmp_path / "strat.py"
    f.write_bytes(b"same content")
    assert hash_file(f) == hash_file(f)


@pytest.mark.l1
def test_hash_file_changes_on_content_change(tmp_path: Path) -> None:
    f = tmp_path / "strat.py"
    f.write_bytes(b"version 1")
    h1 = hash_file(f)
    f.write_bytes(b"version 2")
    h2 = hash_file(f)
    assert h1 != h2


# ── load_strategy_class ───────────────────────────────────────────────────────

_VALID_STRATEGY = '''
from __future__ import annotations
import pandas as pd
from bot_service.strategy.base import BaseStrategy

class DummyStrategy(BaseStrategy):
    @property
    def min_lookback(self) -> int: return 1
    @property
    def max_position_pct(self) -> float: return 0.05
    @property
    def stop_loss_pct(self) -> float: return 0.01
    @property
    def paper_trading(self) -> bool: return True
    @property
    def bus_timeout_seconds(self) -> int: return 30
    @property
    def close_on_bus_timeout(self) -> bool: return False
    def subscribe(self) -> None: pass
'''

_NO_STRATEGY = "x = 1\n"

_TWO_STRATEGIES = _VALID_STRATEGY + '''
class AnotherStrategy(DummyStrategy):
    pass
'''


@pytest.mark.l1
def test_load_strategy_class_returns_class(tmp_path: Path) -> None:
    f = tmp_path / "dummy_strategy.py"
    f.write_text(_VALID_STRATEGY)
    cls = load_strategy_class(f)
    assert cls.__name__ == "DummyStrategy"


@pytest.mark.l1
def test_load_strategy_class_raises_on_no_class(tmp_path: Path) -> None:
    f = tmp_path / "empty.py"
    f.write_text(_NO_STRATEGY)
    with pytest.raises(ValueError, match="No strategy class found"):
        load_strategy_class(f)


@pytest.mark.l1
def test_load_strategy_class_raises_on_multiple_classes(tmp_path: Path) -> None:
    f = tmp_path / "two.py"
    f.write_text(_TWO_STRATEGIES)
    with pytest.raises(ValueError, match="Multiple"):
        load_strategy_class(f)


# ── EquitySampler ─────────────────────────────────────────────────────────────

@pytest.mark.l1
def test_equity_sampler_records_every_n_bars() -> None:
    import backtrader as bt

    class _MinimalStrategy(bt.Strategy):
        def next(self) -> None:
            pass

    cerebro = bt.Cerebro()
    prices = [100.0 + i * 0.1 for i in range(200)]
    import pandas as pd
    from datetime import datetime, timedelta

    base = datetime(2026, 1, 1)
    idx = [base + timedelta(seconds=i) for i in range(200)]
    df = pd.DataFrame({
        "open": prices, "high": prices, "low": prices,
        "close": prices, "volume": [1.0] * 200,
    }, index=pd.DatetimeIndex(idx))

    feed = bt.feeds.PandasData(dataname=df)
    cerebro.adddata(feed)
    cerebro.addstrategy(_MinimalStrategy)
    cerebro.broker.setcash(10000.0)
    cerebro.addobserver(EquitySampler, sample_every=10)
    results = cerebro.run()

    strat = results[0]
    obs_values = strat.observers.equitysampler.lines.portfolio_value.array
    non_nan = [v for v in obs_values if not math.isnan(v)]
    # 200 bars / every 10 = 20 samples (bars 10, 20, ..., 200)
    assert len(non_nan) == 20


@pytest.mark.l1
def test_collect_equity_skips_nan() -> None:
    import backtrader as bt

    class _MinimalStrategy(bt.Strategy):
        def next(self) -> None:
            pass

    cerebro = bt.Cerebro()
    prices = [100.0] * 60
    import pandas as pd
    from datetime import datetime, timedelta

    base = datetime(2026, 1, 1)
    idx = [base + timedelta(seconds=i) for i in range(60)]
    df = pd.DataFrame({
        "open": prices, "high": prices, "low": prices,
        "close": prices, "volume": [1.0] * 60,
    }, index=pd.DatetimeIndex(idx))

    feed = bt.feeds.PandasData(dataname=df)
    cerebro.adddata(feed)
    cerebro.addstrategy(_MinimalStrategy)
    cerebro.broker.setcash(5000.0)
    cerebro.addobserver(EquitySampler, sample_every=30)
    results = cerebro.run()

    equity = collect_equity(results[0], sample_every=30)
    # 60 bars / every 30 = 2 samples
    assert len(equity) == 2
    for ts_str, val in equity:
        assert isinstance(ts_str, str)
        assert val == pytest.approx(5000.0)
