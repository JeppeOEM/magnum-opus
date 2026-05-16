---
id: 25-1
title: Cost basis tracking in OrderQueueWorker
epic: 25
status: ready-for-dev
---

# Story 25-1: Cost basis tracking in OrderQueueWorker

## Context

First part of fixing D-14-5-2. `realized_pnl` is always written as 0.0 because there is no open position cost basis tracking. This story adds the in-memory accounting layer; Story 25-2 uses it to compute and write real P&L.

## What to build

### `bot_service/strategy/order_worker.py`

Add `_position_avg_price: dict[str, float]` and `_position_qty: dict[str, float]` to track open cost basis per symbol.

In `handle_fill`, after removing from `open_orders`:

```python
def _update_position(self, symbol: str, side: str, qty: float, price: float) -> float:
    """Update position and return realized_pnl for this fill. Returns 0.0 for opening fills."""
    cur_qty = self._position_qty.get(symbol, 0.0)
    cur_avg = self._position_avg_price.get(symbol, 0.0)

    if side == "buy":
        new_qty = cur_qty + qty
        self._position_avg_price[symbol] = (cur_avg * cur_qty + price * qty) / new_qty if new_qty else 0.0
        self._position_qty[symbol] = new_qty
        return 0.0
    else:  # sell — closes long
        closed = min(qty, cur_qty)
        realized = closed * (price - cur_avg) if cur_avg > 0 else 0.0
        self._position_qty[symbol] = max(0.0, cur_qty - closed)
        if self._position_qty[symbol] == 0.0:
            self._position_avg_price[symbol] = 0.0
        return realized
```

Restore at reconciliation: `restore_open_order` already tracks notional; add `restore_position(symbol, qty, avg_price)` method for Epic 13 reconciliation path (can be no-op stub for now — reconciliation sets cost basis from QuestDB when D-13-1 is revisited).

## Acceptance Criteria

- Buy 1.0 BTC @ 50000 → `_position_qty["BTCUSDT"] == 1.0`, `_position_avg_price["BTCUSDT"] == 50000`.
- Buy 1.0 more @ 52000 → avg price = 51000, qty = 2.0.
- Sell 1.0 @ 54000 → realized = (54000 - 51000) * 1.0 = 3000.0; qty = 1.0.
- Sell remaining 1.0 @ 48000 → realized = (48000 - 51000) * 1.0 = -3000.0; qty = 0.0.
- Unit tests cover all four cases above.

## Files
- `bot-service/bot_service/strategy/order_worker.py`
- `bot-service/tests/test_order_worker.py` (extend)
