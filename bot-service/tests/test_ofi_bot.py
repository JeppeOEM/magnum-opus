from __future__ import annotations

import json
import math
import pathlib
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from bot_service.strategy.signals.ofi import compute_ofi_signal
from bot_service.strategy.signals.ofi_signal import ofi_signal


# ── T1: compute_ofi_signal alias ─────────────────────────────────────────────

@pytest.mark.l1
def test_compute_ofi_signal_is_alias() -> None:
    assert compute_ofi_signal is ofi_signal


@pytest.mark.l1
def test_compute_ofi_signal_returns_buy_on_high_z() -> None:
    ofi_vals = [0.0] * 29 + [10.0]
    df = pd.DataFrame({"ofi": ofi_vals, "close": [100.0] * 30})
    result = compute_ofi_signal(df, lookback=30, threshold=0.5)
    assert result.action == "buy"


# ── T2: OFIBot properties ─────────────────────────────────────────────────────

def _make_settings() -> MagicMock:
    s = MagicMock()
    s.questdb_http_addr = "http://localhost:9000"
    s.bot_subscribe_timeout_s = 30
    return s


@pytest.mark.l1
def test_ofi_bot_properties() -> None:
    # Import here so conftest env vars are applied first
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "strategies" / "active"))
    from ofi_bot import OFIBot  # type: ignore[import]
    bot = OFIBot("OFIBot", _make_settings())
    assert bot.min_lookback == 200
    assert bot.max_position_pct == 0.05
    assert bot.stop_loss_pct == 0.02
    assert bot.paper_trading is True
    assert bot.bus_timeout_seconds == 30
    assert bot.close_on_bus_timeout is True


# ── T3: OFIBot _on_bar posts order on signal ─────────────────────────────────

def _make_ofi_df(n: int, last_ofi: float) -> pd.DataFrame:
    ofi_vals = [0.0] * (n - 1) + [last_ofi]
    return pd.DataFrame({"ofi": ofi_vals, "close": [100.0] * n, "has_gap": [False] * n})


@pytest.mark.l1
def test_ofi_bot_posts_order_on_buy_signal() -> None:
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "strategies" / "active"))
    from ofi_bot import OFIBot  # type: ignore[import]

    bot = OFIBot("OFIBot", _make_settings())
    mock_worker = MagicMock()
    bot._order_worker = mock_worker
    bot._exchange = "kucoin"

    # Populate rolling df with 200+ rows, last bar has high OFI z-score
    df = _make_ofi_df(201, last_ofi=50.0)
    bot.register_bar_handler("BTCUSDT", "1s", bot._on_bar)
    bot._dfs[("BTCUSDT", "1s")] = df

    # Fire handler directly with the seeded df
    bot._on_bar(df)

    mock_worker.post.assert_called_once()
    req = mock_worker.post.call_args[0][0]
    assert req.side == "buy"
    assert req.paper_trading is True
    assert req.symbol == "BTCUSDT"


@pytest.mark.l1
def test_ofi_bot_blocks_on_gap_invalid() -> None:
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "strategies" / "active"))
    from ofi_bot import OFIBot  # type: ignore[import]

    bot = OFIBot("OFIBot", _make_settings())
    mock_worker = MagicMock()
    bot._order_worker = mock_worker
    bot._signal_invalid["BTCUSDT"] = True  # gap active

    df = _make_ofi_df(201, last_ofi=50.0)
    bot._on_bar(df)

    mock_worker.post.assert_not_called()


@pytest.mark.l1
def test_ofi_bot_hold_on_low_signal() -> None:
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "strategies" / "active"))
    from ofi_bot import OFIBot  # type: ignore[import]

    bot = OFIBot("OFIBot", _make_settings())
    mock_worker = MagicMock()
    bot._order_worker = mock_worker

    # All OFI values are 0 — zero variance → hold
    df = pd.DataFrame({"ofi": [0.0] * 201, "close": [100.0] * 201, "has_gap": [False] * 201})
    bot._on_bar(df)

    mock_worker.post.assert_not_called()


# ── T4: Result files exist and pass ─────────────────────────────────────────

@pytest.mark.l1
def test_fee_impact_result_exists_and_passes() -> None:
    p = pathlib.Path("_results/OFIBot/fee_impact.json")
    if not p.exists():
        pytest.skip("Result file not generated yet — run story 15.2 T3 script from bot-service/")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["passes"] is True


@pytest.mark.l1
def test_validation_report_exists_and_passes() -> None:
    p = pathlib.Path("_results/OFIBot/validation_report.json")
    if not p.exists():
        pytest.skip("Result file not generated yet — run story 15.2 T3 script from bot-service/")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["passes"] is True
