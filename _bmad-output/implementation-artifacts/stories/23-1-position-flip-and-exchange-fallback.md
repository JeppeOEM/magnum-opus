---
id: 23-1
title: Position flip logic and exchange fallback fix
epic: 23
status: ready-for-dev
---

# Story 23-1: Position flip logic and exchange fallback fix

## Context

Deferred items D-15-3-1 and D-15-3-2.

**D-15-3-1** — `MACrossBot._on_bar` posts `order_role="entry"` for both buy and sell signals without first closing an existing opposite-side position. On a buy→sell reversal, two entry orders coexist, producing incorrect P&L accounting.

**D-15-3-2** — `MACrossBot._on_bar` and `OFIBot._on_bar` call `self._exchange` directly; if the injection fails both bots log a warning and drop the order. No problem there. But the exchange attribute is already injected by the registry; the issue is it was previously documented as hardcoded to `"kucoin"` fallback — that code was never present, so D-15-3-2 is a false alarm at the bot level. However both bots lack a `_current_side` tracker to implement flip logic.

## What to build

### `strategies/active/ma_cross_bot.py`
1. Add `_current_side: str | None = None` instance attribute (init in `subscribe` or class body).
2. In `_on_bar`, before posting an order:
   - If `result.action == "buy"` and `self._current_side == "long"`: return early (already long).
   - If `result.action == "sell"` and `self._current_side == "short"`: return early (already short).
   - If `result.action == "buy"` and `self._current_side == "short"`: post a sell exit order first (`order_role="exit"`), then post the buy entry.
   - If `result.action == "sell"` and `self._current_side == "long"`: post a buy exit order first (`order_role="exit"`), then post the sell entry.
3. Update `_current_side` after posting: `"long"` on buy entry, `"short"` on sell entry, `None` on exit.
4. Reset `_current_side = None` in the gap handler path (override `handle_gap` or hook into `_signal_invalid` set).

### `strategies/active/ofi_bot.py`
Apply the same `_current_side` flip pattern (same logic, same fields).

## Acceptance Criteria

- A second buy signal while `_current_side == "long"` is silently dropped (no order posted).
- A sell signal while `_current_side == "long"` first posts a buy-exit, then a sell-entry.
- After a gap, `_current_side` resets to `None`.
- Both bots: tests cover (a) hold-when-already-in-position, (b) flip sequence, (c) gap-reset.

## Files
- `bot-service/strategies/active/ma_cross_bot.py`
- `bot-service/strategies/active/ofi_bot.py`
- `bot-service/tests/strategies/test_ma_cross_bot.py` (create or extend)
- `bot-service/tests/strategies/test_ofi_bot.py` (create or extend)
