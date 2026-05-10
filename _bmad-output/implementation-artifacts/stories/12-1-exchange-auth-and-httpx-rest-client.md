# Story 12.1: Exchange Auth & httpx REST Client

Status: done

## Story

As mrqdt,
I want typed, signed REST clients for KuCoin and Bybit that never expose credentials in logs or errors,
so that order placement and REST poll fallback have a safe, testable HTTP layer.

## Acceptance Criteria

1. `bot_service/exchange/kucoin/rest.py` with `KuCoinRESTClient`: signed requests set `KC-API-KEY`, `KC-API-SIGN` (HMAC-SHA256 of `{timestamp}{method}{path}{body}`), `KC-API-TIMESTAMP`, `KC-API-PASSPHRASE` (HMAC-SHA256 of the passphrase using the secret), and `KC-API-KEY-VERSION: 2` headers. The raw secret is obtained via `SecretStr.get_secret_value()` at sign-time only and never stored in a variable that outlives the function call.
2. `bot_service/exchange/bybit/rest.py` with `BybitRESTClient`: signed requests set `X-BAPI-API-KEY`, `X-BAPI-TIMESTAMP`, `X-BAPI-RECV-WINDOW` (default 5000ms), `X-BAPI-SIGN` (HMAC-SHA256 of `{timestamp}{api_key}{recv_window}{querystring_or_body}`).
3. HTTP 5xx responses: retry up to 3 times with exponential backoff (1s → 2s → 4s); after 3 failures raises `ExchangeRESTError` with status code and truncated body (no credentials). 4xx responses raise `ExchangeRESTError` immediately with no retry.
4. Any `ExchangeRESTError` raised or logged must not contain any substring matching the raw values of `KUCOIN_API_KEY`, `KUCOIN_API_SECRET`, `KUCOIN_API_PASSPHRASE`, `BYBIT_API_KEY`, or `BYBIT_API_SECRET`.
5. Each request creates a fresh `async with httpx.AsyncClient()` — not a shared session; timeout is 10s connect + 30s read.
6. `bot_service/exchange/__init__.py` defines `ExchangeClient` as a `typing.Protocol` with abstract async methods: `place_order(...)`, `cancel_order(order_id, symbol)`, `get_open_orders(symbol)` — both `KuCoinRESTClient` and `BybitRESTClient` satisfy this protocol structurally.
7. L1 tests in `tests/test_exchange_rest.py`: KuCoin signature computed correctly against known test vector; Bybit signature computed correctly; 5xx retries 3 times then raises; 4xx raises immediately; no credential substring in `ExchangeRESTError` message.

## Tasks / Subtasks

- [x] Create `bot_service/exchange/kucoin/` subpackage with `__init__.py` (AC: 1)
  - [x] Write `bot_service/exchange/kucoin/rest.py` with `KuCoinRESTClient`
  - [x] `_sign(method, path, body_str)` — HMAC-SHA256 signing, credential not stored beyond call
  - [x] `_signed_headers(method, path, body_str)` — assembles all KC-API-* headers
  - [x] Async `_request(method, path, params, json)` — fresh AsyncClient, 5xx retry with backoff
  - [x] `place_order(...)`, `cancel_order(...)`, `get_open_orders(...)` public async methods
- [x] Create `bot_service/exchange/bybit/` subpackage with `__init__.py` (AC: 2)
  - [x] Write `bot_service/exchange/bybit/rest.py` with `BybitRESTClient`
  - [x] `_sign(timestamp, recv_window, payload_str)` — HMAC-SHA256 signing
  - [x] `_signed_headers(timestamp, recv_window, payload_str)` — assembles all X-BAPI-* headers
  - [x] Async `_request(method, path, params, json)` — fresh AsyncClient, 5xx retry with backoff
  - [x] `place_order(...)`, `cancel_order(...)`, `get_open_orders(...)` public async methods
- [x] Define `ExchangeClient` protocol in `bot_service/exchange/__init__.py` (AC: 6)
  - [x] `place_order`, `cancel_order`, `get_open_orders` async abstract methods
  - [x] `ExchangeRESTError(Exception)` defined here and imported by both clients
- [x] Write L1 tests `tests/test_exchange_rest.py` (AC: 7)
  - [x] KuCoin sign test vector
  - [x] Bybit sign test vector
  - [x] 5xx retry: 3 failures then `ExchangeRESTError`
  - [x] 4xx: raises immediately, no retry
  - [x] No credential in error message

