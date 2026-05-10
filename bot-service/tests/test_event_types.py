from __future__ import annotations

import pytest
import structlog.testing

from bot_service.bus.event_types import (
    BarClose,
    FundingRate,
    GapMarker,
    MultiBarClose,
    OBSnapshot,
    OrderFilled,
    OrderRejected,
    Tick,
    parse_stream_entry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BAR_CLOSE_ENTRY: dict[str, str] = {
    "ts": "1715000000000",
    "open": "62000.5",
    "high": "62500.0",
    "low": "61800.0",
    "close": "62300.0",
    "volume": "10.5",
    "quote_volume": "654750.0",
    "trade_count": "200",
    "is_complete": "true",
}


# ---------------------------------------------------------------------------
# BarClose — candles:close:* stream key
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_bar_close_from_candles_close_stream() -> None:
    """Round-trip: candles:close:exchange:symbol:tf → BarClose with all fields."""
    result = parse_stream_entry("candles:close:kucoin:BTCUSDT:1m", _BAR_CLOSE_ENTRY)

    assert isinstance(result, BarClose)
    assert result.exchange == "kucoin"
    assert result.symbol == "BTCUSDT"
    assert result.tf == "1m"
    assert result.ts == 1715000000000
    assert result.open == 62000.5
    assert result.high == 62500.0
    assert result.low == 61800.0
    assert result.close == 62300.0
    assert result.volume == 10.5
    assert result.quote_volume == 654750.0
    assert result.trade_count == 200
    assert result.is_complete is True


@pytest.mark.l1
def test_bar_close_from_candles_partial_stream() -> None:
    """Round-trip: candles:exchange:symbol:tf → BarClose (partial bar stream)."""
    entry = {**_BAR_CLOSE_ENTRY, "is_complete": "false"}
    result = parse_stream_entry("candles:kucoin:BTCUSDT:1s", entry)

    assert isinstance(result, BarClose)
    assert result.exchange == "kucoin"
    assert result.symbol == "BTCUSDT"
    assert result.tf == "1s"
    assert result.is_complete is False


# ---------------------------------------------------------------------------
# GapMarker — ticks:* stream key with type=gap
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_gap_marker_from_tick_stream() -> None:
    """Round-trip: ticks:exchange:symbol entry with type=gap → GapMarker."""
    entry: dict[str, str] = {
        "type": "gap",
        "gap_cause": "external_disconnect",
        "ts": "1715000001000",
    }
    result = parse_stream_entry("ticks:bybit:ETHUSDT", entry)

    assert isinstance(result, GapMarker)
    assert result.exchange == "bybit"
    assert result.symbol == "ETHUSDT"
    assert result.gap_cause == "external_disconnect"
    assert result.ts == 1715000001000


# ---------------------------------------------------------------------------
# OBSnapshot — ob_features:* stream key
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_ob_snapshot_from_ob_features_stream() -> None:
    """Round-trip: ob_features:exchange:symbol entry → OBSnapshot."""
    entry: dict[str, str] = {
        "ts": "1715000002000",
        "best_bid": "62290.0",
        "best_ask": "62310.0",
        "mid_price": "62300.0",
    }
    result = parse_stream_entry("ob_features:kucoin:BTCUSDT", entry)

    assert isinstance(result, OBSnapshot)
    assert result.exchange == "kucoin"
    assert result.symbol == "BTCUSDT"
    assert result.ts == 1715000002000
    assert result.best_bid == 62290.0
    assert result.best_ask == 62310.0
    assert result.mid_price == 62300.0


# ---------------------------------------------------------------------------
# Tick — ticks:* stream key with type=tick
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_tick_from_tick_stream() -> None:
    """Round-trip: ticks:exchange:symbol entry with type=tick → Tick."""
    entry: dict[str, str] = {
        "type": "tick",
        "ts": "1715000003000",
        "price": "62301.5",
        "size": "0.25",
        "side": "buy",
    }
    result = parse_stream_entry("ticks:kucoin:BTCUSDT", entry)

    assert isinstance(result, Tick)
    assert result.exchange == "kucoin"
    assert result.symbol == "BTCUSDT"
    assert result.ts == 1715000003000
    assert result.price == 62301.5
    assert result.size == 0.25
    assert result.side == "buy"


# ---------------------------------------------------------------------------
# FundingRate — funding:* stream key
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_funding_rate_from_funding_stream() -> None:
    """Round-trip: funding:exchange:symbol entry → FundingRate."""
    entry: dict[str, str] = {
        "ts": "1715000004000",
        "funding_rate": "0.0001",
        "next_funding_ts": "1715028800000",
    }
    result = parse_stream_entry("funding:bybit:BTCUSDT", entry)

    assert isinstance(result, FundingRate)
    assert result.exchange == "bybit"
    assert result.symbol == "BTCUSDT"
    assert result.ts == 1715000004000
    assert result.funding_rate == 0.0001
    assert result.next_funding_ts == 1715028800000


# ---------------------------------------------------------------------------
# OrderFilled — orders:filled:* stream key
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_order_filled_and_rejected_are_constructable_not_parsed_from_redis() -> None:
    """OrderFilled/OrderRejected arrive via private WebSocket, not Redis streams.
    Verify they are constructable directly and that parse_stream_entry treats
    orders:* keys as unknown (returns None, no log)."""
    filled = OrderFilled(
        order_id="ord-abc-123",
        exchange="kucoin",
        symbol="BTCUSDT",
        side="buy",
        fill_price=62305.0,
        fill_size=0.1,
        fee=3.12,
        ts_exchange=1715000005000,
    )
    assert filled.order_id == "ord-abc-123"
    assert isinstance(filled, OrderFilled)

    rejected = OrderRejected(
        order_id="ord-xyz-456",
        exchange="bybit",
        symbol="ETHUSDT",
        reason="insufficient_margin",
        ts_exchange=1715000006000,
    )
    assert rejected.reason == "insufficient_margin"

    # orders:* keys are unknown to parse_stream_entry — fills arrive via WebSocket
    assert parse_stream_entry("orders:filled:kucoin:BTCUSDT", {}) is None
    assert parse_stream_entry("orders:rejected:bybit:ETHUSDT", {}) is None


# ---------------------------------------------------------------------------
# MultiBarClose — importable but NOT produced by parse_stream_entry
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_multi_bar_close_is_importable_and_constructable() -> None:
    """MultiBarClose is importable and constructable; it is NOT a BusEvent."""
    bar = BarClose(
        exchange="kucoin",
        symbol="BTCUSDT",
        tf="1m",
        ts=1715000000000,
        open=62000.0,
        high=62500.0,
        low=61800.0,
        close=62300.0,
        volume=10.0,
        quote_volume=650000.0,
        trade_count=100,
        is_complete=True,
    )
    mbc = MultiBarClose(tf="1m", ts=1715000000000, closes={"BTCUSDT": bar})
    assert mbc.tf == "1m"
    assert mbc.closes["BTCUSDT"] is bar


# ---------------------------------------------------------------------------
# Unknown stream key → None, no WARN logged
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_unknown_stream_key_returns_none_no_log() -> None:
    """An unknown stream key returns None without emitting any log entries."""
    with structlog.testing.capture_logs() as captured:
        result = parse_stream_entry("unknown:kucoin:BTCUSDT", {"ts": "123"})

    assert result is None
    assert captured == [], f"Expected no log entries, got: {captured}"


# ---------------------------------------------------------------------------
# Missing required field → None + WARN logged
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_missing_field_returns_none_and_logs_warn() -> None:
    """A known stream key with a missing required field returns None and logs WARN."""
    # Omit "ts" from the entry — required for BarClose
    incomplete_entry: dict[str, str] = {
        "open": "62000.5",
        "high": "62500.0",
        "low": "61800.0",
        "close": "62300.0",
        "volume": "10.5",
        "quote_volume": "654750.0",
        "trade_count": "200",
        "is_complete": "true",
        # "ts" deliberately omitted
    }
    with structlog.testing.capture_logs() as captured:
        result = parse_stream_entry("candles:close:kucoin:BTCUSDT:1m", incomplete_entry)

    assert result is None
    assert len(captured) == 1
    warn = captured[0]
    assert warn["log_level"] == "warning"
    assert warn["event"] == "parse_stream_entry_missing_field"
    assert warn["stream_key"] == "candles:close:kucoin:BTCUSDT:1m"
    assert "error" in warn


@pytest.mark.l1
def test_missing_type_field_in_tick_stream_logs_warn() -> None:
    """A ticks:* entry missing the 'type' field returns None and logs WARN."""
    entry: dict[str, str] = {
        "ts": "1715000003000",
        "price": "62301.5",
        "size": "0.25",
        "side": "buy",
        # "type" deliberately omitted
    }
    with structlog.testing.capture_logs() as captured:
        result = parse_stream_entry("ticks:kucoin:BTCUSDT", entry)

    assert result is None
    assert len(captured) == 1
    warn = captured[0]
    assert warn["log_level"] == "warning"
    assert warn["stream_key"] == "ticks:kucoin:BTCUSDT"
    assert "error" in warn
