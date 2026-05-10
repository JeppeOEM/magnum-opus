# Story 12.2: Private WebSocket Fill Feed & REST Poll Fallback

Status: done

## Story

As mrqdt,
I want per-exchange private WebSocket feeds that deliver fill events with automatic REST fallback when the feed goes silent,
so that fill acknowledgement is near-real-time in normal operation and resilient to WebSocket silent drops.

## Acceptance Criteria

1. `bot_service/exchange/kucoin/ws_private.py` and `bot_service/exchange/bybit/ws_private.py` exist; each connects to the exchange's private order/fill channel using the auth mechanism from Story 12.1 (HMAC credentials via `get_settings()`); reconnects automatically on disconnection with exponential backoff (initial 1s, ×2, max 30s).

2. Liveness is determined by `time.monotonic() - _last_msg_ts`, NOT the library connection state. A WebSocket that has sent no messages within `BOT_WS_FALLBACK_TIMEOUT_LIVE` seconds (live) or `BOT_WS_FALLBACK_TIMEOUT_PAPER` seconds (paper) is treated as dead regardless of connection state.

3. When silence timeout elapses: `bot_ws_fallback_active{exchange}` gauge is set to 1; REST `get_open_orders()` is polled every 2 seconds; the first WebSocket message received deactivates the fallback and returns the gauge to 0.

4. A fill event for the same `order_id` arriving from both WebSocket and REST poll is deduplicated via `_seen_fill_ids: set[str]`; `bot_fill_dedup_total{exchange}` counter is incremented on each duplicate; no double-callback fires.

5. REST poll fills are sorted by `ts_exchange` ascending before being applied — REST responses may arrive out of order.

6. L1 tests: silence detection (verify `_is_silent()` returns True/False after injected timestamps); fill deduplication (same order_id from two sources → one callback + one counter increment).

7. L2 test: WS feed goes silent (no messages for > threshold) → REST fallback activates (gauge=1) → first WS message resumes → fallback deactivates (gauge=0).

## Tasks / Subtasks

- [x] Add `websockets>=14.0` to `requirements.txt` (AC: 1)
  - [x] Confirm `websockets` 16.0 is already installed (transitive dep of uvicorn[standard]); add explicit pin
- [x] Add `bot_ws_fallback_active` gauge and `bot_fill_dedup_total` counter to `bot_service/metrics/prometheus.py` (AC: 3, 4)
  - [x] `set_ws_fallback_active(exchange: str, active: bool) -> None`
  - [x] `inc_fill_dedup(exchange: str) -> None`
- [x] Create `bot_service/exchange/kucoin/ws_private.py` with `KuCoinPrivateFeed` (AC: 1, 2, 3, 4, 5)
  - [x] `_get_ws_token()` — POST `/api/v1/bullet-private` signed with KC-API-* headers from Story 12.1 REST client; returns `(endpoint_url: str, token: str)`
  - [x] `_parse_fill(data: dict[str, Any]) -> OrderFilled | None` — extract fill from order-change message; return None if not a fill event
  - [x] `_connect_once(...)` — connect to `wss://{endpoint}?token={token}&connectId={uuid4()}`, subscribe, recv loop
  - [x] `run(on_fill, rest_client, paper_trading) -> None` — main loop: connect, subscribe, recv messages; backoff on disconnect; liveness + fallback as background asyncio tasks
  - [x] `_is_silent(timeout_s: float) -> bool` — `time.monotonic() - self._last_msg_ts > timeout_s`
  - [x] `_run_liveness_checker(timeout_s, rest_client, on_fill)` — background task: check silence every 1s, activate/deactivate fallback
  - [x] `_run_rest_fallback(rest_client, on_fill)` — background task: poll every 2s; sort fills by ts_placed asc; dedup; call on_fill
- [x] Create `bot_service/exchange/bybit/ws_private.py` with `BybitPrivateFeed` (AC: 1, 2, 3, 4, 5)
  - [x] `_build_auth_message() -> dict[str, Any]` — `{"op": "auth", "args": [api_key, expires_ms, signature]}`; expires = `int(time.time() * 1000) + 5000`; sig = `hmac.new(secret, f"GET/realtime{expires}", sha256).hexdigest()`
  - [x] `_parse_fills(data: dict[str, Any]) -> list[OrderFilled]` — extract fills from `topic="order"` messages with `orderStatus` in `{"Filled", "PartiallyFilled"}`
  - [x] `run(on_fill, rest_client, paper_trading) -> None` — main loop with backoff; auth on connect; liveness + fallback tasks
  - [x] `_is_silent`, `_run_liveness_checker`, `_run_rest_fallback` — same pattern as KuCoin