## Dev Notes

### File Layout

```
bot_service/exchange/
  __init__.py          — ExchangeClient Protocol + ExchangeRESTError (UPDATE: currently empty stub)
  kucoin/
    __init__.py        — NEW
    rest.py            — NEW: KuCoinRESTClient
  bybit/
    __init__.py        — NEW
    rest.py            — NEW: BybitRESTClient
tests/
  test_exchange_rest.py — NEW
```

### ExchangeClient Protocol (exchange/__init__.py)

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ExchangeRESTError(Exception):
    """Raised by REST clients on non-retryable or exhausted-retry HTTP errors.

    The message must never contain raw credential values — truncate response
    bodies and omit headers before passing to this exception.
    """


@dataclass(frozen=True)
class OrderRequest:
    """Value object posted to the order queue by a strategy."""
    strategy: str
    exchange: str       # "kucoin" | "bybit"
    symbol: str         # e.g. "BTCUSDT"
    side: str           # "buy" | "sell"
    order_type: str     # "limit" | "market"
    order_role: str     # "entry" | "exit" | "stop"
    size: float
    limit_price: float | None = None
    stop_price: float | None = None
    take_profit_price: float | None = None
    client_order_id: str = ""
    paper_trading: bool = False


@dataclass(frozen=True)
class PlacedOrder:
    """Returned by place_order() on success."""
    order_id: str
    client_order_id: str
    status: str         # "placed" | "rejected"
    ts_exchange: int    # Unix ms from exchange


@dataclass(frozen=True)
class OpenOrder:
    """One open order returned by get_open_orders()."""
    order_id: str
    symbol: str
    side: str
    order_type: str
    size: float
    limit_price: float | None
    status: str         # "open" | "partially_filled"
    ts_placed: int


class ExchangeClient(Protocol):
    async def place_order(self, req: OrderRequest) -> PlacedOrder: ...
    async def cancel_order(self, order_id: str, symbol: str) -> None: ...
    async def get_open_orders(self, symbol: str) -> list[OpenOrder]: ...
```

### KuCoin Signing (v2 API)

KuCoin uses HMAC-SHA256 with base64 encoding. The API version 2 requires the passphrase itself to also be HMAC-signed.

**Signature string:** `timestamp + method.upper() + path_with_query + body_str`
- `timestamp`: milliseconds since epoch as a string, e.g. `str(int(time.time() * 1000))`
- `path_with_query`: the URL path including `?key=value` query string if present; for POST/PUT this is path only (body goes in JSON)
- `body_str`: JSON-encoded body for POST/PUT; empty string `""` for GET/DELETE

**Headers required:**
- `KC-API-KEY`: API key (plain string)
- `KC-API-SIGN`: base64(HMAC-SHA256(secret, signature_string))
- `KC-API-TIMESTAMP`: timestamp string (same used in signature)
- `KC-API-PASSPHRASE`: base64(HMAC-SHA256(secret, passphrase)) — v2 signs the passphrase too
- `KC-API-KEY-VERSION`: `"2"`
- `Content-Type`: `"application/json"`

```python
import base64
import hashlib
import hmac
import time

def _sign_kucoin(secret: str, timestamp: str, method: str, path: str, body_str: str) -> str:
    msg = timestamp + method.upper() + path + body_str
    return base64.b64encode(
        hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()

def _sign_passphrase(secret: str, passphrase: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), passphrase.encode(), hashlib.sha256).digest()
    ).decode()
