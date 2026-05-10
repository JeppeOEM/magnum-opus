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
import pandas_ta as ta  # noqa: F401  # registers df.ta accessor on all DataFrames
import structlog

from bot_service.bus.event_types import BarClose, GapMarker
from bot_service.config import Settings
from bot_service.exchange import ExchangeClient, OrderRequest
from bot_service.metrics.prometheus import (
    inc_heartbeat_timeout,
    inc_nan_guard,
    set_coldstart_gap_fraction,
)

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
    """Abstract base for all live trading strategies.

    Safety mechanisms (NaN guard, lookback gate, gap invalidation, heartbeat)
    are applied automatically and cannot be bypassed by subclasses.
    """

    # ---- Abstract attributes (mypy --strict requires concrete overrides) ----

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

    @property
    @abstractmethod
    def bus_timeout_seconds(self) -> int: ...

    @property
    @abstractmethod
    def close_on_bus_timeout(self) -> bool: ...

    @abstractmethod
    def subscribe(self) -> None:
        """Register event handlers. Routing table is fixed after this returns."""
        ...

    # ---- Lifecycle ----

    def __init__(self, name: str, settings: Settings) -> None:
        self._name = name
        self._settings = settings
        # (symbol, tf) → rolling DataFrame, newest row last
        self._dfs: dict[tuple[str, str], pd.DataFrame] = {}
        # (symbol, tf) → wrapped handler
        self._bar_handlers: dict[tuple[str, str], Callable[[pd.DataFrame], None]] = {}
        # Gap tracking
        self._signal_invalid: dict[str, bool] = {}
        self._clean_bar_count: dict[str, int] = {}
        # Managed positions: symbols with open positions discovered at startup
        # reconciliation, routed to this strategy regardless of subscribe() selections.
        self._managed_positions: set[str] = set()
        # Heartbeat — freeze detection
        self._heartbeat_ack = threading.Event()
        # Set by the strategy runner before starting the event loop
        self._loop: asyncio.AbstractEventLoop | None = None
        # Bus timeout — silence detection
        self._last_event_ts: float = time.time()
        self._open_positions: dict[str, float] = {}
        # Injected by registry before start_heartbeat() is called
        self._exchange_client: ExchangeClient | None = None
        self._exchange: str = ""
        # Injected by registry; used by strategies to post OrderRequests
        self._order_worker: object | None = None
        # Prevents concurrent emergency-close threads for the same symbol (thread explosion guard)
        self._emergency_close_lock = threading.Lock()
        self._emergency_close_in_flight: set[str] = set()

    # ---- Indicator computation hook ----

    def add_indicators(self, df: pd.DataFrame) -> None:
        """Append pandas-ta indicator columns to the rolling DataFrame in-place.

        Called on every BarClose, after the new row is appended and before the
        registered bar handler fires. Override in subclasses:

            def add_indicators(self, df: pd.DataFrame) -> None:
                df.ta.rsi(length=14, append=True)  # type: ignore[attr-defined]
                df.ta.ema(length=20, append=True)   # type: ignore[attr-defined]

        The NaN guard in ``register_bar_handler`` checks only the last row, so
        indicator warmup NaN values in early rows do not permanently block handlers.
        Handlers are blocked only while the most-recent bar itself has NaN.

        IMPORTANT: mutate ``df`` in-place only. Reassigning the local name (e.g.
        ``df = df.dropna()``) has no effect — ``self._dfs`` retains the original
        reference.
        """

    # ---- Handler registration ----

    def register_bar_handler(
        self,
        symbol: str,
        tf: str,
        handler: Callable[[pd.DataFrame], None],
    ) -> None:
        """Register a BarClose handler wrapped with lookback gate then NaN guard.

        min_lookback is captured at registration time — post-registration changes
        to the property have no effect on already-registered handlers.
        """
        strategy_name = self._name
        min_lb = self.min_lookback  # deliberate closure capture

        def _wrapped(df: pd.DataFrame) -> None:
            if len(df) < min_lb:
                return
            if df.iloc[-1:].isnull().any().any():
                inc_nan_guard(strategy_name, symbol)
                return
            handler(df)

        self._bar_handlers[(symbol, tf)] = _wrapped

    # ---- Event dispatch ----

    def on_bar(self, bar: BarClose) -> None:
        """Append bar to rolling DF, advance clean-bar counter, dispatch handler."""
        self._last_event_ts = time.time()
        key = (bar.symbol, bar.tf)
        if key not in self._dfs:
            self._dfs[key] = pd.DataFrame(columns=_HISTORY_COLUMNS)

        row: dict[str, object] = {
            "ts": bar.ts,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "quote_volume": bar.quote_volume,
            "trade_count": bar.trade_count,
            "is_complete": bar.is_complete,
            "has_gap": False,
        }
        self._dfs[key] = pd.concat(
            [self._dfs[key], pd.DataFrame([row])], ignore_index=True
        )

        self._on_clean_bar(bar.symbol)
        try:
            self.add_indicators(self._dfs[key])
        except Exception as exc:
            log.warning(
                "add_indicators_failed",
                strategy=self._name,
                symbol=bar.symbol,
                tf=bar.tf,
                error=str(exc),
            )

        handler = self._bar_handlers.get(key)
        if handler is not None:
            handler(self._dfs[key])

    def handle_gap(self, gap: GapMarker) -> None:
        """Invalidate signal for gap.symbol and mark has_gap=True on the newest row
        in every tracked DataFrame for that symbol (all timeframes)."""
        self._signal_invalid[gap.symbol] = True
        self._clean_bar_count[gap.symbol] = 0

        for (sym, _tf), df in self._dfs.items():
            if sym == gap.symbol and len(df) > 0:
                df.iloc[-1, df.columns.get_loc("has_gap")] = True

        log.warning("gap_invalidated_signal", strategy=self._name, symbol=gap.symbol)

    # ---- Gap recovery ----

    def _on_clean_bar(self, symbol: str) -> None:
        """Advance clean-bar count; clear invalid flag when min_lookback is reached."""
        if not self._signal_invalid.get(symbol, False):
            return
        self._clean_bar_count[symbol] = self._clean_bar_count.get(symbol, 0) + 1
        if self._clean_bar_count[symbol] >= self.min_lookback:
            self._signal_invalid[symbol] = False
            self._clean_bar_count[symbol] = 0
            log.info("signal_restored", strategy=self._name, symbol=symbol)

    # ---- History loading ----

    def get_history(self, symbol: str, tf: str, n_bars: int) -> pd.DataFrame:
        """Synchronously query QuestDB; return empty DF on failure + schedule retry."""
        df = self._query_questdb(symbol, tf, n_bars)
        if df is None:
            log.warning(
                "get_history_failed_returning_empty",
                strategy=self._name,
                symbol=symbol,
                tf=tf,
            )
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
            log.warning(
                "questdb_history_query_failed",
                error=str(exc),
                strategy=self._name,
                symbol=symbol,
                tf=tf,
            )
            return None

    def _check_gap_fraction(self, df: pd.DataFrame, symbol: str, tf: str) -> None:
        if "has_gap" not in df.columns or len(df) == 0:
            return
        fraction = float(df["has_gap"].sum()) / len(df)
        if fraction > 0.05:
            log.warning(
                "coldstart_high_gap_fraction",
                strategy=self._name,
                symbol=symbol,
                tf=tf,
                fraction=fraction,
            )
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
                    log.info(
                        "get_history_retry_succeeded",
                        strategy=self._name,
                        symbol=symbol,
                        tf=tf,
                    )
                    return

        loop.call_soon_threadsafe(lambda: loop.create_task(_retry_loop()))

    # ---- Subscribe with timeout ----

    def run_subscribe(self, timeout_s: float) -> None:
        """Call subscribe() with a wall-clock timeout.

        Uses ThreadPoolExecutor so exceptions from subscribe() propagate.
        ThreadPoolExecutor.__exit__ calls shutdown(cancel_futures=True) in
        Python 3.9+, preventing indefinite block after TimeoutError.
        """
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(self.subscribe)
            try:
                fut.result(timeout=timeout_s)
            except FuturesTimeoutError:
                raise SubscribeTimeoutError(
                    f"Strategy {self._name!r} subscribe() timed out after {timeout_s}s"
                )

    # ---- Heartbeat ----

    def start_heartbeat(self) -> None:
        """Spawn the freeze-detection heartbeat thread and the bus-timeout thread."""
        t = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name=f"heartbeat-{self._name}",
        )
        t.start()
        t2 = threading.Thread(
            target=self._bus_timeout_loop,
            daemon=True,
            name=f"bus-timeout-{self._name}",
        )
        t2.start()

    def ack_heartbeat(self) -> None:
        """Called by the strategy event loop to signal liveness."""
        self._heartbeat_ack.set()

    def _heartbeat_loop(self) -> None:
        while True:
            time.sleep(_HEARTBEAT_POST_INTERVAL_S)
            self._heartbeat_ack.clear()
            if not self._heartbeat_ack.wait(timeout=_HEARTBEAT_ACK_TIMEOUT_S):
                log.critical("heartbeat_timeout", strategy=self._name)
                os.kill(os.getpid(), signal.SIGTERM)
                return

    def _bus_timeout_loop(self) -> None:
        """OS thread: poll every 1s for bus silence; fire emergency close when elapsed."""
        while True:
            time.sleep(1.0)
            elapsed = time.time() - self._last_event_ts
            if elapsed >= self.bus_timeout_seconds:
                self._on_bus_timeout(elapsed)

    def _on_bus_timeout(self, elapsed: float) -> None:
        """Log, increment counter, and optionally spawn emergency-close threads."""
        pos = dict(self._open_positions)
        log.warning(
            "bus_timeout",
            strategy=self._name,
            open_positions=pos,
            elapsed_seconds=round(elapsed, 1),
        )
        inc_heartbeat_timeout(self._name)
        if not self.close_on_bus_timeout or self._exchange_client is None:
            return
        for symbol, qty in pos.items():
            if qty <= 0.0:
                continue
            with self._emergency_close_lock:
                if symbol in self._emergency_close_in_flight:
                    continue
                self._emergency_close_in_flight.add(symbol)
            t = threading.Thread(
                target=self._emergency_close_symbol,
                args=(symbol, qty),
                daemon=True,
                name=f"emergency-close-{self._name}-{symbol}",
            )
            t.start()

    def _emergency_close_symbol(self, symbol: str, qty: float) -> None:
        """Retry market-sell until success or process exit. Called from daemon thread.

        Skipped for paper trading — PaperExchangeClient.place_order schedules an async
        fill task via asyncio.create_task, which asyncio.run would immediately cancel.
        """
        try:
            if self.paper_trading:
                log.warning(
                    "emergency_close_skipped_paper_trading",
                    strategy=self._name,
                    symbol=symbol,
                )
                return
            client = self._exchange_client
            if client is None:
                return
            while True:
                try:
                    req = OrderRequest(
                        strategy=self._name,
                        exchange=self._exchange,
                        symbol=symbol,
                        side="sell",
                        order_type="market",
                        order_role="exit",
                        size=qty,
                        paper_trading=self.paper_trading,
                    )
                    asyncio.run(client.place_order(req))
                    self._open_positions.pop(symbol, None)
                    return
                except Exception as exc:
                    log.critical(
                        "emergency_close_failed",
                        strategy=self._name,
                        symbol=symbol,
                        side="sell",
                        qty=qty,
                        error=str(exc),
                    )
                    time.sleep(5.0)
        finally:
            with self._emergency_close_lock:
                self._emergency_close_in_flight.discard(symbol)