- [x] Write L1 tests `tests/test_ws_private.py` (AC: 6)
  - [x] Silence detection — inject `_last_msg_ts` value, verify `_is_silent(threshold)` returns expected bool
  - [x] Fill dedup — simulate same order_id arriving twice; assert callback called once, dedup counter incremented once
  - [x] KuCoin `_parse_fill` — filled order-change message → OrderFilled; non-fill message → None
  - [x] Bybit `_parse_fills` — filled order message → list with one OrderFilled; partially-filled → one fill; non-order topic → empty list
- [x] Write L2 test (AC: 7)
  - [x] Inject stale `_last_msg_ts`; assert fallback activates after liveness check
  - [x] Simulate WS message arriving; assert fallback deactivates

## Dev Notes

### File Layout

```
bot_service/exchange/
  kucoin/
    __init__.py
    rest.py          — EXISTS (Story 12.1)
    ws_private.py    — NEW: KuCoinPrivateFeed
  bybit/
    __init__.py
    rest.py          — EXISTS (Story 12.1)
    ws_private.py    — NEW: BybitPrivateFeed
bot_service/metrics/
  prometheus.py      — UPDATE: add ws_fallback_active + fill_dedup
tests/
  test_ws_private.py — NEW
requirements.txt     — UPDATE: add websockets>=14.0
```

### WebSocket Library

`websockets` 16.0 is installed (transitive dep of `uvicorn[standard]`). Add explicit pin to `requirements.txt`:
```
websockets>=14.0
```

The `websockets` client API (v12+):
```python
import websockets

async with websockets.connect("wss://...") as ws:
    await ws.send(json.dumps(msg))
    async for raw in ws:
        data = json.loads(raw)
```

For reconnect loops, `websockets.connect` raises `websockets.exceptions.ConnectionClosed` on disconnect. Catch it and re-enter the connect loop with backoff.

### KuCoin Private WebSocket — Protocol

**Step 1: Get private token (signed request)**

```python
async def _get_ws_token(self) -> tuple[str, str]:
    """Returns (endpoint_url, token). Token is short-lived (~5 min)."""
    # Use KuCoinRESTClient for the signed POST
    rest = KuCoinRESTClient()
    data = await rest._request("POST", "/api/v1/bullet-private", json_body={})
    # data shape: {"token": "...", "instanceServers": [{"endpoint": "wss://...", ...}]}
    token = data["token"]
    endpoint = data["instanceServers"][0]["endpoint"]
    return endpoint, token
```

**Step 2: Connect**

```
wss://{endpoint}?token={token}&connectId={uuid4}
```

Connection confirmation message arrives first:
```json
{"type": "welcome", "id": "..."}
```

**Step 3: Subscribe to order changes**

```json
{
    "id": "1",
    "type": "subscribe",
    "topic": "/contractMarket/tradeOrders",
    "privateChannel": true,
    "response": true
}
```

Note: `/contractMarket/tradeOrders` covers futures. For spot: `/spotMarket/tradeOrders`. The story implementation should subscribe to both or the futures channel — the epic targets futures. Confirm and subscribe to `/contractMarket/tradeOrders`.

**Step 4: Fill message format**

```json
{
    "type": "message",
    "topic": "/contractMarket/tradeOrders",
    "subject": "orderChange",
    "data": {
        "orderId": "5bd6e9286d99522a52e458de",
        "symbol": "XBTUSDTM",
        "type": "filled",
        "side": "buy",
        "matchSize": "0.001",
        "matchPrice": "30000.5",
        "fee": "0.000001",
        "tradeTime": 1685000000000,
        "clientOid": "abc123"
    }
}
```

`_parse_fill` logic:
- Return `None` if `data.get("type") != "filled"` (status: "open", "update", "match" etc. are not fills)
- Map `matchSize` → `fill_size` (float), `matchPrice` → `fill_price` (float)
- Map `tradeTime` → `ts_exchange` (int milliseconds)
- Map `fee` → `fee` (float, or 0.0 if absent)

