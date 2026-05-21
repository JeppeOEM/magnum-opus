---
id: 34-9
title: Exchange-native SL/TP — bracket orders at entry, layered exit mode, check_exit_conditions hook
epic: 34
status: ready-for-dev
---

# Story 34-9: Exchange-native SL/TP orders

## Context

Currently, stop-loss and take-profit logic lives entirely in the bot — it watches prices and sends close orders when conditions are met. If the bot crashes, restarts, or loses exchange connectivity, positions have no protection. This story adds exchange-native bracket orders placed at fill confirmation: the exchange holds the SL/TP and will execute them even if the bot is offline.

**Three modes:**

- `static` — SL/TP placed at entry using fixed percentages, never amended. Simplest.
- `dynamic` — SL/TP placed at entry, then amended as strategy updates them. For strategies that adjust exits frequently.
- `layered` — Wide exchange orders (circuit breaker) + bot-monitored `check_exit_conditions()` for precision exits. For micro-frequency strategies where amending the exchange order every bar would spam the API.

The exchange orders are the safety net. The bot's exit logic is the precision instrument.

## What to build

### `BaseStrategy` — new fields

`bot-service/bot_service/strategy/base.py`:

```python
class BaseStrategy:
    # --- Exit configuration ---
    # Percentage of entry price for exchange-native stop-loss
    # e.g. 0.02 = 2% below entry for long, 2% above entry for short
    base_stop_loss_pct: float | None = None

    # Percentage of entry price for exchange-native take-profit
    base_take_profit_pct: float | None = None

    # Exit mode:
    #   "static"  — SL/TP placed at entry, never amended
    #   "dynamic" — SL/TP placed at entry, amended when strategy updates _stop_loss/_take_profit
    #   "layered" — Wide exchange orders (base_stop_loss_pct/base_take_profit_pct) as circuit
    #               breaker; strategy's check_exit_conditions() fires precision exit
    sl_tp_mode: Literal["static", "dynamic", "layered"] = "static"

    # Internal tracking — set by OrderQueueWorker after placing bracket orders
    _sl_order_id: str | None = None
    _tp_order_id: str | None = None

    # Dynamic exit prices — strategy updates these; OrderQueueWorker amends exchange orders
    _stop_loss: float | None = None   # price level (not pct)
    _take_profit: float | None = None  # price level (not pct)

    def check_exit_conditions(self, bar: BarClose, symbol: str) -> bool:
        """
        Called before _on_bar() in layered mode.
        Return True to immediately close the position at market.
        Default: always False. Override in strategies that need precision exits.
        """
        return False
```

### `OrderQueueWorker` — bracket orders after fill

`bot-service/bot_service/exchange/order_worker.py`:

After `handle_fill()` confirms a fill and updates `_open_positions`, place bracket orders:

```python
async def _place_bracket_orders(
    self,
    strategy: BaseStrategy,
    symbol: str,
    filled_price: float,
    side: str,  # "buy" or "sell" — the fill side
) -> None:
    """Place exchange-native SL/TP after a fill. Fire-and-forget, errors logged."""
    if not strategy.base_stop_loss_pct and not strategy.base_take_profit_pct:
        return

    is_long = side == "buy"
    position_side = "long" if is_long else "short"
    close_side = "sell" if is_long else "buy"

    if strategy.base_stop_loss_pct:
        sl_price = (
            filled_price * (1 - strategy.base_stop_loss_pct)
            if is_long
            else filled_price * (1 + strategy.base_stop_loss_pct)
        )
        try:
            sl_resp = await self._exchange.place_order(
                symbol=symbol,
                side=close_side,
                order_type="stop_market",
                size=self._open_positions[strategy.name][symbol]["size"],
                stop_price=round(sl_price, 4),
                reduce_only=True,
                position_side=position_side,
            )
            strategy._sl_order_id = sl_resp.get("orderId") or sl_resp.get("order_id")
            log.info("sl_order_placed", symbol=symbol, sl_price=sl_price,
                     order_id=strategy._sl_order_id)
        except Exception as exc:
            log.error("sl_order_failed", symbol=symbol, error=str(exc))

    if strategy.base_take_profit_pct:
        tp_price = (
            filled_price * (1 + strategy.base_take_profit_pct)
            if is_long
            else filled_price * (1 - strategy.base_take_profit_pct)
        )
        try:
            tp_resp = await self._exchange.place_order(
                symbol=symbol,
                side=close_side,
                order_type="limit",
                size=self._open_positions[strategy.name][symbol]["size"],
                price=round(tp_price, 4),
                reduce_only=True,
                position_side=position_side,
            )
            strategy._tp_order_id = tp_resp.get("orderId") or tp_resp.get("order_id")
            log.info("tp_order_placed", symbol=symbol, tp_price=tp_price,
                     order_id=strategy._tp_order_id)
        except Exception as exc:
            log.error("tp_order_failed", symbol=symbol, error=str(exc))
```

