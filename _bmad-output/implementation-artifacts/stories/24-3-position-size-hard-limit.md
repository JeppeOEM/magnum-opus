---
id: 24-3
title: Position size hard limit enforcement
epic: 24
status: ready-for-dev
---

# Story 24-3: Position size hard limit enforcement

## Context

`OrderQueueWorker._exceeds_risk_gate` blocks orders that push notional above `max_position_pct * portfolio_value`. But for market orders, `_order_notional` uses `size * portfolio_value_usd` where `size` is a portfolio fraction (0.05 = 5%). This means two simultaneous strategies both at 5% could each pass the gate individually before either fill updates `_position_notional`.

Additionally, there is no absolute USD cap — only a relative fraction. If `portfolio_value_usd` is misconfigured high, large orders go through unchecked.

## What to build

### `bot_service/config.py`

Add `max_order_notional_usd: float = 0.0`. When `0.0`, no absolute cap (disabled).

### `bot_service/strategy/order_worker.py`

In `_exceeds_risk_gate`, add a second check:
```python
if self._max_order_notional_usd > 0:
    if projected_notional > self._max_order_notional_usd:
        return True
```

Log the block reason separately: `"order_hard_limit_blocked"`.

Add `max_order_notional_usd: float = 0.0` constructor parameter; store as `self._max_order_notional_usd`.

### `bot_service/main.py`

Pass `max_order_notional_usd=settings.max_order_notional_usd` when constructing `OrderQueueWorker`.

## Acceptance Criteria

- With `max_order_notional_usd=500`, an order projecting $600 notional is blocked with `"order_hard_limit_blocked"` log.
- With `max_order_notional_usd=0` (disabled), only the existing percentage gate applies.
- Exit/stop orders are never blocked by the absolute cap (same exemption as existing gate).
- Unit tests: cap blocks oversized order, cap disabled passes same order, exit order exempt.

## Files
- `bot-service/bot_service/config.py`
- `bot-service/bot_service/strategy/order_worker.py`
- `bot-service/bot_service/main.py`
- `bot-service/tests/test_order_worker.py` (extend)
