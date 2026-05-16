from __future__ import annotations

import signal
import time
from unittest.mock import MagicMock

import pytest

from bot_service.bus.event_types import BarClose, GapMarker
from bot_service.strategy.base import BaseStrategy, SubscribeTimeoutError


class _StubStrategy(BaseStrategy):
    @property
    def min_lookback(self) -> int:
        return 3

    @property
    def max_position_pct(self) -> float:
        return 0.1

    @property
    def stop_loss_pct(self) -> float:
        return 0.02

    @property
    def paper_trading(self) -> bool:
        return True

    @property
    def bus_timeout_seconds(self) -> int:
        return 300

    @property
    def close_on_bus_timeout(self) -> bool:
        return False

    def subscribe(self) -> None:
        pass


def _make_strategy(name: str = "test") -> _StubStrategy:
    # conftest.py sets all required credential env vars; Settings() is valid here.
    from bot_service.config import Settings
    return _StubStrategy(name, Settings())


def _bar(symbol: str, ts: int = 1000, tf: str = "1s") -> BarClose:
    return BarClose(
        exchange="kucoin",
        symbol=symbol,
        tf=tf,
        ts=ts,
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=1.0,
        quote_volume=1.0,
        trade_count=1,
        is_complete=True,
    )


def _gap(symbol: str, ts: int = 2000) -> GapMarker:
    return GapMarker(exchange="", symbol=symbol, gap_cause="external_disconnect", ts=ts)


# ---- NaN guard ----

def test_nan_guard_intercepts_nan_dataframe(monkeypatch: pytest.MonkeyPatch) -> None:
    guards: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "bot_service.strategy.base.inc_nan_guard",
        lambda s, sym: guards.append((s, sym)),
    )
    calls: list[int] = []
    s = _make_strategy("strat_nan")
    s.register_bar_handler("BTC", "1s", lambda df: calls.append(len(df)))

    for ts in [1000, 2000, 3000]:  # 3 rows — past lookback gate
        s.on_bar(_bar("BTC", ts=ts))
    assert calls == [3]  # handler called once (on 3rd bar), with df of length 3

    # Inject NaN into the DF
    df = s._dfs[("BTC", "1s")]
    df.iloc[-1, df.columns.get_loc("close")] = float("nan")

    # Invoke wrapped handler directly on the poisoned DF
    calls.clear()
    s._bar_handlers[("BTC", "1s")](s._dfs[("BTC", "1s")])

    assert calls == []
    assert guards == [("strat_nan", "BTC")]


# ---- Lookback gate ----

def test_lookback_gate_suppresses_handler_before_min_lookback() -> None:
    calls: list[int] = []
    s = _make_strategy()
    s.register_bar_handler("BTC", "1s", lambda df: calls.append(len(df)))

    s.on_bar(_bar("BTC", ts=1000))  # 1 row
    s.on_bar(_bar("BTC", ts=2000))  # 2 rows
    assert calls == []
    s.on_bar(_bar("BTC", ts=3000))  # 3 rows — gate opens
    assert calls == [3]


# ---- GapMarker invalidation ----

def test_gapmarker_sets_signal_invalid_and_marks_row() -> None:
    s = _make_strategy()
    s.on_bar(_bar("BTC", ts=1000))
    s.handle_gap(_gap("BTC", ts=2000))

    assert s._signal_invalid["BTC"] is True
    assert bool(s._dfs[("BTC", "1s")].iloc[-1]["has_gap"]) is True


def test_gapmarker_marks_row_in_all_tracked_timeframes() -> None:
    s = _make_strategy()
    s.on_bar(_bar("BTC", ts=1000, tf="1s"))
    s.on_bar(_bar("BTC", ts=60000, tf="1m"))
    s.handle_gap(_gap("BTC", ts=2000))

    assert bool(s._dfs[("BTC", "1s")].iloc[-1]["has_gap"]) is True
    assert bool(s._dfs[("BTC", "1m")].iloc[-1]["has_gap"]) is True


# ---- Clean-bar recovery ----

