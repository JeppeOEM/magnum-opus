---
id: 33-2
title: Funding rate arb bot strategy
epic: 33
status: done
---

# Story 33-2: Funding rate arb bot strategy

## Context

Story 33-1 delivers `FundingRate` events to the BusManager. `event_types.py` already parses `funding:*` stream entries into `FundingRate` objects, and `funding_rate_arb.py` is a stub signal. The dispatch layer (`run_strategy_event_loop` in `registry.py`) only routes `BarClose` and `GapMarker`; `FundingRate` events arrive in the queue but are silently dropped. This story wires `FundingRate` dispatch into the event loop, adds `handle_funding_rate()` to `BaseStrategy`, and implements `FundingRateArbBot` as a paper-mode strategy that goes short on high positive funding (collect funding payment) and long on high negative funding.

## What to build

### `bot-service/bot_service/strategy/base.py` — add FundingRate support

Add to imports:
```python
from bot_service.bus.event_types import BarClose, FundingRate, GapMarker
```

Add to `__init__`:
```python
self._funding_stream_keys: set[str] = set()
```

Add method:
```python
def register_funding_rate_handler(self, exchange: str, symbol: str) -> None:
    """Register interest in funding:{exchange}:{symbol} stream."""
    self._funding_stream_keys.add(f"funding:{exchange}:{symbol}")

def handle_funding_rate(self, event: FundingRate) -> None:
    """Called on each FundingRate event. Override in strategies that use funding rates."""
```

### `bot-service/bot_service/strategy/registry.py` — dispatch FundingRate

In `run_strategy_event_loop`, add dispatch:
```python
from bot_service.bus.event_types import BarClose, FundingRate, GapMarker
...
if isinstance(event, BarClose):
    strategy.on_bar(event)
elif isinstance(event, GapMarker):
    strategy.handle_gap(event)
elif isinstance(event, FundingRate):
    strategy.handle_funding_rate(event)
```

In `_load_new`, after bar handler stream keys, add:
```python
for stream_key in strategy._funding_stream_keys:
    stream_keys.add(stream_key)
```

### `bot-service/bot_service/strategy/signals/funding_rate_arb.py` — implement signal

Replace the stub:
```python
def funding_rate_arb_signal(
    funding_rate: float, threshold_bps: float = 10.0
) -> SignalResult:
    threshold = threshold_bps / 10_000
    if funding_rate > threshold:
        return SignalResult(action="sell", confidence=min(funding_rate / threshold, 1.0), reason="high_positive_funding")
    if funding_rate < -threshold:
        return SignalResult(action="buy", confidence=min(abs(funding_rate) / threshold, 1.0), reason="high_negative_funding")
    return SignalResult(action="hold", confidence=0.0, reason="rate_below_threshold")
```

### `bot-service/strategies/active/funding_rate_arb_bot.py`

```python
from bot_service.strategy.base import BaseStrategy
from bot_service.bus.event_types import FundingRate
from bot_service.strategy.signals.funding_rate_arb import funding_rate_arb_signal
from bot_service.exchange import OrderRequest

_EXCHANGE = "bybit"
_SYMBOL = "BTCUSDT"
_THRESHOLD_BPS = 10.0

class FundingRateArbBot(BaseStrategy):
    """Short on high positive funding, long on high negative funding (paper mode)."""
    @property
    def min_lookback(self) -> int: return 1
    @property
    def max_position_pct(self) -> float: return 0.02
    @property
    def stop_loss_pct(self) -> float: return 0.05
    @property
    def paper_trading(self) -> bool: return True
    @property
    def bus_timeout_seconds(self) -> int: return 300
    @property
    def close_on_bus_timeout(self) -> bool: return True

    def subscribe(self) -> None:
        self.register_funding_rate_handler(_EXCHANGE, _SYMBOL)

    def handle_funding_rate(self, event: FundingRate) -> None:
        result = funding_rate_arb_signal(event.funding_rate, _THRESHOLD_BPS)
        if result.action == "hold":
            return
        req = OrderRequest(
            strategy=self._name,
            exchange=self._exchange or _EXCHANGE,
            symbol=event.symbol,
            side=result.action,
            order_type="market",
            order_role="entry",
            size=self.max_position_pct,
            paper_trading=self.paper_trading,
        )
        if self._order_worker is not None:
            self._order_worker.post(req)
```

## Acceptance Criteria

1. `FundingRate` events dispatched to `strategy.handle_funding_rate()` in `run_strategy_event_loop`.
2. `BaseStrategy.register_funding_rate_handler(exchange, symbol)` adds `funding:{exchange}:{symbol}` to `_funding_stream_keys`.
3. `_load_new` includes `_funding_stream_keys` in returned stream key set.
4. `funding_rate_arb_signal(rate=0.002, threshold_bps=10.0)` → action="sell".
5. `funding_rate_arb_signal(rate=-0.002)` → action="buy".
6. `funding_rate_arb_signal(rate=0.0)` → action="hold".
7. `FundingRateArbBot.subscribe()` registers `funding:bybit:BTCUSDT` stream.
8. L1 tests cover signal function (sell/buy/hold) and `handle_funding_rate` dispatch.

## Dev Notes

- `handle_funding_rate` default is a no-op in `BaseStrategy` — existing strategies unaffected.
- `FundingRateArbBot` is paper-only (`paper_trading = True`).
- `subscribe()` does NOT call `get_history()` — no bar data needed.
- `min_lookback = 1` satisfies the base class requirement but is effectively unused.
- `bus_timeout_seconds = 300` — poller fires every 60s, so 5 min is 5 missed polls.

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### File List

- `bot-service/bot_service/strategy/base.py`
- `bot-service/bot_service/strategy/registry.py`
- `bot-service/bot_service/strategy/signals/funding_rate_arb.py`
- `bot-service/strategies/active/funding_rate_arb_bot.py`
- `bot-service/tests/test_funding_rate_arb.py`
