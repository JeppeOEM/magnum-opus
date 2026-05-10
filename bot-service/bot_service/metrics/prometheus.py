from __future__ import annotations

import threading

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

_lock = threading.Lock()
_registry: CollectorRegistry | None = None
# Track label combos used by reset-able gauges (avoids gauge._metrics private API)
_pos_pnl_symbols: dict[str, set[str]] = {}  # strategy → set[symbol]
_gauge_strategies: set[str] = set()          # strategies seen by drawdown / consumer_lag
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
_strategy_restart: Counter | None = None
_strategy_backoff_seconds: Gauge | None = None
_strategy_load_failure: Counter | None = None
_heartbeat_timeout: Counter | None = None
# Per-strategy position / P&L gauges (Story 16.1)
_position_size: Gauge | None = None
_unrealized_pnl: Gauge | None = None
_drawdown: Gauge | None = None
_consumer_lag: Gauge | None = None
# Order lifecycle counters (Story 16.1)
_order_placed: Counter | None = None
_order_filled: Counter | None = None
_order_rejected: Counter | None = None
# Execution latency histogram (Story 16.1)
_order_execution_latency_ms: Histogram | None = None
_LATENCY_BUCKETS = [10.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 5000.0]


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


def inc_strategy_restart(strategy: str) -> None:
    """Increment bot_strategy_restart_total{strategy} on watchdog-triggered restart."""
    global _strategy_restart
    registry = get_registry()
    with _lock:
        if _strategy_restart is None:
            _strategy_restart = Counter(
                "bot_strategy_restart_total",
                "Strategy thread restarts by watchdog",
                ["strategy"],
                registry=registry,
            )
        counter = _strategy_restart
    counter.labels(strategy=strategy).inc()


def set_strategy_backoff_seconds(strategy: str, seconds: float) -> None:
    """Set bot_strategy_backoff_seconds{strategy} gauge to current backoff duration."""
    global _strategy_backoff_seconds
    registry = get_registry()
    with _lock:
        if _strategy_backoff_seconds is None:
            _strategy_backoff_seconds = Gauge(
                "bot_strategy_backoff_seconds",
                "Current watchdog backoff duration before next restart attempt",
                ["strategy"],
                registry=registry,
            )
        gauge = _strategy_backoff_seconds
    gauge.labels(strategy=strategy).set(seconds)


def reset_strategy_gauges(strategy: str) -> None:
    """Reset per-strategy position/P&L gauges to 0 on watchdog crash detection.

    Called synchronously before the backoff sleep. Resets all label combinations
    for this strategy to 0.0 so dead strategies show no stale non-zero values.
    """
    with _lock:
        pos_g = _position_size
        pnl_g = _unrealized_pnl
        dd_g = _drawdown
        lag_g = _consumer_lag
        symbols = frozenset(_pos_pnl_symbols.get(strategy, ()))
        has_strat = strategy in _gauge_strategies
    for gauge in (pos_g, pnl_g):
        if gauge is None:
            continue
        for symbol in symbols:
            gauge.labels(strategy=strategy, symbol=symbol).set(0.0)
    if has_strat:
        for gauge in (dd_g, lag_g):
            if gauge is None:
                continue
            gauge.labels(strategy=strategy).set(0.0)


def inc_heartbeat_timeout(strategy: str) -> None:
    """Increment bot_heartbeat_timeout_total{strategy} on bus silence timeout."""
    global _heartbeat_timeout
    registry = get_registry()
    with _lock:
        if _heartbeat_timeout is None:
            _heartbeat_timeout = Counter(
                "bot_heartbeat_timeout_total",
                "Bus silence timeouts detected by per-strategy heartbeat thread",
                ["strategy"],
                registry=registry,
            )
        counter = _heartbeat_timeout
    counter.labels(strategy=strategy).inc()


def inc_strategy_load_failure(filename: str, reason: str) -> None:
    """Increment bot_strategy_load_failure_total{filename, reason} on strategy file load failure."""
    global _strategy_load_failure
    registry = get_registry()
    with _lock:
        if _strategy_load_failure is None:
            _strategy_load_failure = Counter(
                "bot_strategy_load_failure_total",
                "Strategy file load failures (import errors, duplicate class names)",
                ["filename", "reason"],
                registry=registry,
            )
        counter = _strategy_load_failure
    counter.labels(filename=filename, reason=reason).inc()


# ── Story 16.1: per-strategy position / P&L gauges ───────────────────────────

