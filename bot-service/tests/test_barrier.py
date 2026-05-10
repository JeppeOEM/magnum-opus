from __future__ import annotations

import pytest

from bot_service.barrier import Barrier
from bot_service.bus.event_types import BarClose, GapMarker, MultiBarClose


def _bar(symbol: str, ts: int, tf: str = "1s") -> BarClose:
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


def test_all_symbols_arrive_immediately() -> None:
    b = Barrier(symbols=frozenset({"BTC", "ETH"}), tf="1s", timeout_ms=250)
    now = 1_000_000

    # floor-ts: 1000 // 1000 * 1000 = 1000
    r1 = b.feed(_bar("BTC", 1000), now_ms=now)
    assert r1 == []  # still waiting for ETH

    r2 = b.feed(_bar("ETH", 1001), now_ms=now)  # same floor bucket (1001 // 1000 == 1)
    assert len(r2) == 1
    assert isinstance(r2[0], MultiBarClose)
    assert set(r2[0].closes.keys()) == {"BTC", "ETH"}
    assert r2[0].ts == 1000  # floor-aligned


def test_timeout_fires_partial_multibarclose_and_gapmarker(monkeypatch: pytest.MonkeyPatch) -> None:
    timeouts: list[str] = []
    monkeypatch.setattr("bot_service.barrier.inc_barrier_timeout",
                        lambda tf: timeouts.append(tf))

    b = Barrier(symbols=frozenset({"BTC", "ETH"}), tf="1s", timeout_ms=250)
    t0 = 2_000_000

    r1 = b.feed(_bar("BTC", 2000), now_ms=t0)
    assert r1 == []

    # Advance clock past timeout; next feed() flushes expired bucket first
    r2 = b.feed(_bar("BTC", 3000), now_ms=t0 + 300)  # +300ms > 250ms timeout
    # r2 = [MultiBarClose(ts=2000), GapMarker(ETH)] from flush; new BTC@3000 is buffered
    assert len(r2) == 2
    assert isinstance(r2[0], MultiBarClose)
    assert r2[0].ts == 2000
    assert "BTC" in r2[0].closes
    assert "ETH" not in r2[0].closes
    assert isinstance(r2[1], GapMarker)
    assert r2[1].symbol == "ETH"
    assert r2[1].gap_cause == "barrier_timeout"
    assert timeouts == ["1s"]


def test_late_event_produces_no_new_output_and_increments_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    late_events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "bot_service.barrier.inc_barrier_late_event",
        lambda s, sym: late_events.append((s, sym)),
    )

    b = Barrier(symbols=frozenset({"BTC"}), tf="1s", timeout_ms=100, strategy="strat_a")
    t0 = 5_000_000

    # Fire the bucket immediately (single symbol → complete on arrival)
    b.feed(_bar("BTC", 5000), now_ms=t0)

    # Late arrival for the same bucket — no new events, counter incremented
    result = b.feed(_bar("BTC", 5000), now_ms=t0 + 200)
    assert result == []
    assert late_events == [("strat_a", "BTC")]


def test_unknown_tf_raises_valueerror() -> None:
    with pytest.raises(ValueError, match="Unknown timeframe"):
        Barrier(symbols=frozenset({"BTC"}), tf="3m", timeout_ms=500)


def test_empty_symbols_raises_valueerror() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        Barrier(symbols=frozenset(), tf="1s", timeout_ms=250)


def test_floor_ts_alignment_tolerates_clock_skew() -> None:
    b = Barrier(symbols=frozenset({"BTC", "ETH"}), tf="1m", timeout_ms=500)
    now = 10_000_000

    # BTC arrives at 60_001 ms (just after the 1m boundary)
    # ETH arrives at 59_999 ms (just before the 1m boundary) — different raw ts, same bucket
    r1 = b.feed(_bar("BTC", 60_001, tf="1m"), now_ms=now)
    assert r1 == []

    r2 = b.feed(_bar("ETH", 119_999, tf="1m"), now_ms=now)
    # 60_001 // 60_000 * 60_000 = 60_000
    # 119_999 // 60_000 * 60_000 = 60_000  — same bucket
    assert len(r2) == 1
    assert r2[0].ts == 60_000


def test_duplicate_symbol_in_same_bucket_last_write_wins() -> None:
    # Two-symbol barrier so the first BTC feed doesn't fire the bucket.
    # Feed BTC twice; the second should overwrite the first.
    b = Barrier(symbols=frozenset({"BTC", "ETH"}), tf="1s", timeout_ms=250)
    now = 1_000_000

    bar_btc_a = BarClose(
        exchange="kucoin", symbol="BTC", tf="1s", ts=1000,
        open=1.0, high=2.0, low=0.5, close=1.5,
        volume=10.0, quote_volume=15.0, trade_count=5, is_complete=True,
    )
    bar_btc_b = BarClose(
        exchange="bybit", symbol="BTC", tf="1s", ts=1000,
        open=1.0, high=3.0, low=0.4, close=2.0,
        volume=20.0, quote_volume=40.0, trade_count=8, is_complete=True,
    )

    r1 = b.feed(bar_btc_a, now_ms=now)
    assert r1 == []  # ETH still missing

    r2 = b.feed(bar_btc_b, now_ms=now)
    assert r2 == []  # ETH still missing; bar_btc_b overwrote bar_btc_a

    result = b.feed(_bar("ETH", 1000), now_ms=now)
    assert len(result) == 1
    assert isinstance(result[0], MultiBarClose)
    # bar_btc_b overwrote bar_btc_a — last write wins
    assert result[0].closes["BTC"] is bar_btc_b


def test_fired_set_pruned_to_prevent_unbounded_growth() -> None:
    # With timeout_ms=100 and tf="1s", cutoff = bucket_ts - 200.
    # After firing many buckets, _fired should only hold recent entries.
    b = Barrier(symbols=frozenset({"BTC"}), tf="1s", timeout_ms=100)
    now = 0

    for i in range(20):
        ts = i * 1000  # each 1s bucket
        b.feed(_bar("BTC", ts), now_ms=now + i * 1000)

    # _fired should be pruned; entries older than newest - 200ms should be gone
    # The newest bucket fired is 19_000; cutoff = 19_000 - 200 = 18_800
    # So only ts=19_000 should remain (18_000 < 18_800)
    assert all(ts >= 18_800 for ts in b._fired)
