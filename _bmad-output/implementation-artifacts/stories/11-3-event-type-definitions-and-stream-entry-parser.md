# Story 11.3: Event Type Definitions & Stream Entry Parser

Status: done

## Story

As mrqdt,
I want typed, immutable event types and a single parser function that converts raw Redis stream entries to typed events,
so that all downstream code is fully type-checked with no `dict[str, str]` leaking past the bus boundary.

## Acceptance Criteria

1. `bot_service/bus/event_types.py` defines exactly these `@dataclass(frozen=True)` types: `BarClose`, `MultiBarClose`, `GapMarker`, `OBSnapshot`, `Tick`, `FundingRate`, `OrderFilled`, `OrderRejected`, plus `BusEvent: TypeAlias`.
2. `BusEvent: TypeAlias = BarClose | GapMarker | OBSnapshot | Tick | FundingRate | OrderFilled | OrderRejected` (note: `MultiBarClose` is produced by the Barrier, NOT by the parser — exclude it from `BusEvent`).
3. `parse_stream_entry(stream_key: str, entry: dict[str, str]) -> BusEvent | None` returns the correctly typed frozen dataclass for each known event type.
4. Unknown `stream_key` format → returns `None` silently (no log — high-volume path).
5. Valid stream key but missing required field in `entry` → returns `None` and logs WARN with stream key and missing field name.
6. `mypy --strict` passes with zero errors on `event_types.py` and all files that import it.
7. L1 tests: one per event type (round-trip from `dict[str, str]` → typed dataclass), unknown key → None, missing field → None + WARN logged.

## Tasks / Subtasks

- [ ] Implement all dataclasses in `bot_service/bus/event_types.py` (AC: 1)
  - [ ] `BarClose`, `MultiBarClose`, `GapMarker`, `OBSnapshot`, `Tick`, `FundingRate`, `OrderFilled`, `OrderRejected`
  - [ ] All `@dataclass(frozen=True)` with fully annotated fields
  - [ ] `BusEvent: TypeAlias` excluding `MultiBarClose` (AC: 2)
- [ ] Implement `parse_stream_entry` in same file (AC: 3–5)
  - [ ] Route by `stream_key` prefix (e.g. `candles:close:` → `BarClose`, `ticks:` → `Tick | GapMarker` by `type` field, `ob_features:` → `OBSnapshot`, `funding:` → `FundingRate`)
  - [ ] Missing field: log WARN, return None
  - [ ] Unknown key: return None, no log
- [ ] Write L1 tests in `tests/test_event_types.py` (AC: 7)
  - [ ] One test per event type
  - [ ] Test: unknown stream_key → None, no WARN logged
  - [ ] Test: missing required field → None + WARN logged

## Dev Notes

### Dataclass Field Definitions

Design each type to match the Redis stream fields produced by the candle-service. Use `str` for exchange/symbol/tf identifiers. Use `float` for prices/volumes. Use `int` for timestamps (Unix ms). Use `bool` where needed.

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import TypeAlias
import structlog

log = structlog.get_logger()

@dataclass(frozen=True)
class BarClose:
    exchange: str
    symbol: str
    tf: str            # "1s", "1m", "5m", "15m", "1h", "4h", "1d", "1w"
    ts: int            # Unix ms, floor-aligned to tf boundary
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
    # Produced by Barrier, NOT by parser — excluded from BusEvent
    tf: str
    ts: int
    closes: dict[str, BarClose]   # symbol → BarClose; missing symbols absent

@dataclass(frozen=True)
class GapMarker:
    exchange: str
    symbol: str
    gap_cause: str     # "external_disconnect" | "external_rate_limit" | "internal_buffer_overflow" | "internal_merge_error" | "queue_overflow"
    ts: int            # Unix ms when gap was detected

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
    side: str          # "buy" | "sell"

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