def set_position_size(strategy: str, symbol: str, value: float) -> None:
    """Set bot_position_size{strategy, symbol} (signed; negative = short)."""
    global _position_size
    registry = get_registry()
    with _lock:
        if _position_size is None:
            _position_size = Gauge(
                "bot_position_size",
                "Open position size (signed; negative = short)",
                ["strategy", "symbol"],
                registry=registry,
            )
        gauge = _position_size
        _pos_pnl_symbols.setdefault(strategy, set()).add(symbol)
    gauge.labels(strategy=strategy, symbol=symbol).set(value)


def set_unrealized_pnl(strategy: str, symbol: str, value: float) -> None:
    """Set bot_unrealized_pnl{strategy, symbol} in USD."""
    global _unrealized_pnl
    registry = get_registry()
    with _lock:
        if _unrealized_pnl is None:
            _unrealized_pnl = Gauge(
                "bot_unrealized_pnl",
                "Unrealized P&L for open position (USD)",
                ["strategy", "symbol"],
                registry=registry,
            )
        gauge = _unrealized_pnl
        _pos_pnl_symbols.setdefault(strategy, set()).add(symbol)
    gauge.labels(strategy=strategy, symbol=symbol).set(value)


def set_drawdown(strategy: str, value: float) -> None:
    """Set bot_drawdown{strategy} (fraction 0.0–1.0)."""
    global _drawdown
    registry = get_registry()
    with _lock:
        if _drawdown is None:
            _drawdown = Gauge(
                "bot_drawdown",
                "Strategy drawdown from peak equity (fraction 0.0–1.0)",
                ["strategy"],
                registry=registry,
            )
        gauge = _drawdown
        _gauge_strategies.add(strategy)
    gauge.labels(strategy=strategy).set(value)


def set_consumer_lag(strategy: str, value: float) -> None:
    """Set bot_consumer_lag{strategy} (unprocessed Redis stream entries)."""
    global _consumer_lag
    registry = get_registry()
    with _lock:
        if _consumer_lag is None:
            _consumer_lag = Gauge(
                "bot_consumer_lag",
                "Unprocessed Redis stream entries queued for this strategy",
                ["strategy"],
                registry=registry,
            )
        gauge = _consumer_lag
        _gauge_strategies.add(strategy)
    gauge.labels(strategy=strategy).set(value)


# ── Story 16.1: order lifecycle counters ─────────────────────────────────────

def inc_order_placed(strategy: str, exchange: str, symbol: str, side: str) -> None:
    """Increment bot_order_placed_total{strategy, exchange, symbol, side}."""
    global _order_placed
    registry = get_registry()
    with _lock:
        if _order_placed is None:
            _order_placed = Counter(
                "bot_order_placed_total",
                "Orders successfully placed on exchange",
                ["strategy", "exchange", "symbol", "side"],
                registry=registry,
            )
        counter = _order_placed
    counter.labels(strategy=strategy, exchange=exchange, symbol=symbol, side=side).inc()


def inc_order_filled(strategy: str, exchange: str, symbol: str, side: str) -> None:
    """Increment bot_order_filled_total{strategy, exchange, symbol, side}."""
    global _order_filled
    registry = get_registry()
    with _lock:
        if _order_filled is None:
            _order_filled = Counter(
                "bot_order_filled_total",
                "Orders confirmed filled",
                ["strategy", "exchange", "symbol", "side"],
                registry=registry,
            )
        counter = _order_filled
    counter.labels(strategy=strategy, exchange=exchange, symbol=symbol, side=side).inc()


def inc_order_rejected(strategy: str, exchange: str, symbol: str) -> None:
    """Increment bot_order_rejected_total{strategy, exchange, symbol}."""
    global _order_rejected
    registry = get_registry()
    with _lock:
        if _order_rejected is None:
            _order_rejected = Counter(
                "bot_order_rejected_total",
                "Orders rejected by exchange",
                ["strategy", "exchange", "symbol"],
                registry=registry,
            )
        counter = _order_rejected
    counter.labels(strategy=strategy, exchange=exchange, symbol=symbol).inc()


# ── Story 16.1: execution latency histogram ──────────────────────────────────

def observe_order_execution_latency_ms(strategy: str, exchange: str, latency_ms: float) -> None:
    """Observe bot_order_execution_latency_ms{strategy, exchange} in milliseconds."""
    global _order_execution_latency_ms
    registry = get_registry()
    with _lock:
        if _order_execution_latency_ms is None:
            _order_execution_latency_ms = Histogram(
                "bot_order_execution_latency_ms",
                "Time from OrderRequest posted to exchange REST response (ms)",
                ["strategy", "exchange"],
                buckets=_LATENCY_BUCKETS,
                registry=registry,
            )
        hist = _order_execution_latency_ms
    hist.labels(strategy=strategy, exchange=exchange).observe(latency_ms)