def test_clean_bars_restore_signal_at_min_lookback() -> None:
    s = _make_strategy()
    s.on_bar(_bar("BTC", ts=1000))
    s.handle_gap(_gap("BTC"))
    assert s._signal_invalid["BTC"] is True

    s.on_bar(_bar("BTC", ts=3000))  # 1 clean bar
    s.on_bar(_bar("BTC", ts=4000))  # 2 clean bars (N-1)
    assert s._signal_invalid["BTC"] is True

    s.on_bar(_bar("BTC", ts=5000))  # 3rd — equals min_lookback → cleared
    assert s._signal_invalid.get("BTC") is False


def test_new_gap_resets_clean_bar_count() -> None:
    s = _make_strategy()
    s.on_bar(_bar("BTC", ts=1000))
    s.handle_gap(_gap("BTC", ts=2000))

    s.on_bar(_bar("BTC", ts=3000))
    s.on_bar(_bar("BTC", ts=4000))  # 2 clean bars

    # Second gap resets count
    s.handle_gap(_gap("BTC", ts=5000))
    assert s._clean_bar_count["BTC"] == 0
    assert s._signal_invalid["BTC"] is True

    # Must accumulate 3 more clean bars from scratch
    for ts in [6000, 7000, 8000]:
        s.on_bar(_bar("BTC", ts=ts))
    assert s._signal_invalid.get("BTC") is False


# ── TF → table routing ───────────────────────────────────────────────────────

@pytest.mark.parametrize("tf,expected_table", [
    ("1s", "snapshot_1s"),
    ("1m", "snapshot_1m"),
    ("5m", "snapshot_1m"),
    ("15m", "snapshot_15m"),
    ("4h", "snapshot_15m"),
    ("unknown_tf", "snapshot_1s"),
])
@pytest.mark.l1
def test_tf_routes_to_correct_table(
    tf: str, expected_table: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    queries: list[str] = []

    def _mock_get(url: str, *, params: dict, timeout: float) -> MagicMock:
        queries.append(params["query"])
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"dataset": [], "columns": []}
        return resp

    monkeypatch.setattr("bot_service.strategy.base.httpx.get", _mock_get)
    s = _make_strategy()
    s._query_questdb("BTCUSDT", tf, 10)
    assert f"FROM {expected_table}" in queries[0]
    assert f"tf='{tf}'" in queries[0]


# ── max_position_pct validation ───────────────────────────────────────────────

@pytest.mark.l1
def test_max_position_pct_above_1_raises() -> None:
    class _OverAllocStrategy(_StubStrategy):
        @property
        def max_position_pct(self) -> float:
            return 1.01

    from bot_service.config import Settings
    with pytest.raises(ValueError, match="max_position_pct"):
        _OverAllocStrategy("over", Settings())


@pytest.mark.l1
def test_max_position_pct_zero_raises() -> None:
    class _ZeroStrategy(_StubStrategy):
        @property
        def max_position_pct(self) -> float:
            return 0.0

    from bot_service.config import Settings
    with pytest.raises(ValueError, match="max_position_pct"):
        _ZeroStrategy("zero", Settings())


# ---- subscribe() timeout ----

def test_run_subscribe_raises_on_slow_subscribe() -> None:
    class SlowStrategy(_StubStrategy):
        def subscribe(self) -> None:
            time.sleep(5)

    from bot_service.config import Settings
    s = SlowStrategy("slow", Settings())
    with pytest.raises(SubscribeTimeoutError):
        s.run_subscribe(timeout_s=0.1)


# ---- Heartbeat ----

@pytest.mark.l2
def test_heartbeat_sends_sigterm_on_frozen_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "bot_service.strategy.base.os.kill",
        lambda pid, sig: killed.append((pid, sig)),
    )

    s = _make_strategy("hb_test")
    s.start_heartbeat()
    # Never call ack_heartbeat() — simulates a frozen event loop

    # Heartbeat: sleep 5s + wait 10s = fires at T+15s
    deadline = time.monotonic() + 16.0
    while not killed and time.monotonic() < deadline:
        time.sleep(0.1)

    assert killed, "expected os.kill to be called within 16s"
    assert killed[0][1] == signal.SIGTERM
