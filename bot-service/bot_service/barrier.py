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
        # 2 * timeout_ms before this bucket cannot receive a new late event.
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
