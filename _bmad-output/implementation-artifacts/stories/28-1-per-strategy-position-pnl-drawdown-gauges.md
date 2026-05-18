---
id: 28-1
title: Per-strategy position/P&L/drawdown Prometheus gauges
epic: 28
status: ready-for-dev
---

# Story 28-1: Per-strategy position/P&L/drawdown Prometheus gauges

## Context

Fixes D-16-1-1. `set_position_size`, `set_unrealized_pnl`, and `set_drawdown` are defined in `bot_service/metrics/prometheus.py` but never called anywhere in production code. As a result the Grafana per-strategy panels for position size, unrealized P&L, and drawdown always show zero.

The cost-basis state already lives in `order_worker.py` (`_position_qty`, `_position_avg_price`, `_position_notional`). This story wires the gauge updates into `handle_fill` after the position state is updated.

## What to build

### `bot_service/strategy/order_worker.py`

In `handle_fill`, after `realized_pnl = self._update_position(...)` and updating `_position_notional`, add gauge updates:

```python
from bot_service.metrics.prometheus import set_position_size, set_unrealized_pnl, set_drawdown

# after position update
pos_qty = self._position_qty.get(fill.symbol, 0.0)
pos_notional = self._position_notional.get(fill.symbol, 0.0)
avg_price = self._position_avg_price.get(fill.symbol, 0.0)
unrealized = pos_qty * (fill.fill_price - avg_price) if pos_qty > 0 and avg_price > 0 else 0.0

set_position_size(self._strategy_name, fill.symbol, pos_qty)
set_unrealized_pnl(self._strategy_name, fill.symbol, unrealized)
```

For drawdown: track `_peak_notional: float = 0.0` on the worker and compute drawdown from cumulative P&L:

```python
self._cumulative_pnl += realized_pnl
if self._cumulative_pnl > self._peak_pnl:
    self._peak_pnl = self._cumulative_pnl
drawdown = (self._peak_pnl - self._cumulative_pnl) / self._peak_pnl if self._peak_pnl > 0 else 0.0
set_drawdown(self._strategy_name, drawdown)
```

`OrderQueueWorker.__init__` needs:
- `self._strategy_name: str` — accept as constructor parameter (already present? check)
- `self._cumulative_pnl: float = 0.0`
- `self._peak_pnl: float = 0.0`

Check `OrderQueueWorker.__init__` signature and add `strategy_name: str` if absent.

## Acceptance Criteria

- After a buy fill at 50000 qty=1.0, `set_position_size` called with symbol and qty=1.0.
- After a sell fill at 54000 qty=1.0 (realized_pnl=4000), `set_position_size` called with qty=0.0, `set_unrealized_pnl` called with 0.0, `set_drawdown` called with 0.0 (peak=4000, current=4000).
- After a second sell fill at 46000 (realized_pnl=-4000 from fresh buy at 50000), drawdown = (4000 - 0) / 4000 = 1.0 — NOT: use scenario where peak > current.
- Unit tests: mock the three gauge setters; assert calls after a buy fill and after a sell fill.
- No change to `handle_fill`'s ILP write path or circuit breaker.

## Files
- `bot-service/bot_service/strategy/order_worker.py`
- `bot-service/tests/test_order_worker.py` (extend)

### Review Findings

- [x] [Review][Patch] Drawdown=1.0 spec AC scenario not covered by tests — added `test_drawdown_equals_one_when_all_gains_lost` [`tests/test_order_worker.py`]
- [x] [Review][Defer] Gauge state reset on worker restart discards `_cumulative_pnl`/`_peak_pnl` — architectural limitation; restore_position only restores qty/avg_price, not P&L history. Pre-existing design constraint.
- [x] [Review][Defer] `fill.fill_price` used as mark price for unrealized P&L — deliberate approximation; true mark-to-market would require a live price feed.
