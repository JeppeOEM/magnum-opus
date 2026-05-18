---
id: 28-2
title: Market order notional risk gate fix
epic: 28
status: ready-for-dev
---

# Story 28-2: Market order notional risk gate fix

## Context

Fixes D-15-2-3. In `order_worker.py`, the pre-order notional check is:

```python
projected_notional = req.size * (req.limit_price or 0.0)
```

For market orders `limit_price` is `None`, so `projected_notional` is always `0.0` and the check `projected_notional + current > max_notional` is always false. Market orders bypass all notional-based risk checks entirely.

## What to build

### `bot_service/strategy/order_worker.py`

Locate the notional guard in `post()` (or wherever the risk gate runs before accepting an order request). Replace the projected notional calculation:

```python
# old — broken for market orders
projected_notional = req.size * (req.limit_price or 0.0)

# new — use limit_price when present; fall back to last known mid-price if None
if req.limit_price is not None:
    ref_price = req.limit_price
else:
    # market order: use last known fill price for this symbol, or 0 if unknown
    ref_price = self._last_fill_price.get(req.symbol, 0.0)
projected_notional = req.size * ref_price
```

Track `_last_fill_price: dict[str, float]` — update it in `handle_fill` with `fill.fill_price`.

If `ref_price == 0.0` (no prior fill and market order), the projected_notional remains 0.0 and the check is still imprecise. In that case, log WARN with `"market_order_no_ref_price"` and allow the order through (this is the existing behaviour for first-ever market order — we do not block it, we make it observable).

## Acceptance Criteria

- OrderRequest with `limit_price=None` and `size=1.0` is now tested against last known fill price.
- After a prior fill at 50000, a market order for 1.0 → `projected_notional=50000`; if that would exceed `max_position_pct * starting_capital`, the order is rejected.
- With no prior fill price, market order is allowed through with WARN log `market_order_no_ref_price`.
- Unit tests: (1) market order rejected when prior fill makes notional exceed limit, (2) market order allowed on first use (no prior fill), (3) limit order rejection unchanged.

## Files
- `bot-service/bot_service/strategy/order_worker.py`
- `bot-service/tests/test_order_worker.py` (extend)
