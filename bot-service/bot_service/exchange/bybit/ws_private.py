from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
import websockets
import websockets.exceptions

from bot_service.bus.event_types import OrderFilled
from bot_service.exchange import ExchangeRESTError, _scrub_for_error
from bot_service.exchange.bybit.rest import BybitRESTClient
from bot_service.metrics.prometheus import inc_fill_dedup, set_ws_fallback_active

log = structlog.get_logger()

_WS_URL = "wss://stream.bybit.com/v5/private"
_BACKOFF_BASE = 1.0
_BACKOFF_MAX = 30.0
_PING_INTERVAL_S = 20.0
_REST_POLL_INTERVAL_S = 2.0
_LIVENESS_CHECK_INTERVAL_S = 1.0

_FILL_STATUSES = frozenset({"Filled", "PartiallyFilled"})
_MAX_AUTH_FAILURES = 5


class BybitPrivateFeed:
    """Private WebSocket fill feed for Bybit v5 with REST poll fallback.

    Liveness is determined by time.monotonic() — last received message timestamp,
    not by the library's connection state. REST fallback activates on silence.
    """

    exchange: str
    _last_msg_ts: float
    _seen_fill_ids: set[str]
    _fallback_active: bool

    def __init__(self, exchange: str = "bybit") -> None:
        self.exchange = exchange
        self._last_msg_ts = time.monotonic()
        self._last_fill_ts_ms: int = 0
        self._auth_failure_count: int = 0
        self._seen_fill_ids = set()
        self._fallback_active = False

    def _is_silent(self, timeout_s: float) -> bool:
        """Return True if no message has been received within timeout_s seconds."""
        return time.monotonic() - self._last_msg_ts > timeout_s

    def _build_auth_message(self) -> dict[str, Any]:
        """Build Bybit auth op message with fresh timestamp and HMAC signature.

        expires_ms is 5 seconds ahead of now — auth is valid for the next 5 seconds.
        Uses time.time() (wall clock) because Bybit validates against its own UTC clock.
        """
        from bot_service.config import get_settings
        settings = get_settings()
        secret = settings.bybit_api_secret.get_secret_value()
        api_key = settings.bybit_api_key.get_secret_value()
        expires_ms = str(int(time.time() * 1000) + 5000)
        signature = hmac.new(
            secret.encode(),
            f"GET/realtime{expires_ms}".encode(),
            hashlib.sha256,
        ).hexdigest()
        # secret and api_key go out of scope after return
        return {"op": "auth", "args": [api_key, expires_ms, signature]}

    def _parse_fills(self, data: dict[str, Any]) -> list[OrderFilled]:
        """Parse a Bybit WS message; return list of fills (empty if not an order topic)."""
        if data.get("topic") != "order":
            return []
        items: list[dict[str, Any]] = data.get("data", [])
        fills: list[OrderFilled] = []
        for item in items:
            if item.get("orderStatus") not in _FILL_STATUSES:
                continue
            fills.append(
                OrderFilled(
                    order_id=str(item.get("orderId", "")),
                    exchange=self.exchange,
                    symbol=str(item.get("symbol", "")),
                    side=str(item.get("side", "")).lower(),
                    fill_price=float(item.get("avgPrice", 0)),
                    fill_size=float(item.get("cumExecQty", 0)),
                    fee=float(item.get("cumExecFee", 0)),
                    ts_exchange=int(item.get("updatedTime", 0)),
                )
            )
        return fills

    async def _handle_ws_fill(
        self,
        fill: OrderFilled,
        on_fill: Callable[[OrderFilled], Awaitable[None]],
    ) -> None:
        """Deliver fill to callback; drop and count if already seen."""
        if fill.order_id in self._seen_fill_ids:
            inc_fill_dedup(self.exchange)
            return
        self._seen_fill_ids.add(fill.order_id)
        await on_fill(fill)
        if fill.ts_exchange > 0:
            self._last_fill_ts_ms = fill.ts_exchange

    async def _run_rest_fallback(
        self,
        rest_client: BybitRESTClient,
        on_fill: Callable[[OrderFilled], Awaitable[None]],
        stop_event: asyncio.Event,
    ) -> None:
        """Poll fills endpoint every 2s; apply fills in ts_exchange order; dedup against WS."""
        while not stop_event.is_set() and self._fallback_active:
            try:
                since_ms = self._last_fill_ts_ms if self._last_fill_ts_ms > 0 else int(time.time() * 1000) - 1_800_000
                fills = await rest_client.get_recent_fills(symbol="", since_ms=since_ms)
                fills.sort(key=lambda f: f.ts_exchange)
                for fill in fills:
                    if fill.order_id not in self._seen_fill_ids:
                        self._seen_fill_ids.add(fill.order_id)
                        await on_fill(fill)
                    else:
                        inc_fill_dedup(self.exchange)
            except ExchangeRESTError as exc:
                log.warning(
                    "bybit_ws_rest_poll_error",
                    exchange=self.exchange,
                    error=_scrub_for_error(str(exc)),
                )
            await asyncio.sleep(_REST_POLL_INTERVAL_S)

    async def _run_liveness_checker(
        self,
        timeout_s: float,
        rest_client: BybitRESTClient,
        on_fill: Callable[[OrderFilled], Awaitable[None]],
        stop_event: asyncio.Event,
    ) -> None:
        """Check silence every second; activate/deactivate REST fallback as needed.

        try/finally ensures the fallback task is cancelled when this task is
        cancelled (e.g. on WS disconnect), preventing orphaned polling goroutines.
        """
        fallback_task: asyncio.Task[None] | None = None
        try:
            while not stop_event.is_set():
                await asyncio.sleep(_LIVENESS_CHECK_INTERVAL_S)
                if self._is_silent(timeout_s) and not self._fallback_active:
                    self._fallback_active = True
                    set_ws_fallback_active(self.exchange, True)
                    log.warning("bybit_ws_fallback_active", exchange=self.exchange)
                    fallback_task = asyncio.create_task(
                        self._run_rest_fallback(rest_client, on_fill, stop_event)
                    )
                elif not self._is_silent(timeout_s) and self._fallback_active:
                    self._fallback_active = False
                    set_ws_fallback_active(self.exchange, False)
                    log.info("bybit_ws_fallback_deactivated", exchange=self.exchange)
                    if fallback_task is not None:
                        fallback_task.cancel()
                        fallback_task = None
        finally:
            # Cancel fallback task on CancelledError (WS disconnect / session end)
            if fallback_task is not None:
                fallback_task.cancel()
            if self._fallback_active:
                self._fallback_active = False
                set_ws_fallback_active(self.exchange, False)

    async def _connect_once(
        self,
        rest_client: BybitRESTClient,
        on_fill: Callable[[OrderFilled], Awaitable[None]],
        stop_event: asyncio.Event,
        timeout_s: float,
    ) -> None:
        """One WebSocket session: connect, auth, subscribe, recv loop."""
        async with websockets.connect(_WS_URL) as ws:
            # Authenticate (must send within 10 seconds of connect)
            await ws.send(json.dumps(self._build_auth_message()))
            raw = await ws.recv()
            auth_resp = json.loads(raw)
            if not auth_resp.get("success"):
                self._auth_failure_count += 1
                if self._auth_failure_count >= _MAX_AUTH_FAILURES:
                    log.critical(
                        "bybit_ws_auth_max_failures",
                        failures=self._auth_failure_count,
                    )
                    raise RuntimeError(f"Bybit WS auth failed {self._auth_failure_count} times")
                raise ExchangeRESTError(f"Bybit WS auth failed: {str(auth_resp)[:200]}")
            self._auth_failure_count = 0
            self._last_msg_ts = time.monotonic()

            # Subscribe to order updates
            await ws.send(json.dumps({"op": "subscribe", "args": ["order"]}))

            ping_task = asyncio.create_task(self._ping_loop(ws, stop_event))
            liveness_task = asyncio.create_task(
                self._run_liveness_checker(timeout_s, rest_client, on_fill, stop_event)
            )
            try:
                async for raw_msg in ws:
                    if stop_event.is_set():
                        break
                    self._last_msg_ts = time.monotonic()
                    msg = json.loads(raw_msg)
                    for fill in self._parse_fills(msg):
                        await self._handle_ws_fill(fill, on_fill)
            finally:
                ping_task.cancel()
                liveness_task.cancel()

    async def _ping_loop(self, ws: Any, stop_event: asyncio.Event) -> None:
        """Send a ping every 20s to keep the Bybit WS alive."""
        while not stop_event.is_set():
            await asyncio.sleep(_PING_INTERVAL_S)
            if stop_event.is_set():
                break
            try:
                await ws.send(json.dumps({"op": "ping"}))
            except Exception as exc:
                log.debug("bybit_ws_ping_failed", exchange=self.exchange, error=str(exc)[:100])
                break

    async def run(
        self,
        on_fill: Callable[[OrderFilled], Awaitable[None]],
        rest_client: BybitRESTClient,
        paper_trading: bool = False,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Main loop: connect, auth, subscribe, receive fills; reconnect with backoff on error."""
        from bot_service.config import get_settings
        settings = get_settings()
        timeout_s = float(
            settings.bot_ws_fallback_timeout_paper if paper_trading
            else settings.bot_ws_fallback_timeout_live
        )
        if stop_event is None:
            stop_event = asyncio.Event()

        backoff = _BACKOFF_BASE
        while not stop_event.is_set():
            try:
                await self._connect_once(rest_client, on_fill, stop_event, timeout_s)
                backoff = _BACKOFF_BASE
            except websockets.exceptions.ConnectionClosed as exc:
                log.warning(
                    "bybit_ws_disconnect",
                    exchange=self.exchange,
                    code=getattr(exc, "code", None),
                    backoff=backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)
            except Exception as exc:
                log.warning(
                    "bybit_ws_error",
                    exchange=self.exchange,
                    error=_scrub_for_error(str(exc)),
                    backoff=backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)
