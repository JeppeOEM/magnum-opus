---
id: 23-2
title: BaseStrategy TF-to-table routing and OFI size validation
epic: 23
status: ready-for-dev
---

# Story 23-2: BaseStrategy TF-to-table routing and OFI size validation

## Context

**D-15-3-3** — `BaseStrategy._query_questdb` always queries `snapshot_1s` regardless of the requested `tf`. `MACrossBot` requests `tf="1m"` but the query uses `snapshot_1s WHERE tf='1m'`. With Epic 21 having added `snapshot_1m` and `snapshot_15m`, 1m bars should be read from `snapshot_1m`.

**D-15-2-2** — `OFIBot` passes `size=self.max_position_pct` (0.05) as a portfolio fraction, but the risk gate in `OrderQueueWorker._order_notional` already handles this correctly for market orders: `size * portfolio_value_usd`. No change to order_worker needed; needs a validation that `max_position_pct` is ≤ 1.0 and a doc comment clarifying the contract.

## What to build

### `bot_service/strategy/base.py` — `_query_questdb`

Add a `_TF_TABLE` mapping:
```python
_TF_TABLE: dict[str, str] = {
    "1s": "snapshot_1s",
    "1m": "snapshot_1m",
    "5m": "snapshot_1m",   # aggregated on demand from 1m
    "15m": "snapshot_15m",
    "1h": "snapshot_15m",  # aggregated on demand from 15m
    "4h": "snapshot_15m",
    "1d": "snapshot_15m",
    "1w": "snapshot_15m",
}
```

In `_query_questdb`, derive `table = _TF_TABLE.get(tf, "snapshot_1s")` and use it in the FROM clause. Keep the `WHERE tf='{tf}'` filter so multi-TF tables still return only the requested rows.

### `bot_service/strategy/base.py` — `__init__` validation

Add: `if not (0 < self.max_position_pct <= 1.0): raise ValueError(...)`.

### `strategies/active/ofi_bot.py` — doc comment

Add a one-line comment above `size=self.max_position_pct` clarifying it is a portfolio fraction (0–1), not a coin quantity.

## Acceptance Criteria

- `get_history(symbol, "1m", n)` issues a query against `snapshot_1m`.
- `get_history(symbol, "1s", n)` issues a query against `snapshot_1s`.
- `get_history(symbol, "4h", n)` issues a query against `snapshot_15m`.
- Unknown TF falls back to `snapshot_1s`.
- `max_position_pct > 1.0` raises `ValueError` at construction time.
- Unit tests cover TF routing for at least 1s, 1m, 15m, and an unknown TF.

## Files
- `bot-service/bot_service/strategy/base.py`
- `bot-service/strategies/active/ofi_bot.py`
- `bot-service/tests/test_base_strategy.py` (create or extend)