Call `_place_bracket_orders` at the end of `handle_fill()` after position state is updated.

### Cancel bracket orders on bot-side close

When the strategy closes a position via `_on_bar()` (or `check_exit_conditions()` returning True), cancel the outstanding SL/TP orders:

```python
async def _cancel_bracket_orders(self, strategy: BaseStrategy, symbol: str) -> None:
    """Cancel exchange SL/TP orders after bot-side close."""
    for order_id, label in [
        (strategy._sl_order_id, "SL"),
        (strategy._tp_order_id, "TP"),
    ]:
        if not order_id:
            continue
        try:
            await self._exchange.cancel_order(symbol=symbol, order_id=order_id)
            log.info("bracket_order_cancelled", symbol=symbol, label=label, order_id=order_id)
        except Exception as exc:
            log.warning("bracket_order_cancel_failed", symbol=symbol,
                        label=label, order_id=order_id, error=str(exc))

    strategy._sl_order_id = None
    strategy._tp_order_id = None
```

### `check_exit_conditions` integration in strategy loop

In the main strategy execution loop (in `BaseStrategy.on_bar()` or caller):

```python
async def on_bar(self, bar: BarClose, symbol: str) -> None:
    if self.sl_tp_mode == "layered":
        # Check precision exit before _on_bar (runs every bar in layered mode)
        if self.check_exit_conditions(bar, symbol):
            log.info("check_exit_triggered", strategy=self.name, symbol=symbol)
            await self._order_worker.close_position(self, symbol, reason="check_exit")
            return  # skip _on_bar this bar

    await self._on_bar(bar, symbol)
```

### Dynamic mode — amend bracket orders

When `sl_tp_mode == "dynamic"`, after `_on_bar()` runs, check if the strategy updated `_stop_loss` or `_take_profit` and amend the exchange orders:

```python
async def _amend_bracket_orders_if_changed(
    self, strategy: BaseStrategy, symbol: str
) -> None:
    """Amend exchange SL/TP if strategy updated _stop_loss/_take_profit (dynamic mode only)."""
    if strategy.sl_tp_mode != "dynamic":
        return

    if strategy._stop_loss and strategy._sl_order_id:
        try:
            await self._exchange.amend_order(
                symbol=symbol,
                order_id=strategy._sl_order_id,
                stop_price=round(strategy._stop_loss, 4),
            )
        except Exception as exc:
            log.warning("sl_amend_failed", symbol=symbol, error=str(exc))

    if strategy._take_profit and strategy._tp_order_id:
        try:
            await self._exchange.amend_order(
                symbol=symbol,
                order_id=strategy._tp_order_id,
                price=round(strategy._take_profit, 4),
            )
        except Exception as exc:
            log.warning("tp_amend_failed", symbol=symbol, error=str(exc))
```

### Paper exchange — simulated SL/TP

`bot-service/bot_service/exchange/paper.py` — simulate bracket order execution:

```python
def _check_bracket_orders(self, symbol: str, current_price: float) -> None:
    """Simulate SL/TP trigger in paper trading mode."""
    for strategy_name, positions in self._open_positions.items():
        if symbol not in positions:
            continue
        pos = positions[symbol]
        if pos.get("sl_price") and _price_crosses(current_price, pos["sl_price"], pos["side"]):
            log.info("paper_sl_triggered", symbol=symbol, price=current_price)
            self._simulate_fill(symbol, strategy_name, pos, reason="stop_loss")
        elif pos.get("tp_price") and _price_crosses(current_price, pos["tp_price"], pos["side"]):
            log.info("paper_tp_triggered", symbol=symbol, price=current_price)
            self._simulate_fill(symbol, strategy_name, pos, reason="take_profit")
```

