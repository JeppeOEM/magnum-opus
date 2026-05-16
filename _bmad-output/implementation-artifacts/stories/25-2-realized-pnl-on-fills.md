---
id: 25-2
title: Write computed realized_pnl to order_events on fills
epic: 25
status: ready-for-dev
---

# Story 25-2: Write computed realized_pnl to order_events on fills

## Context

Second part of D-14-5-2. Story 25-1 adds `_update_position` returning realized P&L. This story wires that value into the `_write_order_event` call so `order_events.realized_pnl` gets a real number.

## What to build

### `bot_service/strategy/order_worker.py` — `handle_fill`

Replace `realized_pnl=0.0` in the `handle_fill` ILP write with:

```python
realized_pnl = self._update_position(
    fill.symbol, fill.side, float(fill.fill_size), float(fill.fill_price)
)
```

Pass `realized_pnl=realized_pnl` to `_write_order_event`.

Also compute `slippage`:
```python
if req.limit_price and req.limit_price > 0:
    slippage = float(fill.fill_price - req.limit_price) * float(fill.fill_size)
else:
    slippage = 0.0
```

Pass `slippage=slippage` to `_write_order_event`.

### Circuit breaker integration (from 24-2)

After computing `realized_pnl`, pass it to the circuit breaker:
```python
if self._circuit_breaker:
    self._circuit_breaker.record_pnl(realized_pnl)
    if self._circuit_breaker.is_tripped():
        log.critical("daily_loss_limit_tripped", ...)
        if self._on_circuit_breaker_trip:
            self._on_circuit_breaker_trip()
```

## Acceptance Criteria

- A sell fill closing a profitable long position writes `realized_pnl > 0` to `order_events`.
- A sell fill at a loss writes `realized_pnl < 0`.
- An opening buy fill writes `realized_pnl == 0.0`.
- The circuit breaker receives the computed value (integration test with mock breaker).
- Unit tests: profitable close, loss close, opening fill, slippage computation.

## Files
- `bot-service/bot_service/strategy/order_worker.py`
- `bot-service/tests/test_order_worker.py` (extend)
