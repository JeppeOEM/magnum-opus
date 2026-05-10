# Story 11.6: BaseStrategy — Signal Computation Foundation

Status: done

## Story

As mrqdt,
I want an abstract BaseStrategy that enforces NaN guards, gap invalidation, lookback gating, and heartbeat liveness,
so that concrete strategy subclasses cannot accidentally bypass safety mechanisms.

## Acceptance Criteria

1. `bot_service/strategy/base.py` defines `BaseStrategy(ABC)` with abstract properties `min_lookback: int`, `max_position_pct: float`, `stop_loss_pct: float`, `paper_trading: bool` (each declared `@property @abstractmethod`); concrete subclasses that do not override all four fail `mypy --strict`.
2. `register_bar_handler(symbol, tf, handler)` wraps the handler with two guards applied in order: (a) lookback gate — if `len(df) < min_lookback: return` (no log); (b) NaN guard — if `df.isnull().any().any()`, increment `bot_nan_guard_total{strategy, symbol}` and return. `min_lookback` is captured from `self.min_lookback` at registration time (deliberate — dynamic changes to the property after registration have no effect). Wrapping is applied inside `register_bar_handler` and cannot be bypassed by subclasses.
3. `handle_gap(gap: GapMarker)` sets `_signal_invalid[gap.symbol] = True`, resets `_clean_bar_count[gap.symbol] = 0`, and marks `has_gap = True` on the most recently appended row in every tracked DataFrame for that symbol (i.e. all timeframes — a gap in the feed is gap-agnostic to timeframe).
4. On each clean BarClose for a symbol with `_signal_invalid[symbol] == True`: `_on_clean_bar(symbol)` increments `_clean_bar_count[symbol]`; when the count reaches `min_lookback`, clears `_signal_invalid[symbol]`. Any new `GapMarker` for that symbol resets the count to zero. `_on_clean_bar` is called from `on_bar` for all symbols, not only symbols with registered handlers.
5. `get_history(symbol, tf, n_bars) -> pd.DataFrame` attempts a synchronous QuestDB HTTP query; on failure returns an empty DataFrame with schema `[ts, open, high, low, close, volume, quote_volume, trade_count, is_complete, has_gap]`, logs WARN `"get_history_failed_returning_empty"`, and schedules a background retry task via `self._loop.create_task(_retry_loop())` inside `self._loop.call_soon_threadsafe(...)`. The retry runs every 30 s in the strategy loop until the first success. If `self._loop` is None or closed at call time, the retry is silently skipped.
6. When history is loaded (initial or retry) and the fraction of gap rows > 0.05: log WARN `"coldstart_high_gap_fraction"` and set `bot_coldstart_gap_fraction{strategy, symbol, tf}` gauge to the fraction (0.0–1.0); do NOT reject the DataFrame.
7. `SubscribeTimeoutError(RuntimeError)` in `bot_service/strategy/base.py`; `run_subscribe(timeout_s: float)` submits `subscribe()` to a `ThreadPoolExecutor(max_workers=1, cancel_futures=True)` and calls `fut.result(timeout=timeout_s)`; raises `SubscribeTimeoutError` on `concurrent.futures.TimeoutError`; other exceptions from `subscribe()` propagate as-is. `cancel_futures=True` on shutdown prevents the background thread from blocking indefinitely after a timeout.
8. Heartbeat: `start_heartbeat()` spawns a daemon thread running: `while True: sleep(5) → _heartbeat_ack.clear() → if not _heartbeat_ack.wait(10): SIGTERM; return`. The sequence means: sleep 5 s (during which any pre-set ack is wiped), then require a fresh ack within 10 s. The strategy event loop must call `ack_heartbeat()` at least once every 10 s after each clear; in practice it calls it on every event dispatched.
9. `bot_service/metrics/prometheus.py` gains `inc_nan_guard(strategy, symbol)` (Counter `bot_nan_guard_total`) and `set_coldstart_gap_fraction(strategy, symbol, tf, value)` (Gauge `bot_coldstart_gap_fraction`); add `Gauge` to the `prometheus_client` import; follow existing `_lock` / lazy-init pattern.
10. L1 tests in `tests/test_base_strategy.py`: NaN guard intercepts; lookback gate suppresses until min_lookback; GapMarker → `_signal_invalid` True; N-1 clean bars → still invalid; N-th clean bar → valid; second gap resets count.
11. L2 test: heartbeat detects frozen loop → `os.kill(pid, SIGTERM)` called within 15 s.

