---
id: 25-3
title: Backtest realized P&L computation
epic: 25
status: ready-for-dev
---

# Story 25-3: Backtest realized P&L computation

## Context

Third part of D-14-5-2. `bot_service/backtest/writer.py` always writes `realized_pnl=0.0`. Backtrader `notify_order` provides `order.executed.price` and `order.executed.size`; P&L can be computed from these using the same FIFO cost-basis logic as 25-1.

## What to build

### `bot_service/backtest/writer.py`

Add a `_CostBasisTracker` (same algorithm as `_update_position` in order_worker) inside the writer:

```python
class _CostBasisTracker:
    def __init__(self) -> None:
        self._qty: dict[str, float] = {}
        self._avg: dict[str, float] = {}

    def record(self, symbol: str, side: str, qty: float, price: float) -> float:
        """Returns realized_pnl for this fill (0 for opening fills)."""
        ...  # same FIFO logic as order_worker
```

In `BacktestResultWriter.__init__`, create `self._tracker = _CostBasisTracker()`.

In `write_fill` (or wherever `notify_order` maps fills to ILP writes), replace `"realized_pnl": 0.0` with:
```python
side = "buy" if order.isbuy() else "sell"
realized_pnl = self._tracker.record(symbol, side, qty, price)
```

## Acceptance Criteria

- A backtest run where a buy at 100 is followed by a sell at 110 writes `realized_pnl=10*qty`.
- A buy at 100 then sell at 90 writes `realized_pnl=-10*qty`.
- Opening buy writes `realized_pnl=0.0`.
- Unit tests cover the tracker logic independent of Backtrader.

## Files
- `bot-service/bot_service/backtest/writer.py`
- `bot-service/tests/backtest/test_writer.py` (create or extend)
