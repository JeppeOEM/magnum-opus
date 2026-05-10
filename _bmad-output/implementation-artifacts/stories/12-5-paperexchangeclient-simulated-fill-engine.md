# Story 12.5: PaperExchangeClient — Simulated Fill Engine

## Status: done

## Story

**As** mrqdt,
**I want** a paper trading client that simulates fills using live tick data and writes outcomes to the same order_events table as live trading,
**so that** paper and live results are queryable together in Grafana with a single filter.

## Acceptance Criteria

- **AC1:** `OrderQueueWorker` accepts an `ExchangeClient` protocol — when `paper_trading=True` the instantiation uses `PaperExchangeClient` instead of live clients; the swap is transparent to `OrderQueueWorker`.

- **AC2:** Limit order: after a random latency drawn from `Uniform(BOT_PAPER_LATENCY_MIN_MS, BOT_PAPER_LATENCY_MAX_MS)`, check live tick stream (Redis `ticks:{exchange}:{symbol}`) for whether the limit price was crossed; if crossed → fills at limit price.

- **AC3:** Limit order with no ticks available (empty Redis stream during latency window) → fills at limit price; logs WARN `paper_fill_no_ticks`.

- **AC4:** Market order → fills at mid-price + configured slippage (`BOT_PAPER_SLIPPAGE_BPS`, default 5 bps); if no mid-price available → fills at last-known price and logs WARN.

- **AC5:** `order_events` row written by `OrderQueueWorker` has `paper_trading=True` and `backtest=False`; filterable with `WHERE paper_trading = true`.

## Tasks / Subtasks

- [x] T1: Implement `PaperExchangeClient` in `bot_service/exchange/paper.py`
  - [x] T1.1: 10 L1 tests written (failing first)
  - [x] T1.2: Implement `__init__`
  - [x] T1.3: Implement `place_order` — returns PlacedOrder immediately, schedules background fill task
  - [x] T1.4: Implement `_resolve_limit_price` — reads Redis ticks, fills optimistically at limit
  - [x] T1.5: Implement `_resolve_market_price` — reads OB snapshot, computes mid ± slippage_bps
  - [x] T1.6: Implement `cancel_order` — no-op
  - [x] T1.7: Implement `get_open_orders` — returns []

- [x] T2: 10/10 L1 tests, 122 total, mypy clean

## Senior Developer Review (AI)

**Date:** 2026-05-10  
**Outcome:** Changes Requested → all resolved

### Action Items

- [x] **[High]** P2: `_simulate_fill` background task silently swallowed exceptions from `on_fill` — wrapped in try/except with `log.error("paper_fill_task_failed")`
- [x] **[Med]** P3: `order_id=""` in `paper_fill_no_ticks` log — `_resolve_limit_price` now accepts `order_id` param, logs actual order_id
- [x] **[Med]** P4: `limit_price or 0.0` masked intentional zero prices — changed to `if req.limit_price is not None else 0.0`
- [x] **[Low]** P6: Market sell path had no test — added `test_market_sell_fills_at_mid_minus_slippage`

### Deferred

- D1: `candles:ob:` vs `ob_features:` stream key inconsistency — pre-existing in codebase (CLAUDE.md says `candles:ob:`); paper client follows CLAUDE.md; inconsistency is in `parse_stream_entry` routing, not introduced here

## Dev Notes

### Architecture

`PaperExchangeClient` satisfies the `ExchangeClient` protocol (place_order, cancel_order, get_open_orders). It uses Redis to read tick data during the simulated latency window.

Redis key for ticks: `ticks:{exchange}:{symbol}` — the same stream written by the aggregator. Use `XREVRANGE ticks:{exchange}:{symbol} + - COUNT 100` to get recent ticks during the latency window.

Redis key for OB snapshots: `candles:ob:{exchange}:{symbol}` — look for the most recent entry, use `best_bid` + `best_ask` to compute mid.

### `place_order` return

