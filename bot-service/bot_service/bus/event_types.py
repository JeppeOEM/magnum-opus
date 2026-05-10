from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

import structlog

log: structlog.stdlib.BoundLogger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Immutable event types
# All fields use Python 3.10+ union syntax (X | None) — no Optional[X].
# All numeric fields are parsed from str at the bus boundary; never arrive
# pre-typed from the Redis stream.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BarClose:
    exchange: str
    symbol: str
    tf: str  # "1s", "1m", "5m", "15m", "1h", "4h", "1d", "1w"
    ts: int  # Unix ms, floor-aligned to tf boundary
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trade_count: int
    is_complete: bool


@dataclass(frozen=True)
class MultiBarClose:
    # Produced by the Barrier (Story 11.5), NOT by parse_stream_entry.
    # Excluded from BusEvent TypeAlias.
    # NOTE: frozen=True makes the `closes` field reference immutable (cannot
    # reassign), but not the dict contents — this is acceptable; the Barrier
    # owns the dict and never mutates it after construction.
    tf: str
    ts: int
    closes: dict[str, BarClose]  # symbol → BarClose; missing symbols absent


@dataclass(frozen=True)
class GapMarker:
    exchange: str
    symbol: str
    gap_cause: str  # "external_disconnect" | "external_rate_limit" | "internal_buffer_overflow" | "internal_merge_error" | "queue_overflow" | "barrier_timeout"
    ts: int  # Unix ms when gap was detected


@dataclass(frozen=True)
class OBSnapshot:
    exchange: str
    symbol: str
    ts: int
    best_bid: float
    best_ask: float
    mid_price: float


@dataclass(frozen=True)
class Tick:
    exchange: str
    symbol: str
    ts: int
    price: float
    size: float
    side: str  # "buy" | "sell"


@dataclass(frozen=True)
class FundingRate:
    exchange: str
    symbol: str
    ts: int
    funding_rate: float
    next_funding_ts: int


@dataclass(frozen=True)
class OrderFilled:
    order_id: str
    exchange: str
    symbol: str
    side: str
    fill_price: float
    fill_size: float
    fee: float
    ts_exchange: int


@dataclass(frozen=True)
class OrderRejected:
    order_id: str
    exchange: str
    symbol: str
    reason: str
    ts_exchange: int


# MultiBarClose is intentionally excluded — it is produced by the Barrier,
# not by the stream parser, and downstream code must not expect to receive it
# from parse_stream_entry.
BusEvent: TypeAlias = (
    BarClose | GapMarker | OBSnapshot | Tick | FundingRate | OrderFilled | OrderRejected
)


# ---------------------------------------------------------------------------
# Private parsers — one per stream key prefix
# ---------------------------------------------------------------------------


def _parse_bar_close(stream_key: str, entry: dict[str, str]) -> BarClose | None:
    """Parse a BarClose from a candles:close:* or candles:* stream entry."""
    try:
        parts = stream_key.split(":")
        # candles:close:{exchange}:{symbol}:{tf}  → len >= 5, parts[1]=="close"
        # candles:{exchange}:{symbol}:{tf}         → len >= 4, parts[1]!="close"
        if parts[1] == "close":
            exchange = parts[2]
            symbol = parts[3]
            tf = parts[4]
        else:
            exchange = parts[1]
            symbol = parts[2]
            tf = parts[3]

        return BarClose(
            exchange=exchange,
            symbol=symbol,
            tf=tf,
            ts=int(entry["ts"]),
            open=float(entry["open"]),
            high=float(entry["high"]),
            low=float(entry["low"]),
            close=float(entry["close"]),
            volume=float(entry["volume"]),
            quote_volume=float(entry["quote_volume"]),
            trade_count=int(entry["trade_count"]),
            is_complete=entry["is_complete"].lower() == "true",
        )
    except (KeyError, ValueError, IndexError) as exc:
        log.warning(
            "parse_stream_entry_missing_field",
            stream_key=stream_key,
            error=str(exc),
        )
        return None