**KuCoin keepalive**: the server sends `{"type": "pong"}` in response to `{"id": "...", "type": "ping"}`. Send a ping every 30 seconds to avoid server-side timeout. Update `_last_msg_ts` on every received message (including pong).

### Bybit Private WebSocket — Protocol

**Step 1: Connect**

```
wss://stream.bybit.com/v5/private
```

**Step 2: Auth message (send within 10 seconds of connect)**

```python
expires_ms = str(int(time.time() * 1000) + 5000)  # 5 seconds ahead
signature = hmac.new(
    secret.encode(),
    f"GET/realtime{expires_ms}".encode(),
    hashlib.sha256
).hexdigest()
auth_msg = {
    "op": "auth",
    "args": [api_key, expires_ms, signature]
}
await ws.send(json.dumps(auth_msg))
```

Response: `{"op": "auth", "success": true}`

**Step 3: Subscribe**

```json
{"op": "subscribe", "args": ["order"]}
```

Response: `{"op": "subscribe", "success": true, "ret_msg": "subscribe"}`

**Step 4: Fill message format**

```json
{
    "topic": "order",
    "data": [
        {
            "orderId": "1234567890",
            "symbol": "BTCUSDT",
            "side": "Buy",
            "orderType": "Limit",
            "orderStatus": "Filled",
            "cumExecQty": "0.001",
            "avgPrice": "30000.5",
            "cumExecFee": "0.003",
            "updatedTime": "1685000000000",
            "orderLinkId": "abc123"
        }
    ]
}
```

`_parse_fill` logic:
- Return `[]` if `data.get("topic") != "order"`
- For each item in `data["data"]`:
  - Include only if `item["orderStatus"] in {"Filled", "PartiallyFilled"}`
  - `fill_size = float(item.get("cumExecQty", 0))`
  - `fill_price = float(item.get("avgPrice", 0))`
  - `fee = float(item.get("cumExecFee", 0))`
  - `ts_exchange = int(item.get("updatedTime", 0))`
  - `side = item["side"].lower()`

**Bybit keepalive**: send `{"op": "ping"}` every 20 seconds. Response: `{"op": "pong"}`. Update `_last_msg_ts` on all received messages.

### Reconnect Pattern

```python
_BACKOFF_BASE = 1.0
_BACKOFF_MAX = 30.0

async def run(self, ...) -> None:
    backoff = _BACKOFF_BASE
    while True:
        try:
            await self._connect_once(...)
            backoff = _BACKOFF_BASE  # reset on clean exit
        except websockets.exceptions.ConnectionClosed as exc:
            log.warning("ws_private_disconnect", exchange=self.exchange, code=exc.code)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)
        except Exception as exc:
            log.warning("ws_private_error", exchange=self.exchange, error=_scrub_for_error(str(exc)))
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)
```

### Liveness + Fallback Design