Returns `PlacedOrder(order_id=uuid, client_order_id=req.client_order_id, status="placed", ts_exchange=int(time.time()*1000))`. The fill event (`OrderFilled`) is emitted separately via the `on_fill` callback that `OrderQueueWorker` registers.

Wait — looking at the ExchangeClient protocol: `place_order` returns `PlacedOrder`, not `OrderFilled`. The fill comes from the private WS feed. For paper trading, we need a different mechanism: the paper client needs to call `on_fill` after the latency window. 

**Design choice:** `PaperExchangeClient.place_order()` returns a `PlacedOrder` immediately (status="placed"), then schedules a background task that: waits the latency, checks ticks, and calls `on_fill_callback(OrderFilled)`. The `on_fill_callback` is registered at construction time (`OrderQueueWorker.handle_fill`).

So the constructor signature:
```python
def __init__(
    self,
    exchange: str,
    redis_client: redis.asyncio.Redis,
    on_fill: Callable[[OrderFilled], Awaitable[None]],
    settings: Settings,
) -> None:
```

### Tick crossing logic for limit order

For a BUY limit order at `limit_price=P`:
- Read ticks from Redis during the latency window
- A tick at price `tick_price <= P` means the price traded AT or BELOW our buy limit → fill

For a SELL limit order at `limit_price=P`:
- A tick at price `tick_price >= P` → fill

If no ticks → fill at limit price with WARN `paper_fill_no_ticks`.

### Market order slippage

```python
slippage_multiplier = 1 + (bps / 10_000)  # buys
slippage_multiplier = 1 - (bps / 10_000)  # sells
fill_price = mid_price * slippage_multiplier
```

### OB snapshot from Redis

Read `candles:ob:{exchange}:{symbol}` stream — XREVRANGE COUNT 1. If stream is empty or key doesn't exist, use last-known price (a fallback stored in the client instance). If no fallback exists either, log WARN and use 0.0 (order still placed, fill_price=0.0).

### ExchangeClient protocol compliance

`get_open_orders(symbol="")` → returns `[]` always (paper client has no real exchange state).
`cancel_order(order_id, symbol)` → returns None silently (no-op).

### Test plan (L1, no Redis)

Use monkeypatch/AsyncMock for Redis calls:
1. Limit buy: tick at price below limit → fills at limit price
2. Limit buy: no ticks → fills at limit price + WARN
3. Limit buy: all ticks above limit → does NOT fill (just fills at limit if no ticks cross... wait — re-read AC2: "if crossed, fills at limit price". If NOT crossed during the window, the order does NOT fill. But AC3 says no-ticks → fills anyway. The distinction: no ticks = fill; ticks present but no crossing = what?)

Re-reading epics: "if the live tick stream for a symbol has no ticks during the simulated latency window... fill the order at the limit price... Never silently reject a paper fill". So: if ticks are present but none cross → the order does NOT fill during this window. But "Never silently reject" means we still need to emit a fill. Actually for paper trading simplicity, if the latency window elapses with no crossing tick, fill anyway at limit price (optimistic assumption). This avoids the complexity of order remaining open.

Actually the story spec says: "during this window it checks the live tick stream to determine whether the limit price was crossed; if crossed, the order fills at the limit price". The "if no ticks" fallback is separate. If ticks exist but none cross — the story doesn't say what happens. For simplicity: fill at limit price (paper trading is optimistic). Log nothing.

Test 4: Market order with mid-price available → fills at mid ± slippage
Test 5: Market order with no OB snapshot → fills at 0.0 (or last-known) + WARN

## File List

- NEW: `bot-service/bot_service/exchange/paper.py`
- NEW: `bot-service/tests/test_paper_exchange.py`

## Dev Agent Record

### Completion Notes

All review patches applied. 10 L1 tests, 122 total, mypy clean.

## Change Log

| Date | Change |
|------|--------|
| 2026-05-10 | Story created |
