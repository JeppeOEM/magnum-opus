---
id: 26-5
title: Bybit auth failure escalation and KuCoin public WS token method
epic: 26
status: ready-for-dev
---

# Story 26-5: Bybit auth failure escalation and KuCoin public WS token method

## Context

**D5 from 12-2** — `bybit/ws_private.py` retries auth failures forever with no ERROR escalation after N attempts. A bad API key loops silently.

**D3 from 12-2** — `kucoin/ws_private.py._get_ws_token` calls `self._rest._request(...)` directly, tightly coupling to REST client internals. Should use a public method.

## What to build

### `bot_service/exchange/bybit/ws_private.py`

Add `_auth_failure_count: int = 0` and `_MAX_AUTH_FAILURES: int = 5` (class constant).

In the auth-response handler, on failure:
```python
self._auth_failure_count += 1
if self._auth_failure_count >= _MAX_AUTH_FAILURES:
    log.critical(
        "bybit_ws_auth_max_failures",
        failures=self._auth_failure_count,
    )
    # Stop retrying — raise to let caller decide
    raise RuntimeError(f"Bybit WS auth failed {self._auth_failure_count} times")
```

Reset `_auth_failure_count = 0` on successful auth.

### `bot_service/exchange/kucoin/rest.py`

Add public method:
```python
async def get_private_ws_token(self) -> tuple[str, str]:
    """Return (token, endpoint) for the private WebSocket connection."""
    data = await self._request("POST", "/api/v1/bullet-private")
    token = data["data"]["token"]
    server = data["data"]["instanceServers"][0]
    return token, server["endpoint"]
```

### `bot_service/exchange/kucoin/ws_private.py`

Replace the direct `self._rest._request(...)` call in `_get_ws_token` with `await self._rest.get_private_ws_token()`.

## Acceptance Criteria

- Bybit: after `_MAX_AUTH_FAILURES` consecutive auth failures, `"bybit_ws_auth_max_failures"` is logged at CRITICAL and a RuntimeError is raised.
- Bybit: a successful auth resets the counter.
- KuCoin: `ws_private._get_ws_token` calls `self._rest.get_private_ws_token()` (no direct `_request`).
- Unit tests: Bybit escalation fires at exactly N failures; KuCoin token method called through public API.

## Files
- `bot-service/bot_service/exchange/bybit/ws_private.py`
- `bot-service/bot_service/exchange/kucoin/rest.py`
- `bot-service/bot_service/exchange/kucoin/ws_private.py`
- `bot-service/tests/exchange/test_bybit_ws_private.py` (extend)
- `bot-service/tests/exchange/test_kucoin_ws_private.py` (extend)