```python
class KuCoinPrivateFeed:
    _last_msg_ts: float        # updated on every received WS message
    _seen_fill_ids: set[str]   # dedup across WS and REST poll
    _fallback_active: bool     # True when REST poll is active

    def _is_silent(self, timeout_s: float) -> bool:
        return time.monotonic() - self._last_msg_ts > timeout_s

    async def _run_liveness_checker(
        self,
        timeout_s: float,
        rest_client: KuCoinRESTClient,
        on_fill: Callable[[OrderFilled], Awaitable[None]],
        stop_event: asyncio.Event,
    ) -> None:
        fallback_task: asyncio.Task[None] | None = None
        while not stop_event.is_set():
            await asyncio.sleep(1.0)
            if self._is_silent(timeout_s) and not self._fallback_active:
                self._fallback_active = True
                set_ws_fallback_active(self.exchange, True)
                log.warning("ws_private_fallback_active", exchange=self.exchange)
                fallback_task = asyncio.create_task(
                    self._run_rest_fallback(rest_client, on_fill, stop_event)
                )
            elif not self._is_silent(timeout_s) and self._fallback_active:
                self._fallback_active = False
                set_ws_fallback_active(self.exchange, False)
                log.info("ws_private_fallback_deactivated", exchange=self.exchange)
                if fallback_task is not None:
                    fallback_task.cancel()
                    fallback_task = None

    async def _run_rest_fallback(
        self,
        rest_client: KuCoinRESTClient,
        on_fill: Callable[[OrderFilled], Awaitable[None]],
        stop_event: asyncio.Event,
    ) -> None:
        while not stop_event.is_set() and self._fallback_active:
            try:
                orders = await rest_client.get_open_orders(symbol="")  # "" = all symbols
                # sort by ts_placed ascending for chronological order
                fills = [o for o in orders if o.status in {"filled", "partially_filled"}]
                fills.sort(key=lambda o: o.ts_placed)
                for order in fills:
                    if order.order_id not in self._seen_fill_ids:
                        self._seen_fill_ids.add(order.order_id)
                        fill = OrderFilled(
                            order_id=order.order_id,
                            exchange=self.exchange,
                            symbol=order.symbol,
                            side=order.side,
                            fill_price=order.limit_price or 0.0,
                            fill_size=order.size,
                            fee=0.0,
                            ts_exchange=order.ts_placed,
                        )
                        await on_fill(fill)
                    else:
                        inc_fill_dedup(self.exchange)
            except ExchangeRESTError as exc:
                log.warning("ws_private_rest_poll_error", exchange=self.exchange,
                            error=_scrub_for_error(str(exc)))
            await asyncio.sleep(2.0)
```

### Fill Deduplication

```python
async def _handle_ws_fill(self, fill: OrderFilled, on_fill: Callable) -> None:
    if fill.order_id in self._seen_fill_ids:
        inc_fill_dedup(self.exchange)
        return
    self._seen_fill_ids.add(fill.order_id)
    await on_fill(fill)
```

`_seen_fill_ids` is **shared** between `_handle_ws_fill` and `_run_rest_fallback`. Since both run in the same asyncio event loop (one is the ws recv coroutine, one is a task), there is no concurrent mutation — asyncio's cooperative model protects the set without a lock.

### Prometheus Metrics to Add

In `bot_service/metrics/prometheus.py`:

```python
def set_ws_fallback_active(exchange: str, active: bool) -> None:
    """Set bot_ws_fallback_active{exchange} gauge (1=active, 0=inactive)."""

def inc_fill_dedup(exchange: str) -> None:
    """Increment bot_fill_dedup_total{exchange} counter."""
```

Use the same lazy-init pattern as existing metrics — global `None` sentinels, initialized under `_lock`, registered against `get_registry()`.

### on_fill Callback Contract

The `on_fill` parameter is `Callable[[OrderFilled], Awaitable[None]]`. It is implemented by the order worker in Story 12.3. For this story, tests can use `AsyncMock` or a simple `async def capture_fill(f): captured.append(f)`.

The feed class does NOT import from `bot_service.strategy` — this would create a circular import. The callback is injected, not hard-coded.

### Type Annotations for Callable

```python
from collections.abc import Callable, Awaitable

class KuCoinPrivateFeed:
    async def run(
        self,
        on_fill: Callable[[OrderFilled], Awaitable[None]],
        rest_client: KuCoinRESTClient,
        paper_trading: bool = False,
    ) -> None: ...
```

### L1 Test Pattern — Silence Detection

```python
@pytest.mark.l1
def test_is_silent_returns_true_after_threshold() -> None:
    feed = KuCoinPrivateFeed("kucoin")
    # Inject a stale last_msg_ts (11 seconds ago)
    feed._last_msg_ts = time.monotonic() - 11.0
    assert feed._is_silent(timeout_s=10.0) is True

@pytest.mark.l1
def test_is_silent_returns_false_within_threshold() -> None:
    feed = KuCoinPrivateFeed("kucoin")
    feed._last_msg_ts = time.monotonic() - 5.0
    assert feed._is_silent(timeout_s=10.0) is False
```

### L1 Test Pattern — Fill Deduplication