## Tasks / Subtasks

- [ ] Define `SubscribeTimeoutError` and `BaseStrategy(ABC)` in `bot_service/strategy/base.py` (AC: 1–8)
  - [ ] Abstract properties `min_lookback`, `max_position_pct`, `stop_loss_pct`, `paper_trading` (`@property @abstractmethod`)
  - [ ] `__init__(name, settings)` — init state dicts, `_heartbeat_ack = threading.Event()`
  - [ ] `register_bar_handler(symbol, tf, handler)` — capture `min_lb = self.min_lookback`, wrap with lookback gate then NaN guard
  - [ ] `on_bar(bar)` — append row to DF, call `_on_clean_bar(symbol)`, dispatch registered handler
  - [ ] `handle_gap(gap)` — set `_signal_invalid`, reset count, mark `has_gap=True` on last row of all symbol DFs
  - [ ] `_on_clean_bar(symbol)` — increment counter only when `_signal_invalid[symbol] == True`; clear at min_lookback
  - [ ] `get_history(symbol, tf, n_bars)` — sync query, empty DF + WARN + retry on failure; gap fraction check (AC: 5, 6)
  - [ ] `run_subscribe(timeout_s)` — `ThreadPoolExecutor(cancel_futures=True)`, raise `SubscribeTimeoutError` (AC: 7)
  - [ ] `start_heartbeat()` / `ack_heartbeat()` / `_heartbeat_loop()` (AC: 8)
- [ ] Add `inc_nan_guard` and `set_coldstart_gap_fraction` to `bot_service/metrics/prometheus.py`; add `Gauge` to import (AC: 9)
- [ ] Write `tests/test_base_strategy.py` (AC: 10–11)

## Dev Notes

### File Layout

- `bot_service/strategy/base.py` — REPLACE stub entirely
- `bot_service/metrics/prometheus.py` — UPDATE: add `Gauge` to import, add two new helpers
- `tests/test_base_strategy.py` — NEW file; L1 tests use fake Settings from conftest; L2 test marked `@pytest.mark.l2`

### BaseStrategy Implementation

