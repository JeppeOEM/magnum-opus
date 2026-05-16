---
id: 26-4
title: Exchange timestamp from response body
epic: 26
status: ready-for-dev
---

# Story 26-4: Exchange timestamp from response body

## Context

**D2 from 12-1** — `PlacedOrder.ts_exchange` is populated with `int(time.time() * 1000)` in both KuCoin and Bybit `place_order`. It should use the exchange-returned timestamp (`createdAt` for KuCoin, `createdTime` for Bybit).

## What to build

### `bot_service/exchange/kucoin/rest.py` — `place_order`

KuCoin `/api/v1/orders` POST returns `{"orderId": "..."}` with no timestamp. Use `createdAt` from the GET open-orders endpoint if immediately available, otherwise keep `int(time.time() * 1000)` as fallback and log `"kucoin_ts_exchange_fallback_to_local_clock"`.

Actually KuCoin POST /orders does not return `createdAt`. The correct approach: accept the local-clock fallback but add a `# ts_exchange: KuCoin POST /orders does not return createdAt; using local clock` comment removing the deferred item.

### `bot_service/exchange/bybit/rest.py` — `place_order`

Bybit `/v5/order/create` returns `{"result": {"orderId": "...", "orderLinkId": "..."}}` — also no timestamp in the POST response. Same approach: accept local clock, add clarifying comment.

**Revised scope:** Since neither exchange returns the creation timestamp in the POST response, the correct fix is:
1. Document the limitation with a comment in both `place_order` methods.
2. Remove D2 from the deferred list (it was a misread — the endpoints don't expose this).
3. In `get_open_orders`, the `createdAt`/`createdTime` field is already parsed into `ts_placed` — this is correct.

## What to build (revised)

Add comments in both `place_order` methods:
```python
# ts_exchange uses local clock: KuCoin POST /api/v1/orders does not return createdAt.
# Fill timestamps (ts_exchange on OrderFilled) come from the WS feed or fills endpoint.
ts_exchange=int(time.time() * 1000),
```

Remove D2 from deferred-work.md (12-1 section) as "resolved by documentation".

## Acceptance Criteria

- Both `place_order` methods have the explanatory comment.
- `deferred-work.md` D2 from 12-1 is struck through / noted as resolved.
- No behavior changes — this is a doc-only story for this specific deferred item.

## Files
- `bot-service/bot_service/exchange/kucoin/rest.py`
- `bot-service/bot_service/exchange/bybit/rest.py`
- `_bmad-output/implementation-artifacts/deferred-work.md`