def _parse_tick_or_gap(stream_key: str, entry: dict[str, str]) -> Tick | GapMarker | None:
    """Route a ticks:* entry to Tick or GapMarker based on the 'type' field."""
    try:
        parts = stream_key.split(":")
        exchange = parts[1]
        symbol = parts[2]
        event_type = entry["type"]
    except (KeyError, IndexError) as exc:
        log.warning(
            "parse_stream_entry_missing_field",
            stream_key=stream_key,
            error=str(exc),
        )
        return None

    if event_type == "tick":
        try:
            return Tick(
                exchange=exchange,
                symbol=symbol,
                ts=int(entry["ts"]),
                price=float(entry["price"]),
                size=float(entry["size"]),
                side=entry["side"],
            )
        except (KeyError, ValueError) as exc:
            log.warning(
                "parse_stream_entry_missing_field",
                stream_key=stream_key,
                error=str(exc),
            )
            return None

    if event_type == "gap":
        try:
            return GapMarker(
                exchange=exchange,
                symbol=symbol,
                gap_cause=entry["gap_cause"],
                ts=int(entry["ts"]),
            )
        except (KeyError, ValueError) as exc:
            log.warning(
                "parse_stream_entry_missing_field",
                stream_key=stream_key,
                error=str(exc),
            )
            return None

    # Unknown type sub-field — treat as missing field (WARN)
    log.warning(
        "parse_stream_entry_missing_field",
        stream_key=stream_key,
        error=f"unknown type field: {event_type!r}",
    )
    return None


def _parse_ob_snapshot(stream_key: str, entry: dict[str, str]) -> OBSnapshot | None:
    """Parse an OBSnapshot from an ob_features:* stream entry."""
    try:
        parts = stream_key.split(":")
        exchange = parts[1]
        symbol = parts[2]
        return OBSnapshot(
            exchange=exchange,
            symbol=symbol,
            ts=int(entry["ts"]),
            best_bid=float(entry["best_bid"]),
            best_ask=float(entry["best_ask"]),
            mid_price=float(entry["mid_price"]),
        )
    except (KeyError, ValueError, IndexError) as exc:
        log.warning(
            "parse_stream_entry_missing_field",
            stream_key=stream_key,
            error=str(exc),
        )
        return None


def _parse_funding_rate(stream_key: str, entry: dict[str, str]) -> FundingRate | None:
    """Parse a FundingRate from a funding:* stream entry."""
    try:
        parts = stream_key.split(":")
        exchange = parts[1]
        symbol = parts[2]
        return FundingRate(
            exchange=exchange,
            symbol=symbol,
            ts=int(entry["ts"]),
            funding_rate=float(entry["funding_rate"]),
            next_funding_ts=int(entry["next_funding_ts"]),
        )
    except (KeyError, ValueError, IndexError) as exc:
        log.warning(
            "parse_stream_entry_missing_field",
            stream_key=stream_key,
            error=str(exc),
        )
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_stream_entry(stream_key: str, entry: dict[str, str]) -> BusEvent | None:
    """Convert a raw Redis stream entry to a typed BusEvent.

    Routes by stream_key prefix. Returns None silently for unknown stream keys
    (high-volume path — no log). Returns None and logs WARN for known stream
    keys with missing or malformed required fields.

    OrderFilled / OrderRejected are NOT parsed here — they arrive via private
    WebSocket directly into the strategy queue (see Bus Manager, Epic 12) and
    never travel through Redis streams.
    """
    if stream_key.startswith("candles:"):
        return _parse_bar_close(stream_key, entry)
    if stream_key.startswith("ob_features:"):
        return _parse_ob_snapshot(stream_key, entry)
    if stream_key.startswith("ticks:"):
        return _parse_tick_or_gap(stream_key, entry)
    if stream_key.startswith("funding:"):
        return _parse_funding_rate(stream_key, entry)

    # Unknown stream key — return None with no log (high-volume path)
    return None