BusEvent: TypeAlias = BarClose | GapMarker | OBSnapshot | Tick | FundingRate | OrderFilled | OrderRejected
```

### parse_stream_entry Routing Logic

Stream key patterns (from project-context.md § Redis Stream Contracts):
- `candles:close:{exchange}:{symbol}:{tf}` → `BarClose` (only `is_complete="true"` entries)
- `candles:{exchange}:{symbol}:{tf}` → `BarClose` (partial + close)
- `ob_features:{exchange}:{symbol}` → `OBSnapshot`
- `ticks:{exchange}:{symbol}` → route by `entry["type"]` field: `"tick"` → `Tick`, `"gap"` → `GapMarker`
- `funding:{exchange}:{symbol}` → `FundingRate`

All Redis stream values are strings — parse numeric fields explicitly:
```python
def _parse_bar_close(stream_key: str, entry: dict[str, str]) -> BarClose | None:
    try:
        parts = stream_key.split(":")
        # candles:close:exchange:symbol:tf → parts[2], parts[3], parts[4]
        # candles:exchange:symbol:tf → parts[1], parts[2], parts[3]
        ...
        return BarClose(
            exchange=...,
            ts=int(entry["ts"]),
            open=float(entry["open"]),
            ...
        )
    except (KeyError, ValueError) as exc:
        log.warning("parse_stream_entry_missing_field", stream_key=stream_key, error=str(exc))
        return None
```

### Key Constraints

- `MultiBarClose` must be importable from `event_types.py` (it's used by the Barrier in Story 11.5) but must NOT appear in `BusEvent` TypeAlias
- `dict[str, BarClose]` in `MultiBarClose.closes` — this field is `dict`, not a frozen type, so `MultiBarClose` cannot be fully frozen at runtime if the dict is mutable. Use `types.MappingProxyType` or accept the limitation and document it.  Actually, keep it simple: use `dict[str, BarClose]` but note that `frozen=True` only makes the dataclass fields themselves immutable, not the dict contents. Do not use `MappingProxyType` — overkill for this context.
- All Redis values arrive as `str` — always parse to target types explicitly, catch `ValueError`/`KeyError` and return None + WARN

### What Already Exists (Stories 11.1–11.2 output)

- `bot_service/bus/event_types.py` — currently a stub with just `from __future__ import annotations` — REPLACE this file entirely
- `bot_service/config.py`, `pyproject.toml`, `Makefile` — unchanged
- `tests/` directory with `__init__.py` — add `test_event_types.py` here

### mypy Note on `dict` field in frozen dataclass

`dict[str, BarClose]` as a field in `@dataclass(frozen=True)` passes mypy --strict. The `frozen=True` makes the reference immutable (can't reassign `closes`), not the dict itself. This is acceptable — document with an inline comment.

### References

- [Source: bot-service/project-context.md § Redis Stream Contracts]
- [Source: bot-service/project-context.md § Python Code Standards]
- [Source: _bmad-output/planning-artifacts/epics-bot.md § Story 11.3]

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

None — clean run, no debug iterations required.

### Completion Notes List

- Replaced stub `bot_service/bus/event_types.py` with full implementation: 8 `@dataclass(frozen=True)` types, `BusEvent` TypeAlias (excludes `MultiBarClose`), and `parse_stream_entry` routing function with 6 private sub-parsers.
- `MultiBarClose` is importable for the Barrier (Story 11.5) but excluded from `BusEvent` per spec.
- Unknown `stream_key` → `None` with no log (high-volume path).
- Missing/malformed field in a known stream key → `None` + `structlog` WARN with `stream_key` and `error` fields.
- `orders:filled:*` and `orders:rejected:*` prefixes added for `OrderFilled`/`OrderRejected` (exchange event streams).
- `mypy --strict` passes with 0 errors across all 15 source files.
- `pytest -m l1` passes: 20 tests (8 existing from Stories 11.1–11.2 + 12 new).

### File List

- `bot_service/bus/event_types.py` — replaced (was stub)
- `tests/test_event_types.py` — new