```python
@pytest.mark.l1
async def test_fill_dedup_blocks_second_call() -> None:
    from bot_service.metrics.prometheus import get_registry
    from prometheus_client import REGISTRY
    # use isolated registry via monkeypatch if needed

    received: list[OrderFilled] = []
    async def on_fill(f: OrderFilled) -> None:
        received.append(f)

    feed = KuCoinPrivateFeed("kucoin")
    fill = OrderFilled(order_id="abc123", exchange="kucoin", symbol="XBTUSDTM",
                       side="buy", fill_price=30000.0, fill_size=0.001, fee=0.001,
                       ts_exchange=1685000000000)
    await feed._handle_ws_fill(fill, on_fill)
    await feed._handle_ws_fill(fill, on_fill)  # duplicate

    assert len(received) == 1  # only one callback
```

### L1 Test Pattern — Parse Fill (KuCoin)

```python
@pytest.mark.l1
def test_kucoin_parse_fill_returns_order_filled() -> None:
    feed = KuCoinPrivateFeed("kucoin")
    msg = {
        "type": "message",
        "data": {
            "orderId": "abc123", "symbol": "XBTUSDTM", "type": "filled",
            "side": "buy", "matchSize": "0.001", "matchPrice": "30000.5",
            "fee": "0.001", "tradeTime": 1685000000000
        }
    }
    result = feed._parse_fill(msg["data"])
    assert result is not None
    assert result.order_id == "abc123"
    assert result.fill_price == pytest.approx(30000.5)

@pytest.mark.l1
def test_kucoin_parse_fill_returns_none_for_non_fill() -> None:
    feed = KuCoinPrivateFeed("kucoin")
    result = feed._parse_fill({"type": "open", "orderId": "abc"})
    assert result is None
```

### L2 Test Pattern — Fallback Activation

```python
@pytest.mark.l2
async def test_fallback_activates_on_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    from bot_service.metrics import prometheus as pm
    feed = KuCoinPrivateFeed("kucoin")
    # Inject stale timestamp so feed is already "silent"
    feed._last_msg_ts = time.monotonic() - 15.0

    gauge_values: list[bool] = []
    monkeypatch.setattr(pm, "set_ws_fallback_active",
                        lambda exchange, active: gauge_values.append(active))

    stop = asyncio.Event()
    mock_rest = AsyncMock(spec=KuCoinRESTClient)
    mock_rest.get_open_orders.return_value = []

    task = asyncio.create_task(
        feed._run_liveness_checker(timeout_s=10.0, rest_client=mock_rest,
                                   on_fill=AsyncMock(), stop_event=stop)
    )
    await asyncio.sleep(1.5)  # let one liveness check run
    stop.set()
    await task

    assert True in gauge_values  # fallback was activated
```

### What Already Exists

- `bot_service/exchange/kucoin/rest.py` — `KuCoinRESTClient`, `_sign_kucoin`, `_sign_passphrase` (Story 12.1)
- `bot_service/exchange/bybit/rest.py` — `BybitRESTClient`, `_sign_bybit` (Story 12.1)
- `bot_service/exchange/__init__.py` — `ExchangeRESTError`, `OrderFilled`, `OrderRejected`, `OrderRequest`, `PlacedOrder`, `OpenOrder`, `_scrub_for_error`
- `bot_service/metrics/prometheus.py` — lazy-init registry pattern with `_lock`
- `bot_service/config.py` — `get_settings()` with `bot_ws_fallback_timeout_live` (10) and `bot_ws_fallback_timeout_paper` (30)
- `tests/conftest.py` — credential env vars set, `clear_settings_cache` autouse fixture

### Critical Constraints

- **`_last_msg_ts` must use `time.monotonic()`, not `time.time()`** — monotonic is wall-clock-drift-immune; `time.time()` can jump backward on NTP adjustment, causing false fallback activation
- **`_seen_fill_ids` is never cleared** — the feed lifetime matches the process lifetime; clearing would re-process old fills on reconnect. If memory is a concern (long-running with millions of fills), this is a future concern (deferred).
- **Do NOT store credentials on the feed class** — call `get_settings()` at sign-time only, same pattern as Story 12.1
- **`on_fill` is always awaited** — the callback is async; never call it with `asyncio.create_task`; errors in `on_fill` must propagate to the caller
- **Bybit auth message uses `time.time()` (wall clock) for expires** — this is intentional; the exchange validates against its own clock; expires must be in the future from the exchange's perspective. Use `time.time()`, not `time.monotonic()`.
- **`from __future__ import annotations` first line** in every new `.py` file
- **No `Optional`, no `Union`** — use `X | None`, `X | Y`
- **mypy --strict zero errors** — run before marking done