```python
from __future__ import annotations

import asyncio
import os
import signal
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Callable

import httpx
import pandas as pd
import structlog

from bot_service.bus.event_types import BarClose, GapMarker
from bot_service.config import Settings
from bot_service.metrics.prometheus import inc_nan_guard, set_coldstart_gap_fraction

log = structlog.get_logger()

_HEARTBEAT_POST_INTERVAL_S = 5.0
_HEARTBEAT_ACK_TIMEOUT_S = 10.0

_HISTORY_COLUMNS: list[str] = [
    "ts", "open", "high", "low", "close",
    "volume", "quote_volume", "trade_count", "is_complete", "has_gap",
]


class SubscribeTimeoutError(RuntimeError):
    """Raised when subscribe() does not complete within the configured timeout."""


class BaseStrategy(ABC):

    # ---- Abstract attributes (mypy --strict requires concrete override) ----

    @property
    @abstractmethod
    def min_lookback(self) -> int: ...

    @property
    @abstractmethod
    def max_position_pct(self) -> float: ...

    @property
    @abstractmethod
    def stop_loss_pct(self) -> float: ...

    @property
    @abstractmethod
    def paper_trading(self) -> bool: ...

    @abstractmethod
    def subscribe(self) -> None:
        """Register event handlers. Routing table is fixed after this returns."""
        ...

    # ---- Lifecycle ----

    def __init__(self, name: str, settings: Settings) -> None:
        self._name = name
        self._settings = settings
        self._dfs: dict[tuple[str, str], pd.DataFrame] = {}
        self._bar_handlers: dict[tuple[str, str], Callable[[pd.DataFrame], None]] = {}
        self._signal_invalid: dict[str, bool] = {}
        self._clean_bar_count: dict[str, int] = {}
        self._heartbeat_ack = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---- Handler registration ----

    def register_bar_handler(
        self,
        symbol: str,
        tf: str,
        handler: Callable[[pd.DataFrame], None],
    ) -> None:
        strategy_name = self._name
        min_lb = self.min_lookback  # captured at registration — deliberate

        def _wrapped(df: pd.DataFrame) -> None:
            if len(df) < min_lb:
                return
            if df.isnull().any().any():
                inc_nan_guard(strategy_name, symbol)
                return
            handler(df)

        self._bar_handlers[(symbol, tf)] = _wrapped

    # ---- Event dispatch ----

    def on_bar(self, bar: BarClose) -> None:
        key = (bar.symbol, bar.tf)
        if key not in self._dfs:
            self._dfs[key] = pd.DataFrame(columns=_HISTORY_COLUMNS)

        row = {
            "ts": bar.ts, "open": bar.open, "high": bar.high, "low": bar.low,
            "close": bar.close, "volume": bar.volume,
            "quote_volume": bar.quote_volume, "trade_count": bar.trade_count,
            "is_complete": bar.is_complete, "has_gap": False,
        }
        self._dfs[key] = pd.concat(
            [self._dfs[key], pd.DataFrame([row])], ignore_index=True
        )

        self._on_clean_bar(bar.symbol)

        handler = self._bar_handlers.get(key)
        if handler is not None:
            handler(self._dfs[key])

    def handle_gap(self, gap: GapMarker) -> None:
        """Invalidate signal and mark has_gap=True on the newest row of all timeframe DFs for this symbol."""
        self._signal_invalid[gap.symbol] = True
        self._clean_bar_count[gap.symbol] = 0

        for (sym, _tf), df in self._dfs.items():
            if sym == gap.symbol and len(df) > 0:
                df.iloc[-1, df.columns.get_loc("has_gap")] = True

        log.warning("gap_invalidated_signal", strategy=self._name, symbol=gap.symbol)

    # ---- Gap recovery ----

    def _on_clean_bar(self, symbol: str) -> None:
        if not self._signal_invalid.get(symbol, False):
            return
        self._clean_bar_count[symbol] = self._clean_bar_count.get(symbol, 0) + 1
        if self._clean_bar_count[symbol] >= self.min_lookback:
            self._signal_invalid[symbol] = False
            self._clean_bar_count[symbol] = 0
            log.info("signal_restored", strategy=self._name, symbol=symbol)

    # ---- History ----

    def get_history(self, symbol: str, tf: str, n_bars: int) -> pd.DataFrame:
        df = self._query_questdb(symbol, tf, n_bars)
        if df is None:
            log.warning("get_history_failed_returning_empty",
                        strategy=self._name, symbol=symbol, tf=tf)
            df = pd.DataFrame(columns=_HISTORY_COLUMNS)
            self._schedule_history_retry(symbol, tf, n_bars)
        else:
            self._dfs[(symbol, tf)] = df
            self._check_gap_fraction(df, symbol, tf)
        return df

    def _query_questdb(self, symbol: str, tf: str, n_bars: int) -> pd.DataFrame | None:
        query = (
            f"SELECT ts,open,high,low,close,volume,quote_volume,trade_count,"
            f"is_complete,has_gap FROM snapshot_1s "
            f"WHERE symbol='{symbol}' AND tf='{tf}' "
            f"ORDER BY ts DESC LIMIT {n_bars}"
        )
        try:
            resp = httpx.get(
                f"{self._settings.questdb_http_addr}/exec",
                params={"query": query},
                timeout=5.0,
            )
            resp.raise_for_status()
            data = resp.json()
            rows = data.get("dataset", [])
            cols = [c["name"] for c in data.get("columns", [])]
            df = pd.DataFrame(rows, columns=cols)
            df.sort_values("ts", inplace=True)
            df.reset_index(drop=True, inplace=True)
            return df
        except Exception as exc:
            log.warning("questdb_history_query_failed", error=str(exc),
                        strategy=self._name, symbol=symbol, tf=tf)
            return None

    def _check_gap_fraction(self, df: pd.DataFrame, symbol: str, tf: str) -> None:
        if "has_gap" not in df.columns or len(df) == 0:
            return
        fraction = float(df["has_gap"].sum()) / len(df)
        if fraction > 0.05:
            log.warning("coldstart_high_gap_fraction",
                        strategy=self._name, symbol=symbol, tf=tf, fraction=fraction)
            set_coldstart_gap_fraction(self._name, symbol, tf, fraction)

    def _schedule_history_retry(self, symbol: str, tf: str, n_bars: int) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        async def _retry_loop() -> None:
            while True:
                await asyncio.sleep(30)
                df = self._query_questdb(symbol, tf, n_bars)
                if df is not None:
                    self._dfs[(symbol, tf)] = df
                    self._check_gap_fraction(df, symbol, tf)
                    log.info("get_history_retry_succeeded",
                             strategy=self._name, symbol=symbol, tf=tf)
                    return

        loop.call_soon_threadsafe(lambda: loop.create_task(_retry_loop()))

    # ---- Subscribe with timeout ----

    def run_subscribe(self, timeout_s: float) -> None:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(self.subscribe)
            try:
                fut.result(timeout=timeout_s)
            except FuturesTimeoutError:
                raise SubscribeTimeoutError(
                    f"Strategy {self._name!r} subscribe() timed out after {timeout_s}s"
                )
        # ThreadPoolExecutor.__exit__ calls shutdown(wait=True, cancel_futures=True)
        # in Python 3.9+, ensuring the background thread does not block indefinitely.

    # ---- Heartbeat ----

    def start_heartbeat(self) -> None:
        t = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name=f"heartbeat-{self._name}"
        )
        t.start()

    def ack_heartbeat(self) -> None:
        self._heartbeat_ack.set()

    def _heartbeat_loop(self) -> None:
        while True:
            time.sleep(_HEARTBEAT_POST_INTERVAL_S)
            self._heartbeat_ack.clear()
            if not self._heartbeat_ack.wait(timeout=_HEARTBEAT_ACK_TIMEOUT_S):
                log.critical("heartbeat_timeout", strategy=self._name)
                os.kill(os.getpid(), signal.SIGTERM)
                return
```

