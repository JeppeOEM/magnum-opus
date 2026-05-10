# Story 11.5: MultiBarClose Barrier

Status: done

## Story

As mrqdt,
I want a Barrier that collects per-symbol BarClose events for a given timeframe and delivers a single MultiBarClose when all expected symbols arrive (or times out),
so that strategies receive consistent multi-symbol snapshots and missing symbols are explicitly marked as gaps.

## Acceptance Criteria

1. `Barrier(symbols: frozenset[str], tf: str, timeout_ms: int, strategy: str = "")` in `bot_service/barrier.py`; unknown `tf` or empty `symbols` raises `ValueError` at construction time.
2. When all expected symbols have arrived for a given floor-ts bucket, `feed()` immediately returns `[MultiBarClose]` without waiting for timeout.
3. When `timeout_ms` ms elapses since first symbol arrived for bucket T and not all symbols have arrived, `feed()` flushes the expired bucket: returns `[MultiBarClose(closes=arrived_so_far), GapMarker(gap_cause="barrier_timeout"), ...]` — one GapMarker per missing symbol; `bot_barrier_timeout_total{tf}` is incremented once per timeout event.
4. When a BarClose for bucket T arrives after T has already been fired (timeout or complete), the late event produces no new output (though any concurrently expiring buckets may appear in the same return value); `bot_barrier_late_event_total{strategy, symbol}` is incremented; WARN `"barrier_late_event"` is logged.
5. Floor-ts alignment: `bucket_ts = bar.ts // tf_ms * tf_ms`; two BarClose events with the same floored timestamp are placed in the same bucket regardless of sub-timeframe jitter.
6. Timeout is checked lazily on every `feed()` call (expired buckets flushed before the new event is processed) — no background thread required.
7. `_fired` set is pruned on every bucket fire: entries with `bucket_ts < new_bucket_ts - 2 * timeout_ms` are removed; this bounds `_fired` memory without affecting correctness.
8. `bot_service/metrics/prometheus.py` gains `inc_barrier_timeout(tf: str)` and `inc_barrier_late_event(strategy: str, symbol: str)` following the existing `inc_queue_drop` pattern.
9. L1 tests in `tests/test_barrier.py`: all symbols arrive → MultiBarClose immediately; one symbol missing, advance clock past timeout → partial MultiBarClose + GapMarker + counter incremented; late event → no new output + counter incremented.

## Tasks / Subtasks

- [ ] Implement `bot_service/barrier.py` (AC: 1–7)
  - [ ] `_TF_MS` mapping for all 8 supported timeframes
  - [ ] `Barrier.__init__` with `ValueError` on unknown `tf` and on empty `symbols`
  - [ ] `feed(bar, now_ms=None)` with lazy timeout flush before processing
  - [ ] `_flush_expired(now_ms)` — fires buckets past timeout, increments `bot_barrier_timeout_total`
  - [ ] `_fire_bucket(bucket_ts, *, timed_out: bool)` — keyword-only `timed_out`, no default; pops pending, prunes `_fired`, marks fired, appends GapMarkers when timed out
  - [ ] Late-event detection, counter, WARN log
- [ ] Add `inc_barrier_timeout` and `inc_barrier_late_event` to `bot_service/metrics/prometheus.py` (AC: 8)
- [ ] Update `GapMarker.gap_cause` docstring in `bot_service/bus/event_types.py` to include `"barrier_timeout"` as a valid value
- [ ] Write `tests/test_barrier.py` with L1 unit tests (AC: 9)

## Dev Notes

### File Layout

- `bot_service/barrier.py` — NEW file; purely in-process, no Redis, no threads
- `bot_service/bus/event_types.py` — UPDATE: add `"barrier_timeout"` to `GapMarker.gap_cause` docstring
- `bot_service/metrics/prometheus.py` — UPDATE: add two new counter helpers
- `tests/test_barrier.py` — NEW file; L1 unit tests only, no fixtures needed

### Barrier Implementation