```

**Known KuCoin test vector** (use this in L1 tests to verify correctness):
- `secret = "c5c895a3-5c4b-4c33-b73b-d82b9f1a7b3f"` (fake — for L1 only)
- `api_key = "test-key"`
- `passphrase = "test-passphrase"`
- `timestamp = "1685000000000"`
- `method = "POST"`
- `path = "/api/v1/orders"`
- `body_str = '{"clientOid":"abc","side":"buy","symbol":"BTC-USDT","type":"limit","size":"0.001","price":"30000"}'`
- Compute `sign = base64(HMAC-SHA256(secret, timestamp + "POST" + path + body_str))` and include in test assertion

The test vector should use `hmac.new(secret.encode(), ...).hexdigest()` to visually verify, but the actual header uses `base64(digest)` not hex.

### Bybit Signing (v5 API)

Bybit HMAC-SHA256 signature is hex-encoded (NOT base64).

**Signature string for GET:** `timestamp + api_key + recv_window + querystring`
- `querystring`: URL-encoded query params, e.g. `"category=spot&symbol=BTCUSDT"`

**Signature string for POST:** `timestamp + api_key + recv_window + json_body_str`
- `json_body_str`: JSON-encoded body

**Headers required:**
- `X-BAPI-API-KEY`: API key (plain string)
- `X-BAPI-TIMESTAMP`: timestamp string
- `X-BAPI-RECV-WINDOW`: `"5000"` (ms window for request validity)
- `X-BAPI-SIGN`: hex(HMAC-SHA256(secret, signature_string))
- `Content-Type`: `"application/json"`

```python
def _sign_bybit(secret: str, timestamp: str, api_key: str, recv_window: str, payload: str) -> str:
    msg = timestamp + api_key + recv_window + payload
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
```

**Known Bybit test vector:**
- `secret = "fake-bybit-secret-for-testing"`
- `api_key = "test-bybit-key"`
- `timestamp = "1685000000000"`
- `recv_window = "5000"`
- `payload = "category=spot&symbol=BTCUSDT"` (GET) or `'{"category":"spot","symbol":"BTCUSDT","side":"Buy","orderType":"Limit","qty":"0.001","price":"30000"}'` (POST)
- `sign = hmac.new(secret.encode(), (timestamp + api_key + recv_window + payload).encode(), hashlib.sha256).hexdigest()`

### KuCoin REST Endpoints

KuCoin Futures v1 base URL: `https://api-futures.kucoin.com`
KuCoin Spot v1 base URL: `https://api.kucoin.com`