### Prometheus Additions (metrics/prometheus.py)

Update the import line and add two helpers. **`Gauge` must be in the import.**

```python
from prometheus_client import CollectorRegistry, Counter, Gauge  # add Gauge here

_nan_guard: Counter | None = None
_coldstart_gap: Gauge | None = None


def inc_nan_guard(strategy: str, symbol: str) -> None:
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
```

### L1/L2 Test Pattern

**Monkeypatch targets — always use the `bot_service.strategy.base` namespace:**
- `monkeypatch.setattr("bot_service.strategy.base.inc_nan_guard", ...)`
- `monkeypatch.setattr("bot_service.strategy.base.os.kill", ...)`

**Stub subclass — use proper `@property` overrides** (not `property(lambda …)` class assignments):

```python
from __future__ import annotations

import signal
import time
import unittest.mock as mock

import pandas as pd
import pytest

from bot_service.bus.event_types import BarClose, GapMarker
from bot_service.strategy.base import BaseStrategy, SubscribeTimeoutError


class _StubStrategy(BaseStrategy):
    @property
    def min_lookback(self) -> int: return 3
    @property
    def max_position_pct(self) -> float: return 0.1
    @property
    def stop_loss_pct(self) -> float: return 0.02
    @property
    def paper_trading(self) -> bool: return True
    def subscribe(self) -> None: pass


def _make_strategy(name: str = "test") -> _StubStrategy:
    # conftest.py sets all required credential env vars — Settings() is valid here
    from bot_service.config import Settings
    return _StubStrategy(name, Settings())


def _bar(symbol: str, ts: int = 1000, tf: str = "1s") -> BarClose:
    return BarClose(exchange="kucoin", symbol=symbol, tf=tf, ts=ts,
                    open=1.0, high=1.0, low=1.0, close=1.0,
                    volume=1.0, quote_volume=1.0, trade_count=1, is_complete=True)


def test_nan_guard_intercepts_nan_dataframe(monkeypatch: pytest.MonkeyPatch) -> None:
    guards: list[tuple[str, str]] = []
    monkeypatch.setattr("bot_service.strategy.base.inc_nan_guard",
                        lambda s, sym: guards.append((s, sym)))
    s = _make_strategy("strat_nan")
    s.register_bar_handler("BTC", "1s", lambda df: None)

    for ts in [1000, 2000, 3000]:   # 3 rows — past lookback gate
        s.on_bar(_bar("BTC", ts=ts))
    # Inject NaN into the last row
    df = s._dfs[("BTC", "1s")]
    df.iloc[-1, df.columns.get_loc("close")] = float("nan")
    # Invoke the wrapped handler directly
    s._bar_handlers[("BTC", "1s")](s._dfs[("BTC", "1s")])

    assert guards == [("strat_nan", "BTC")]


def test_lookback_gate_suppresses_handler_before_min_lookback() -> None:
    calls: list[int] = []
    s = _make_strategy()
    s.register_bar_handler("BTC", "1s", lambda df: calls.append(len(df)))

    s.on_bar(_bar("BTC", ts=1000))  # 1 row
    s.on_bar(_bar("BTC", ts=2000))  # 2 rows
    assert calls == []
    s.on_bar(_bar("BTC", ts=3000))  # 3 rows — gate opens
    assert calls == [3]


def test_gapmarker_sets_signal_invalid_and_marks_row() -> None:
    s = _make_strategy()
    s.on_bar(_bar("BTC", ts=1000))
    s.handle_gap(GapMarker(exchange="", symbol="BTC", gap_cause="external_disconnect", ts=2000))

    assert s._signal_invalid["BTC"] is True
    assert bool(s._dfs[("BTC", "1s")].iloc[-1]["has_gap"]) is True


def test_clean_bars_restore_signal_at_min_lookback() -> None:
    s = _make_strategy()
    s.on_bar(_bar("BTC", ts=1000))
    s.handle_gap(GapMarker(exchange="", symbol="BTC", gap_cause="external_disconnect", ts=2000))
    assert s._signal_invalid["BTC"] is True

    s.on_bar(_bar("BTC", ts=3000))  # 1 clean bar
    s.on_bar(_bar("BTC", ts=4000))  # 2 clean bars
    assert s._signal_invalid["BTC"] is True  # still invalid (N-1 = 2 < 3)

    s.on_bar(_bar("BTC", ts=5000))  # 3rd clean bar — cleared
    assert s._signal_invalid.get("BTC") is False


def test_new_gap_resets_clean_bar_count() -> None:
    s = _make_strategy()
    s.on_bar(_bar("BTC", ts=1000))
    s.handle_gap(GapMarker(exchange="", symbol="BTC", gap_cause="external_disconnect", ts=2000))

    s.on_bar(_bar("BTC", ts=3000))  # 1 clean bar
    s.on_bar(_bar("BTC", ts=4000))  # 2 clean bars

    # Second gap resets count
    s.handle_gap(GapMarker(exchange="", symbol="BTC", gap_cause="external_disconnect", ts=5000))
    assert s._clean_bar_count["BTC"] == 0
    assert s._signal_invalid["BTC"] is True

    for ts in [6000, 7000, 8000]:   # 3 clean bars from scratch → valid
        s.on_bar(_bar("BTC", ts=ts))
    assert s._signal_invalid.get("BTC") is False


@pytest.mark.l2
def test_heartbeat_sends_sigterm_on_frozen_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr("bot_service.strategy.base.os.kill",
                        lambda pid, sig: killed.append((pid, sig)))

    s = _make_strategy("hb_test")
    s.start_heartbeat()
    # Never call ack_heartbeat() → frozen loop simulation

    deadline = time.monotonic() + 15.0
    while not killed and time.monotonic() < deadline:
        time.sleep(0.1)

    assert killed, "expected os.kill within 15s"
    assert killed[0][1] == signal.SIGTERM
```

