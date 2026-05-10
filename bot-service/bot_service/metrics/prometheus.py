from __future__ import annotations

import threading

from prometheus_client import CollectorRegistry, Counter, Gauge

_lock = threading.Lock()
_registry: CollectorRegistry | None = None
_queue_drop: Counter | None = None
_barrier_timeout: Counter | None = None
_barrier_late: Counter | None = None
_nan_guard: Counter | None = None
_coldstart_gap: Gauge | None = None
_ws_fallback_active: Gauge | None = None
_fill_dedup: Counter | None = None
_order_queue_dedup: Counter | None = None
_risk_gate_block: Counter | None = None
_orphaned_order: Counter | None = None


def get_registry() -> CollectorRegistry:
    """Return the singleton private CollectorRegistry for bot-service metrics.

    Never uses the default prometheus_client registry — per project-context.md.
    Thread-safe: multiple callers racing on first call are serialised by _lock.
    """
    global _registry
    with _lock:
        if _registry is None:
            _registry = CollectorRegistry()
        return _registry


def inc_queue_drop(strategy: str) -> None:
    """Increment bot_queue_drop_total for the given strategy."""
    global _queue_drop
    # Resolve registry outside _lock to avoid re-entrant deadlock:
    # _lock is non-reentrant; calling get_registry() inside the lock would deadlock.
    registry = get_registry()
    with _lock:
        if _queue_drop is None:
            _queue_drop = Counter(
                "bot_queue_drop_total",
                "Events dropped from strategy queue (oldest evicted)",
                ["strategy"],
                registry=registry,
            )
        counter = _queue_drop
    counter.labels(strategy=strategy).inc()


def inc_barrier_timeout(tf: str) -> None:
    """Increment bot_barrier_timeout_total for the given timeframe."""
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
    """Increment bot_barrier_late_event_total for the given strategy and symbol."""
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


def inc_nan_guard(strategy: str, symbol: str) -> None:
    """Increment bot_nan_guard_total for the given strategy and symbol."""
    global _nan_guard
    registry = get_registry()
    with _lock:
        if _nan_guard is None:
            _nan_guard = Counter(
                "bot_nan_guard_total",
                "Signal handler calls blocked by NaN guard",
                ["strategy", "symbol"],
                registry=registry,
            )
        counter = _nan_guard
    counter.labels(strategy=strategy, symbol=symbol).inc()


def set_coldstart_gap_fraction(strategy: str, symbol: str, tf: str, value: float) -> None:
    """Set bot_coldstart_gap_fraction gauge for the given strategy/symbol/tf."""
    global _coldstart_gap
    registry = get_registry()
    with _lock:
        if _coldstart_gap is None:
            _coldstart_gap = Gauge(
                "bot_coldstart_gap_fraction",
                "Gap fraction in cold-start history (0.0–1.0)",
                ["strategy", "symbol", "tf"],
                registry=registry,
            )
        gauge = _coldstart_gap
    gauge.labels(strategy=strategy, symbol=symbol, tf=tf).set(value)


def set_ws_fallback_active(exchange: str, active: bool) -> None:
    """Set bot_ws_fallback_active{exchange} gauge (1 = fallback active, 0 = WS live)."""
    global _ws_fallback_active
    registry = get_registry()
    with _lock:
        if _ws_fallback_active is None:
            _ws_fallback_active = Gauge(
                "bot_ws_fallback_active",
                "Private WebSocket REST poll fallback active (1=active, 0=inactive)",
                ["exchange"],
                registry=registry,
            )
        gauge = _ws_fallback_active
    gauge.labels(exchange=exchange).set(1.0 if active else 0.0)


def inc_fill_dedup(exchange: str) -> None:
    """Increment bot_fill_dedup_total{exchange} counter on duplicate fill event."""
    global _fill_dedup
    registry = get_registry()
    with _lock:
        if _fill_dedup is None:
            _fill_dedup = Counter(
                "bot_fill_dedup_total",
                "Fill events discarded as duplicates (same order_id from WS and REST)",
                ["exchange"],
                registry=registry,
            )
        counter = _fill_dedup
    counter.labels(exchange=exchange).inc()


def inc_order_queue_dedup(strategy: str, symbol: str) -> None:
    """Increment bot_order_queue_dedup_total{strategy, symbol} on duplicate entry order."""
    global _order_queue_dedup
    registry = get_registry()
    with _lock:
        if _order_queue_dedup is None:
            _order_queue_dedup = Counter(
                "bot_order_queue_dedup_total",
                "Entry OrderRequests discarded as duplicates (same symbol+side already open)",
                ["strategy", "symbol"],
                registry=registry,
            )
        counter = _order_queue_dedup
    counter.labels(strategy=strategy, symbol=symbol).inc()


def inc_risk_gate_block(strategy: str, symbol: str) -> None:
    """Increment bot_risk_gate_block_total{strategy, symbol} on risk gate rejection."""
    global _risk_gate_block
    registry = get_registry()
    with _lock:
        if _risk_gate_block is None:
            _risk_gate_block = Counter(
                "bot_risk_gate_block_total",
                "OrderRequests discarded by risk gate (would exceed max_position_pct)",
                ["strategy", "symbol"],
                registry=registry,
            )
        counter = _risk_gate_block
    counter.labels(strategy=strategy, symbol=symbol).inc()


def inc_orphaned_order(exchange: str) -> None:
    """Increment bot_orphaned_order_total{exchange} on orphaned order detection."""
    global _orphaned_order
    registry = get_registry()
    with _lock:
        if _orphaned_order is None:
            _orphaned_order = Counter(
                "bot_orphaned_order_total",
                "Orders found on exchange with no matching QuestDB record",
                ["exchange"],
                registry=registry,
            )
        counter = _orphaned_order
    counter.labels(exchange=exchange).inc()