### Dependencies to Add

`requirements.txt`:
```
websockets>=14.0
```

No new `requirements-dev.txt` entries needed — tests use `AsyncMock` from `unittest.mock`.

### References

- [Source: _bmad-output/planning-artifacts/epics-bot.md § Story 12.2]
- [Source: bot-service/project-context.md § Private WebSocket Health — Timestamp Not State]
- [Source: bot-service/project-context.md § Fill Deduplication]
- [Source: bot-service/bot_service/exchange/kucoin/rest.py — signing pattern (Story 12.1)]
- [Source: bot-service/bot_service/exchange/bybit/rest.py — signing pattern (Story 12.1)]
- [Source: bot-service/bot_service/metrics/prometheus.py — lazy-init registry pattern]
- [Source: bot-service/bot_service/config.py — BOT_WS_FALLBACK_TIMEOUT_LIVE/PAPER]

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- `websockets` 16.0 was already installed as a transitive dep of `uvicorn[standard]`; added explicit pin `>=14.0` to `requirements.txt` for stability.
- `OrderFilled` lives in `bot_service.bus.event_types`, not `bot_service.exchange` — import corrected in both ws_private modules and test file.
- `monkeypatch.setattr` on the `pm` module doesn't patch functions already imported by name in ws_private modules; tests patched the function in each ws_private module's namespace (`kucoin_ws.inc_fill_dedup`, etc.).
- `_seen_fill_ids` is shared between `_handle_ws_fill` and `_run_rest_fallback`; no lock needed — both run in the same asyncio event loop (cooperative multitasking).
- Bybit auth uses `time.time()` (wall clock) for expires; `time.monotonic()` is used only for liveness tracking.
- 72 tests pass (20 new: 15 L1 + 5 L2), zero regressions, mypy --strict clean.

### File List

- bot_service/exchange/kucoin/ws_private.py (NEW)
- bot_service/exchange/bybit/ws_private.py (NEW)
- bot_service/metrics/prometheus.py (UPDATE — added set_ws_fallback_active + inc_fill_dedup)
- requirements.txt (UPDATE — added websockets>=14.0)
- tests/test_ws_private.py (NEW)

## Senior Developer Review (AI)

**Review Date:** 2026-05-10
**Outcome:** Changes Requested → All actionable items resolved; structural issues deferred

### Action Items

- [x] **[HIGH] P2** — Orphaned fallback task on WS disconnect: `_run_liveness_checker` had no cleanup on CancelledError; old fallback task kept polling after reconnect. Fixed via try/finally that cancels fallback_task and resets `_fallback_active` on any exit path.
- [x] **[LOW] P7** — Ping loop swallowed exceptions silently: both ping loops now log.debug on exception before break.
- [x] **[LOW] P9** — Missing Bybit test for different order IDs both delivered: added `test_bybit_different_order_ids_both_delivered`.

### Deferred

- D1: REST fallback calls `get_open_orders` (returns open orders only) — never delivers fills. Needs fills/history endpoint (`/api/v1/fills` for KuCoin, `/v5/order/history` for Bybit). Out of scope for 12-2 (story spec says "open-orders endpoint").
- D2: Sort by `ts_placed` instead of `ts_exchange` — AC5 says sort by ts_exchange but OpenOrder.ts_placed is the only fill-time proxy available without fills endpoint. Fixed by D1.
- D3: `_get_ws_token` calls private `_request` method — add public `get_private_ws_token()` to KuCoinRESTClient.
- D4: KuCoin token lifetime (`tokenLife`) and server-negotiated `pingInterval` not used — hardcoded 30s ping.
- D5: Bybit auth failure retried with backoff forever — no ERROR escalation after N failures.
- D6: Missing test for empty `orderId` in `_parse_fill` — delivers fill with `order_id=""`.
- D7: L2 tests use 1.5s sleep with 1s liveness check interval — fragile under CI load.

## Review Follow-ups (AI)

- [x] [AI-Review] P2 (HIGH): Fix orphaned fallback task — try/finally in _run_liveness_checker
- [x] [AI-Review] P7 (LOW): Log ping failures
- [x] [AI-Review] P9 (LOW): Add Bybit test for distinct order IDs

### Change Log