### What Already Exists

- `bot_service/strategy/base.py` — stub (`# Stub — populated in Story 11.6`), REPLACE entirely
- `bot_service/metrics/prometheus.py` — has `_lock`, `get_registry()`, existing counter helpers; add `Gauge` to import and two new helpers
- `bot_service/config.py` — `Settings.questdb_http_addr` used in `_query_questdb`; `bot_subscribe_timeout_s` used by the caller of `run_subscribe`, not by `BaseStrategy` itself
- `tests/conftest.py` — sets all five credential env vars and clears `get_settings` `lru_cache`; no changes needed

### Critical Constraints

- `@property @abstractmethod` (in that order) — NOT bare `ClassVar` annotations — for mypy --strict enforcement
- `min_lookback` is captured as `min_lb = self.min_lookback` at handler registration time; post-registration changes to the property have no effect on the closure
- `_on_clean_bar` is called from `on_bar` for ALL symbols regardless of handler registration
- `handle_gap` marks `has_gap=True` on the last row of ALL timeframe DataFrames for the given symbol — a feed-level gap affects all timeframes equally
- Gap fraction threshold: `fraction > 0.05` (raw ratio of gap rows), NOT `count > 0.05 * min_lookback`
- `asyncio.ensure_future(loop=...)` was removed in Python 3.10 — always use `loop.create_task(...)` inside `call_soon_threadsafe`
- `ThreadPoolExecutor.__exit__` in Python 3.9+ calls `shutdown(wait=True, cancel_futures=True)`, preventing indefinite block after timeout
- Heartbeat: acks set during the 5 s sleep are wiped by the subsequent `clear()`; strategy loops must be able to ack within 10 s after each `clear()`
- `bool(df.iloc[-1]["has_gap"])` is needed in tests — pandas/numpy `bool_` is not `bool`, so `is True` identity check fails

### References

- [Source: _bmad-output/planning-artifacts/epics-bot.md § Story 11.6]
- [Source: _bmad-output/planning-artifacts/architecture.md § Strategy interface, Multi-timeframe DataFrames]
- [Source: bot_service/metrics/prometheus.py — lazy counter/gauge init pattern]
- [Source: bot_service/config.py — Settings.questdb_http_addr]

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- `pandas` and `httpx` were not in requirements-dev.txt; installed via pip during dev. Add to requirements-dev.txt in a follow-up.
- NaN guard test initially used `lambda df: calls.append(1)` instead of `lambda df: calls.append(len(df))`, causing wrong assertion. Fixed before commit.
- `bool(df.iloc[-1]["has_gap"]) is True` needed because pandas returns `numpy.bool_`, not Python `bool`.

### File List

- bot_service/strategy/base.py
- bot_service/metrics/prometheus.py
- tests/test_base_strategy.py