```python
from __future__ import annotations

import time

import structlog

from bot_service.bus.event_types import BarClose, GapMarker, MultiBarClose
from bot_service.metrics.prometheus import inc_barrier_late_event, inc_barrier_timeout

log = structlog.get_logger()

_TF_MS: dict[str, int] = {
    "1s":  1_000,
    "1m":  60_000,
    "5m":  300_000,
    "15m": 900_000,
    "1h":  3_600_000,
    "4h":  14_400_000,
    "1d":  86_400_000,
    "1w":  604_800_000,
}


class Barrier:
    """Collects per-symbol BarClose events for one timeframe.

    ``feed()`` is called once per BarClose from the strategy's event loop.
    It flushes any timed-out pending buckets first, then processes the new
    event.  No background thread is required — timeout checks are lazy.
    """

    def __init__(
        self,
        symbols: frozenset[str],
        tf: str,
        timeout_ms: int,
        strategy: str = "",
    ) -> None:
        if tf not in _TF_MS:
            raise ValueError(f"Unknown timeframe: {tf!r}. Supported: {list(_TF_MS)}")
        if not symbols:
            raise ValueError("symbols must be non-empty")
        self._symbols = symbols
        self._tf = tf
        self._tf_ms = _TF_MS[tf]
        self._timeout_ms = timeout_ms
        self._strategy = strategy
        # bucket_ts → {symbol: BarClose}
        self._pending: dict[int, dict[str, BarClose]] = {}
        # bucket_ts → first_arrival_ms (wall clock when first symbol for this bucket arrived)
        self._first_arrival: dict[int, int] = {}
        # set of recently-fired bucket_ts (pruned to keep memory bounded)
        self._fired: set[int] = set()

    def feed(
        self, bar: BarClose, now_ms: int | None = None
    ) -> list[MultiBarClose | GapMarker]:
        """Process one BarClose event.

        Returns a list of events to emit downstream.  An empty list means the
        barrier is still collecting for this bucket.  Late events produce no
        new output but may share a return value with concurrently flushed buckets.

        Args:
            bar: incoming BarClose from the bus queue.
            now_ms: current time in Unix ms; injected for tests, defaults to
                    real wall clock when None.
        """
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        # Flush any buckets that have exceeded timeout BEFORE handling new event.
        results: list[MultiBarClose | GapMarker] = self._flush_expired(now_ms)

        bucket_ts = bar.ts // self._tf_ms * self._tf_ms

        if bucket_ts in self._fired:
            log.warning(
                "barrier_late_event",
                strategy=self._strategy,
                symbol=bar.symbol,
                tf=self._tf,
                bucket_ts=bucket_ts,
            )
            inc_barrier_late_event(self._strategy, bar.symbol)
            return results

        if bucket_ts not in self._pending:
            self._pending[bucket_ts] = {}
            self._first_arrival[bucket_ts] = now_ms
        # Last-write-wins if the same symbol sends duplicate events for this bucket.
        self._pending[bucket_ts][bar.symbol] = bar

        if self._pending[bucket_ts].keys() >= self._symbols:
            results.extend(self._fire_bucket(bucket_ts, timed_out=False))

        return results

    def _flush_expired(self, now_ms: int) -> list[MultiBarClose | GapMarker]:
        expired = sorted(
            ts
            for ts, first_ms in self._first_arrival.items()
            if now_ms - first_ms >= self._timeout_ms
        )
        results: list[MultiBarClose | GapMarker] = []
        for ts in expired:
            inc_barrier_timeout(self._tf)
            results.extend(self._fire_bucket(ts, timed_out=True))
        return results

    def _fire_bucket(
        self, bucket_ts: int, *, timed_out: bool
    ) -> list[MultiBarClose | GapMarker]:
        closes = self._pending.pop(bucket_ts, {})
        self._first_arrival.pop(bucket_ts, None)

        # Prune _fired to prevent unbounded growth.  Any entry older than
        # 2 * timeout_ms before this bucket cannot receive a new late event
        # (the exchange would have to hold a message for longer than the full
        # timeout window, which is not a supported scenario).
        cutoff = bucket_ts - 2 * self._timeout_ms
        self._fired = {ts for ts in self._fired if ts >= cutoff}
        self._fired.add(bucket_ts)

        out: list[MultiBarClose | GapMarker] = [
            MultiBarClose(tf=self._tf, ts=bucket_ts, closes=closes)
        ]
        if timed_out:
            for symbol in sorted(self._symbols - closes.keys()):
                out.append(
                    GapMarker(
                        exchange="",
                        symbol=symbol,
                        gap_cause="barrier_timeout",
                        ts=bucket_ts,
                    )
                )
        return out
```

### Prometheus Additions (metrics/prometheus.py)

Follow the exact pattern of `inc_queue_drop` — resolve registry outside `_lock`, then acquire lock for lazy Counter init.

```python
_barrier_timeout: Counter | None = None
_barrier_late: Counter | None = None


def inc_barrier_timeout(tf: str) -> None:
    global _barrier_timeout
    registry = get_registry()
    with _lock:
        if _barrier_timeout is None:
            _barrier_timeout = Counter(
                "bot_barrier_timeout_total",
                "Barrier timeouts — partial MultiBarClose emitted",
                ["tf"],
                registry=registry,
            )
        counter = _barrier_timeout
    counter.labels(tf=tf).inc()


def inc_barrier_late_event(strategy: str, symbol: str) -> None:
    global _barrier_late
    registry = get_registry()
    with _lock:
        if _barrier_late is None:
            _barrier_late = Counter(
                "bot_barrier_late_event_total",
                "BarClose events discarded because their bucket already fired",
                ["strategy", "symbol"],
                registry=registry,
            )
        counter = _barrier_late
    counter.labels(strategy=strategy, symbol=symbol).inc()
```

### GapMarker Docstring Update (event_types.py)

