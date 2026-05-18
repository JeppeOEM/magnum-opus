---
id: 28-3
title: REST fills pagination for KuCoin and Bybit
epic: 28
status: ready-for-dev
---

# Story 28-3: REST fills pagination for KuCoin and Bybit

## Context

Fixes D-26-1 and D-26-2. Both `KuCoinRESTClient.get_recent_fills` and `BybitRESTClient.get_recent_fills` return at most 50 fills (one page). During a long WebSocket outage with >50 fills the excess fills are silently dropped.

## What to build

### `bot_service/exchange/kucoin/rest.py` — `get_recent_fills`

KuCoin `/api/v1/fills` supports cursor-based pagination via `currentPage` + `pageSize`. The response has `data.totalPage` and `data.currentPage` fields.

Replace the single-page fetch with a paginating loop:

```python
async def get_recent_fills(self, symbol: str, since_ms: int) -> list[OrderFilled]:
    fills: list[OrderFilled] = []
    page = 1
    while True:
        data = await self._request(
            "GET", "/api/v1/fills",
            params={"symbol": symbol, "startAt": since_ms, "pageSize": 50, "currentPage": page}
        )
        items = data.get("items", [])
        for item in items:
            fill = self._parse_fill(item)
            if fill:
                fills.append(fill)
        total_pages = int(data.get("totalPage", 1))
        if page >= total_pages or not items:
            break
        page += 1
    return fills
```

### `bot_service/exchange/bybit/rest.py` — `get_recent_fills`

Bybit `/v5/order/history` returns a `nextPageCursor` string in the response. Loop while cursor is non-empty:

```python
async def get_recent_fills(self, symbol: str, since_ms: int) -> list[OrderFilled]:
    fills: list[OrderFilled] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {
            "category": "spot",
            "symbol": symbol,
            "startTime": since_ms,
            "limit": 50,
        }
        if cursor:
            params["cursor"] = cursor
        data = await self._request("GET", "/v5/order/history", params=params)
        items = data.get("list", [])
        for item in items:
            fill = self._parse_fill(item)
            if fill:
                fills.append(fill)
        cursor = data.get("nextPageCursor") or None
        if not cursor or not items:
            break
    return fills
```

## Acceptance Criteria

- KuCoin: if `totalPage=3` in response, all three pages are fetched and merged into a single list.
- Bybit: if `nextPageCursor` is non-empty, subsequent pages are fetched; stops when cursor is empty or items empty.
- Empty first page returns empty list (no loop).
- Single page (totalPage=1 for KuCoin, empty cursor for Bybit) fetches exactly one page.
- Unit tests for both clients: mock `_request` to return multi-page response and assert all fills returned.
- Existing single-page tests still pass.

## Files
- `bot-service/bot_service/exchange/kucoin/rest.py`
- `bot-service/bot_service/exchange/bybit/rest.py`
- `bot-service/tests/test_exchange_rest.py` (extend)

### Review Findings

- [x] [Review][Patch] KuCoin unbounded pagination loop — no max-page guard; added `_MAX_PAGES=100` bound [`kucoin/rest.py`]
- [x] [Review][Patch] KuCoin `int(result.get("totalPage", 1))` crashes on `null` API response — changed to `int(float(result.get("totalPage") or 1))` [`kucoin/rest.py`]
- [x] [Review][Patch] Bybit unbounded pagination loop — no max-page guard; changed to `for _ in range(_MAX_PAGES)` [`bybit/rest.py`]
- [x] [Review][Patch] Bybit cursor whitespace not stripped — `.or None` doesn't catch `" "` cursor; changed to `(... or "").strip() or None` [`bybit/rest.py`]
- [x] [Review][Defer] Bybit `PartiallyFilled` orders silently discarded — pre-existing behaviour, no change in this epic
- [x] [Review][Defer] KuCoin futures endpoint (`api-futures.kucoin.com`) for spot fills — pre-existing, out of scope