### Strategy example

`bot-service/strategies/active/funding_rate_arb_bot.py` — example usage:

```python
class FundingRateArbBot(BaseStrategy):
    # Wide exchange circuit breaker: 3% SL, 5% TP
    base_stop_loss_pct = 0.03
    base_take_profit_pct = 0.05
    sl_tp_mode = "layered"

    def check_exit_conditions(self, bar: BarClose, symbol: str) -> bool:
        """Close if funding rate flips against position direction."""
        if symbol not in self._open_positions.get(self.name, {}):
            return False
        pos = self._open_positions[self.name][symbol]
        current_rate = self._latest_funding_rates.get(symbol, 0)
        if pos["side"] == "buy" and current_rate < 0:
            log.info("funding_rate_flipped_exit", symbol=symbol, rate=current_rate)
            return True
        if pos["side"] == "sell" and current_rate > 0:
            log.info("funding_rate_flipped_exit", symbol=symbol, rate=current_rate)
            return True
        return False
```

### Config additions — `.env` / `config.py`

No new global config needed — SL/TP settings are per-strategy class attributes. Document in `docs/ops.md` that `base_stop_loss_pct` and `base_take_profit_pct` are optional; if both are `None`, no bracket orders are placed and the strategy behaves as before.

## Acceptance Criteria

1. After a fill, if `base_stop_loss_pct = 0.02`, a stop-market order is visible on the exchange at entry_price × 0.98 (for long) or entry_price × 1.02 (for short).
2. After a fill, if `base_take_profit_pct = 0.03`, a limit order is visible on the exchange at the correct TP price.
3. When the bot closes a position via `_on_bar()`, both SL and TP exchange orders are cancelled.
4. In `layered` mode, `check_exit_conditions()` returning True closes the position immediately without calling `_on_bar()`, and then cancels bracket orders.
5. In `dynamic` mode, updating `strategy._stop_loss` in `_on_bar()` causes the exchange SL order to be amended to the new price.
6. Paper exchange simulates SL/TP trigger when price crosses the bracket prices.
7. If both `base_stop_loss_pct` and `base_take_profit_pct` are `None`, no bracket orders are placed and no exchange API calls are made.
8. Exchange errors placing bracket orders are logged as warnings but do not abort the fill handling or crash the strategy.
9. `FundingRateArbBot` uses `layered` mode with `check_exit_conditions()` checking funding rate direction.

## Dev Notes

- **Exchange API compatibility:** Bybit supports conditional stop-market orders (`order_type="Stop"`) and limit TPs via `placeOrder`. KuCoin supports stop orders via `/api/v1/stop-order`. These use different parameter names — the exchange adapter (`rest.py`) must normalize to a common interface (`stop_market`, `reduce_only`, `stop_price`). Add `amend_order` and `cancel_order` to the exchange interface if not already present.
- **Bracket orders survive restarts:** when the bot restarts and runs `run_startup_reconciliation()`, it queries open positions from the exchange. The bracket orders appear as open orders on the exchange — the reconciliation should detect them and store their order IDs in `strategy._sl_order_id` and `strategy._tp_order_id`. Add this logic to `startup_reconciliation.py`.
- **Reduce-only flag:** all bracket orders must be `reduce_only=True` so they can only close, never flip the position. This is critical on exchanges where a filled SL could open a new position in the opposite direction.
- **`check_exit_conditions` call frequency:** in `layered` mode, this is called on every bar for every strategy. Keep it fast — it's a synchronous check, no I/O. Use cached state (e.g. `_latest_funding_rates` populated by a background task).
- **Dynamic mode rate limiting:** amending bracket orders every bar in `dynamic` mode with a 1-second bar interval = 60 amendments/minute. Exchanges typically rate-limit order amendments. Throttle amendments in `_amend_bracket_orders_if_changed` by only amending if the price changed by >0.1% from the last amended price.
- **Paper exchange bracket simulation:** the paper exchange receives bars, not ticks. SL/TP simulation uses bar OHLC — if the bar's low (for long SL) or high (for short SL) crosses the stop price, the fill is simulated at the stop price. This underestimates slippage but is good enough for backtesting.