In the `GapMarker.gap_cause` field comment, add `"barrier_timeout"` to the pipe-separated list:

```python
gap_cause: str  # "external_disconnect" | "external_rate_limit" | "internal_buffer_overflow" | "internal_merge_error" | "queue_overflow" | "barrier_timeout"
```

### L1 Test Pattern

**IMPORTANT — monkeypatch target:** `barrier.py` imports `inc_barrier_timeout` and `inc_barrier_late_event` directly into its module namespace. Always patch via `"bot_service.barrier.inc_barrier_*"`, NOT `"bot_service.metrics.prometheus.inc_barrier_*"` — the latter has no effect on the already-bound names in the barrier module.

```python
import pytest
from bot_service.barrier import Barrier
from bot_service.bus.event_types import BarClose, GapMarker, MultiBarClose


def _bar(symbol: str, ts: int, tf: str = "1s") -> BarClose:
    return BarClose(
        exchange="kucoin", symbol=symbol, tf=tf, ts=ts,
        open=1.0, high=1.0, low=1.0, close=1.0,
        volume=1.0, quote_volume=1.0, trade_count=1, is_complete=True,
    )


def test_all_symbols_arrive_immediately() -> None:
    b = Barrier(symbols=frozenset({"BTC", "ETH"}), tf="1s", timeout_ms=250)
    now = 1_000_000

    # floor-ts: 1000 // 1000 * 1000 = 1000
    r1 = b.feed(_bar("BTC", 1000), now_ms=now)
    assert r1 == []  # still waiting for ETH

    r2 = b.feed(_bar("ETH", 1001), now_ms=now)  # same floor bucket (1001 // 1000 * 1000 == 1000)
    assert len(r2) == 1
    assert isinstance(r2[0], MultiBarClose)
    assert set(r2[0].closes.keys()) == {"BTC", "ETH"}
    assert r2[0].ts == 1000  # floor-aligned


def test_timeout_fires_partial_multibarclose_and_gapmarker(monkeypatch) -> None:
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
    # Counter was incremented once
    assert timeouts == ["1s"]


def test_late_event_produces_no_new_output_and_increments_counter(monkeypatch) -> None:
    late_events: list[tuple[str, str]] = []
    monkeypatch.setattr("bot_service.barrier.inc_barrier_late_event",
                        lambda s, sym: late_events.append((s, sym)))

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
```

### What Already Exists

- `bot_service/bus/event_types.py` — `BarClose`, `MultiBarClose`, `GapMarker` fully implemented (Story 11.3); only the `gap_cause` comment needs updating
- `bot_service/metrics/prometheus.py` — `get_registry()`, `inc_queue_drop()`, `_lock` pattern; add new counters following same pattern
- `bot_service/config.py` — `bot_barrier_timeout_ms_1s` through `_1w` already present with defaults; `Barrier` does NOT read Settings directly — `timeout_ms` is always passed in by the caller

### Critical Constraints

- No background threads — timeout is checked lazily in `feed()`
- `_fire_bucket` signature is `(bucket_ts, *, timed_out: bool)` — `timed_out` is keyword-only with NO default; callers must always pass it explicitly
- `_fired` is pruned in `_fire_bucket` to entries `>= bucket_ts - 2 * timeout_ms`; this bounds it to at most `ceil(2 * timeout_ms / tf_ms)` entries (typically 1–2)
- The Barrier does NOT validate `bar.tf` against `self._tf` — it uses `bar.ts` and its own `_tf_ms` for floor computation; routing correctness is BaseStrategy's responsibility
- Duplicate symbol for same bucket: last-write-wins (`_pending[bucket_ts][bar.symbol] = bar`); no error, no counter
- `MultiBarClose.closes` is `dict[str, BarClose]` — missing symbols are absent from the dict, not None
- `gap_cause="barrier_timeout"` must be added to the `GapMarker.gap_cause` comment in `event_types.py`
- Tests must NOT import from `bot_service.config` (no env vars needed — barrier takes `timeout_ms` directly)
- Always patch metrics via `"bot_service.barrier.inc_barrier_*"` in tests, not via the prometheus module

### References

- [Source: _bmad-output/planning-artifacts/epics-bot.md § Story 11.5]
- [Source: _bmad-output/planning-artifacts/architecture.md § Event bus]
- [Source: bot_service/bus/event_types.py — MultiBarClose, BarClose, GapMarker]
- [Source: bot_service/metrics/prometheus.py — inc_queue_drop pattern]

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- `test_duplicate_symbol_in_same_bucket_last_write_wins` required a 2-symbol barrier; with a single-symbol barrier the first feed fires the bucket, making the second a late event rather than an overwrite test.
- `_TF_MS` dict preserves insertion/chronological order in the ValueError message rather than sorting alphabetically.

### File List

- bot_service/barrier.py
- bot_service/bus/event_types.py
- bot_service/metrics/prometheus.py
- tests/test_barrier.py