Relevant endpoints (implement only what's needed for this story — all private):
- `POST /api/v1/orders` — place order; body: `{clientOid, side, symbol, type, size, [price], [stop], [stopPrice]}`
- `DELETE /api/v1/orders/{orderId}` — cancel by exchange order ID
- `GET /api/v1/openOrders` — open orders; params: `{symbol}`

Response format: `{"code": "200000", "data": {...}}` — success when `code == "200000"`. A non-200000 code is a business-level error (e.g., insufficient margin) — treat as rejection, raise `ExchangeRESTError` immediately (these are not 5xx, so do NOT retry).

### Bybit REST Endpoints

Bybit v5 base URL: `https://api.bybit.com`

Relevant endpoints:
- `POST /v5/order/create` — place order; body JSON: `{category, symbol, side, orderType, qty, [price], [orderLinkId]}`
- `POST /v5/order/cancel` — cancel; body JSON: `{category, symbol, orderId}`
- `GET /v5/order/realtime` — open orders; params: `category=spot&symbol=BTCUSDT`

Response format: `{"retCode": 0, "retMsg": "OK", "result": {...}}` — success when `retCode == 0`. Non-zero `retCode` is a business-level error — raise `ExchangeRESTError` immediately.

### Retry Pattern

```python
import asyncio
import httpx

_RETRY_DELAYS = [1.0, 2.0, 4.0]  # seconds

async def _request_with_retry(method: str, url: str, headers: dict[str, str], **kwargs: Any) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt, delay in enumerate([0.0] + _RETRY_DELAYS):
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0)) as client:
                resp = await client.request(method, url, headers=headers, **kwargs)
            if resp.status_code >= 500:
                last_exc = ExchangeRESTError(
                    f"HTTP {resp.status_code}: {resp.text[:200]}"  # truncated, no credentials
                )
                continue  # retry
            if resp.status_code >= 400:
                raise ExchangeRESTError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return resp
        except httpx.HTTPError as exc:
            last_exc = ExchangeRESTError(str(exc)[:200])  # truncate
    raise last_exc or ExchangeRESTError("exhausted retries")
```

**Critical:** `resp.text[:200]` truncates the body. But that truncated text must ALSO be scrubbed for credentials (the response body could contain an echoed header or key). Add a `_scrub_for_error(text: str) -> str` helper that replaces any known credential substring with `[REDACTED]` — reuse the same regex approach from `config.py`'s `redact_credentials`.

### Credential Safety Pattern

```python
from __future__ import annotations

from bot_service.config import get_settings

class KuCoinRESTClient:
    def _signed_headers(self, method: str, path: str, body_str: str) -> dict[str, str]:
        settings = get_settings()
        # get_secret_value() called once, used, local variable does not escape this scope
        secret = settings.kucoin_api_secret.get_secret_value()
        passphrase = settings.kucoin_api_passphrase.get_secret_value()
        api_key = settings.kucoin_api_key.get_secret_value()
        timestamp = str(int(time.time() * 1000))
        sign = _sign_kucoin(secret, timestamp, method, path, body_str)
        signed_pass = _sign_passphrase(secret, passphrase)
        return {
            "KC-API-KEY": api_key,
            "KC-API-SIGN": sign,
            "KC-API-TIMESTAMP": timestamp,
            "KC-API-PASSPHRASE": signed_pass,
            "KC-API-KEY-VERSION": "2",
            "Content-Type": "application/json",
        }
        # secret, passphrase, api_key go out of scope here — not stored on self
```

**Never** store `get_secret_value()` result on `self` or as a module-level variable. Call it fresh at sign-time.

### place_order() Method Signature

Both clients must satisfy the `ExchangeClient` protocol. Suggested signature:

```python
async def place_order(self, req: OrderRequest) -> PlacedOrder:
    body = {
        "clientOid": req.client_order_id or str(uuid.uuid4()),
        "side": req.side,
        "symbol": req.symbol,
        "type": req.order_type,
        "size": str(req.size),
    }
    if req.limit_price is not None:
        body["price"] = str(req.limit_price)
    headers = self._signed_headers("POST", "/api/v1/orders", json.dumps(body))
    resp = await self._request("POST", "/api/v1/orders", json=body, headers=headers)
    data = resp.json()["data"]
    return PlacedOrder(
        order_id=data["orderId"],
        client_order_id=req.client_order_id,
        status="placed",
        ts_exchange=int(time.time() * 1000),
    )
```

### Async Pattern — httpx.AsyncClient Usage

```python
# CORRECT: fresh client per request (no shared session)
async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0)) as client:
    resp = await client.post(url, json=body, headers=headers)

# WRONG: do NOT share a client across requests
self._client = httpx.AsyncClient()  # forbidden
```

This matches `project-context.md` § Concurrency Model: "Never block the event loop on a REST call... `async with httpx.AsyncClient(timeout=10.0) as client:`"

### L1 Test Pattern

Tests must not make real network calls. Use `pytest-httpx` or `unittest.mock.AsyncMock` to mock `httpx.AsyncClient`. Preferred approach: `pytest-httpx` with `httpx_mock` fixture (add `pytest-httpx` to `requirements-dev.txt`).

```python
import pytest
from pytest_httpx import HTTPXMock
from bot_service.exchange.kucoin.rest import KuCoinRESTClient, _sign_kucoin, _sign_passphrase

@pytest.mark.l1
def test_kucoin_sign_vector() -> None:
    secret = "c5c895a3-5c4b-4c33-b73b-d82b9f1a7b3f"
    timestamp = "1685000000000"
    method = "POST"
    path = "/api/v1/orders"
    body = '{"clientOid":"abc","side":"buy"}'
    result = _sign_kucoin(secret, timestamp, method, path, body)
    # Pre-computed expected value:
    import base64, hashlib, hmac as hmac_mod
    expected = base64.b64encode(
        hmac_mod.new(secret.encode(), (timestamp + method + path + body).encode(), hashlib.sha256).digest()
    ).decode()
    assert result == expected


@pytest.mark.l1
def test_bybit_sign_vector() -> None:
    from bot_service.exchange.bybit.rest import _sign_bybit
    secret = "fake-bybit-secret-for-testing"
    api_key = "test-bybit-key"
    timestamp = "1685000000000"
    recv_window = "5000"
    payload = "category=spot&symbol=BTCUSDT"
    result = _sign_bybit(secret, timestamp, api_key, recv_window, payload)
    import hashlib, hmac as hmac_mod
    expected = hmac_mod.new(
        secret.encode(),
        (timestamp + api_key + recv_window + payload).encode(),
        hashlib.sha256,
    ).hexdigest()
    assert result == expected


@pytest.mark.l1
async def test_kucoin_5xx_retries_then_raises(httpx_mock: HTTPXMock) -> None:
    # Mock 3 consecutive 500 responses
    for _ in range(4):  # first attempt + 3 retries
        httpx_mock.add_response(status_code=500, text="Internal Server Error")
    client = KuCoinRESTClient()
    from bot_service.exchange import ExchangeRESTError
    with pytest.raises(ExchangeRESTError):
        await client._request("GET", "/api/v1/openOrders", params={"symbol": "BTC-USDT"})


@pytest.mark.l1
async def test_kucoin_4xx_raises_immediately(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=400, text="Bad Request")
    client = KuCoinRESTClient()
    from bot_service.exchange import ExchangeRESTError
    with pytest.raises(ExchangeRESTError):
        await client._request("GET", "/api/v1/openOrders", params={"symbol": "BTC-USDT"})
    # Only one request should have been made (no retry)
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.l1
def test_no_credential_in_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from bot_service.exchange import ExchangeRESTError
    # ExchangeRESTError message should never contain raw secret values
    secret = "c5c895a3-5c4b-4c33-b73b-d82b9f1a7b3f"
    err = ExchangeRESTError(f"HTTP 400: some body text (no secret here)")
    assert secret not in str(err)
```

**Note on async tests:** `pyproject.toml` already has `asyncio_mode = "auto"` — all `async def test_*` functions are automatically treated as async tests.

### What Already Exists

- `bot_service/exchange/__init__.py` — currently has only `from __future__ import annotations`; UPDATE to add `ExchangeClient` protocol, `ExchangeRESTError`, `OrderRequest`, `PlacedOrder`, `OpenOrder`
- `bot_service/config.py` — `Settings` with all 5 credentials as `SecretStr`; `get_settings()` singleton
- `tests/conftest.py` — sets all credential env vars; `clear_settings_cache` autouse fixture
- `bot_service/metrics/prometheus.py` — `get_registry()` + lazy counter/gauge pattern; DO NOT modify
- `pyproject.toml` — `asyncio_mode = "auto"`, `strict = true` mypy, marks l1/l2/l3/l4 registered

### Dependencies to Add

Add to `requirements-dev.txt`:
```
pytest-httpx>=0.30.0
```

Also fix the missing entries from Epic 11 completion notes:
```
pandas>=2.2.0
httpx>=0.27.0
```
(These are runtime deps already in `requirements.txt` but missing from `requirements-dev.txt`.)

`httpx` is already in `requirements.txt` — the `KuCoinRESTClient` and `BybitRESTClient` use it for async requests; no new runtime dep is needed.

`uuid` is part of the Python standard library — no new dep.

### Critical Constraints

- **Never store `get_secret_value()` on `self`** — call it at sign-time only, let it go out of scope
- **Never use ccxt** — project-context.md explicitly forbids it; use direct httpx calls
- **Fresh AsyncClient per request** — do NOT share a client across calls; `async with httpx.AsyncClient()` pattern required
- **5xx → retry; 4xx → immediate raise** — no retry on client errors
- **Business-level errors** (KuCoin `code != "200000"`, Bybit `retCode != 0`) are not HTTP errors — raise `ExchangeRESTError` immediately with the error code and message (truncated/scrubbed)
- **`from __future__ import annotations` first line** in every `.py` file
- **No `Optional`, no `Union`** — use `X | None`, `X | Y`
- **mypy --strict zero errors** — run `make typecheck` before marking done
- **Passphrase signing (KuCoin v2):** the `KC-API-PASSPHRASE` header is NOT the raw passphrase — it must be `base64(HMAC-SHA256(secret, passphrase))`; sending the raw passphrase is v1 behaviour and will be rejected

### References

- [Source: _bmad-output/planning-artifacts/epics-bot.md § Story 12.1]
- [Source: bot-service/project-context.md § Technology Stack — httpx pattern]
- [Source: bot-service/project-context.md § Python Code Standards]
- [Source: bot-service/project-context.md § Concurrency Model — async with httpx.AsyncClient]
- [Source: bot_service/config.py — Settings with SecretStr credentials]
- [Source: tests/conftest.py — credential env vars for tests]

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- `pytest-httpx` 0.36.2 installed (spec said >=0.30.0); API matches — `httpx_mock.add_response()` and `httpx_mock.get_requests()` work as expected.
- Added `[[tool.mypy.overrides]]` for `pandas` module (`ignore_missing_imports = true`) — pandas has no bundled stubs; previously worked because mypy wasn't checking the pandas import path. Now 22 source files pass with zero mypy errors.
- Added `pandas>=2.2.0` and `httpx>=0.27.0` to `requirements-dev.txt` (were in `requirements.txt` only; flagged in Epic 11 retro).
- `_scrub_for_error` in `exchange/__init__.py` calls `get_settings()` lazily — `conftest.py` already sets fake credential env vars for all tests, so scrubbing works correctly in L1 tests.
- KuCoin passphrase signing (v2): `KC-API-PASSPHRASE` header is `base64(HMAC-SHA256(secret, passphrase))` not the raw passphrase. Verified with dedicated L1 test.
- Bybit signature is hex-encoded (not base64); dedicated L1 test verifies length==64 and hex character set.
- 50/50 tests pass (39 L1 + 11 L2), zero regressions.

### File List

- bot_service/exchange/__init__.py (UPDATE)
- bot_service/exchange/kucoin/__init__.py (NEW)
- bot_service/exchange/kucoin/rest.py (NEW)
- bot_service/exchange/bybit/__init__.py (NEW)
- bot_service/exchange/bybit/rest.py (NEW)
- tests/test_exchange_rest.py (NEW)
- requirements-dev.txt (UPDATE — added pytest-httpx, pandas, httpx)
- pyproject.toml (UPDATE — added pandas mypy override)

## Senior Developer Review (AI)

**Review Date:** 2026-05-10
**Outcome:** Changes Requested → All Resolved

### Action Items

- [x] **[HIGH] P1** — `kucoin/rest.py` + `bybit/rest.py`: Move `_signed_headers()` inside retry loop — stale timestamp after sleep causes 401 on 2nd/3rd retry (exceeds ±5s window)
- [x] **[HIGH] P2** — `kucoin/rest.py` `place_order`: Stop direction inverted — `"down" if buy else "up"` should be `"up" if buy else "down"` (KuCoin "up" fires when price rises)
- [x] **[MED] P3** — `kucoin/rest.py` + `bybit/rest.py`: Pass sorted params dict to httpx so serialized query string matches signed query string
- [x] **[MED] P4** — Both `rest.py`: Scrub `error=` field in `log.warning` — `error=str(exc)` → `error=_scrub_for_error(str(exc)[:200])`
- [x] **[MED] P9** — `tests/test_exchange_rest.py`: Add credential scrubbing test on 4xx path (only 5xx was covered)
- [x] **[MED] P10** — `tests/test_exchange_rest.py`: Hardcode expected values in sign vector tests (self-referential computation proves nothing)
- [x] **[MED] P11** — `kucoin/rest.py` `cancel_order`: URL-encode `order_id` with `urllib.parse.quote(order_id, safe="")`
- [x] **[LOW] P5** — `exchange/__init__.py`: Remove unused imports `json`, `re`, `field`
- [x] **[LOW] P6** — Both `rest.py`: Scrub before truncate — `_scrub_for_error(text[:200])` → `_scrub_for_error(text)[:200]`
- [x] **[LOW] P7** — `kucoin/rest.py` `get_open_orders`: Validate `data` is list before iterating
- [x] **[LOW] P8** — Both `rest.py`: Fix docstring "3 attempts" → "1 initial + 3 retries (4 total)"

### Deferred

- D1: Bybit `category` hardcoded to `"spot"` — needs `market_type` in `OrderRequest`
- D2: `ts_exchange` from local clock instead of exchange response timestamp
- D3: `AsyncClient` created per retry iteration instead of per request — minor but noted
- D4: `cancel_order` `symbol` unused in KuCoin (API doesn't need it for DELETE by ID)

## Review Follow-ups (AI)

- [x] [AI-Review] P1 (HIGH): Move signed headers inside retry loop — kucoin/rest.py + bybit/rest.py
- [x] [AI-Review] P2 (HIGH): Fix stop direction inversion — kucoin/rest.py place_order
- [x] [AI-Review] P3 (MED): Sort params passed to httpx — kucoin/rest.py + bybit/rest.py
- [x] [AI-Review] P4 (MED): Scrub log.warning error field — both rest.py
- [x] [AI-Review] P5 (LOW): Remove unused imports — exchange/__init__.py
- [x] [AI-Review] P6 (LOW): Scrub before truncate — both rest.py
- [x] [AI-Review] P7 (LOW): Validate data is list — kucoin get_open_orders
- [x] [AI-Review] P8 (LOW): Fix docstring — both rest.py
- [x] [AI-Review] P9 (MED): Add 4xx credential scrubbing test
- [x] [AI-Review] P10 (MED): Hardcode sign vector expected values
- [x] [AI-Review] P11 (MED): URL-encode order_id in cancel_order
