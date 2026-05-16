---
id: 26-3
title: REST fallback fills endpoint for KuCoin and Bybit
epic: 26
status: ready-for-dev
---

# Story 26-3: REST fallback fills endpoint for KuCoin and Bybit

## Context

**D1 from 12-2** — `get_open_orders()` is used for REST fallback but it only returns open orders, never filled ones. A fill that arrived during WS downtime is permanently missed.

**D2 from 12-2** — `ts_placed` is used instead of fill time in the REST fallback sort; this is resolved once fills endpoint is used.

## What to build

### `bot_service/exchange/__init__.py`

Add `get_recent_fills(symbol: str, since_ms: int) -> list[OrderFilled]` to `ExchangeClient` Protocol.

### `bot_service/exchange/kucoin/rest.py`

```python
async def get_recent_fills(self, symbol: str, since_ms: int) -> list[OrderFilled]:
    resp = await self._request("GET", "/api/v1/fills", params={"symbol": symbol, "startAt": since_ms})
    items = resp.get("data", {}).get("items", [])
    return [
        OrderFilled(
            order_id=item["orderId"],
            exchange="kucoin",
            symbol=item["symbol"],
            side=item["side"],
            fill_size=Decimal(item["size"]),
            fill_price=Decimal(item["price"]),
            fee=Decimal(item.get("fee", "0")),
            ts_exchange=int(item.get("createdAt", 0)),
        )
        for item in items
    ]
```

### `bot_service/exchange/bybit/rest.py`

```python
async def get_recent_fills(self, symbol: str, since_ms: int) -> list[OrderFilled]:
    resp = await self._request("GET", "/v5/order/history", params={"symbol": symbol, "startTime": since_ms, "orderStatus": "Filled"})
    items = resp.get("result", {}).get("list", [])
    return [
        OrderFilled(
            order_id=item["orderId"],
            exchange="bybit",
            symbol=item["symbol"],
            side=item["side"].lower(),
            fill_size=Decimal(item["cumExecQty"]),
            fill_price=Decimal(item["avgPrice"]),
            fee=Decimal(item.get("cumExecFee", "0")),
            ts_exchange=int(item.get("updatedTime", 0)),
        )
        for item in items
        if item.get("orderStatus") == "Filled"
    ]
```

### `bot_service/exchange/kucoin/ws_private.py` and `bybit/ws_private.py`

In the REST fallback path, replace `get_open_orders` with `get_recent_fills(symbol, since_ms=last_ws_ts)` where `last_ws_ts` is the timestamp of the last successfully received fill.

### `bot_service/exchange/paper.py`

Add a no-op `get_recent_fills` returning `[]`.

## Acceptance Criteria

- KuCoin `get_recent_fills` calls `/api/v1/fills` with correct symbol and startAt params.
- Bybit `get_recent_fills` calls `/v5/order/history` with symbol and startTime params.
- REST fallback uses fills endpoint, not open-orders.
- `PaperExchangeClient.get_recent_fills` returns `[]`.
- Unit tests: mock HTTP; assert correct endpoint and parameter mapping for each exchange.

## Files
- `bot-service/bot_service/exchange/__init__.py`
- `bot-service/bot_service/exchange/kucoin/rest.py`
- `bot-service/bot_service/exchange/bybit/rest.py`
- `bot-service/bot_service/exchange/kucoin/ws_private.py`
- `bot-service/bot_service/exchange/bybit/ws_private.py`
- `bot-service/bot_service/exchange/paper.py`
- `bot-service/tests/exchange/test_kucoin_rest.py` (extend)
- `bot-service/tests/exchange/test_bybit_rest.py` (extend)
