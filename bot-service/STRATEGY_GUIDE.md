# Strategy Creation Guide

This guide explains how to write, activate, and backtest trading strategies in
the bot-service.  It describes the actual system as it exists today — every
path, class name, and API call is taken from the live code.

---

## Table of Contents

1. [How the system works](#1-how-the-system-works)
2. [File location and naming](#2-file-location-and-naming)
3. [Required interface — BaseStrategy](#3-required-interface--basestrategy)
4. [subscribe() — connecting to data](#4-subscribe--connecting-to-data)
5. [Placing orders](#5-placing-orders)
6. [Signal helpers](#6-signal-helpers)
7. [Indicator hook — add_indicators()](#7-indicator-hook--add_indicators)
8. [Gap handling](#8-gap-handling)
9. [Orderbook access (optional)](#9-orderbook-access-optional)
10. [Funding rate access (optional)](#10-funding-rate-access-optional)
11. [Activating and deactivating a strategy](#11-activating-and-deactivating-a-strategy)
12. [Backtesting](#12-backtesting)
13. [Walk-forward validation](#13-walk-forward-validation)
14. [Complete minimal example](#14-complete-minimal-example)
15. [Gotchas and constraints](#15-gotchas-and-constraints)

---

## 1. How the system works

```
strategies/active/*.py
       │
       ▼
FileWatcher (polls every 60 s)
       │  loads via importlib; finds unique BaseStrategy subclass
       ▼
StrategyRegistry
       │  injects settings, exchange client, order worker
       ▼
strategy.subscribe()          ← you declare which symbols/timeframes to watch
       │
       ├─ get_history()        ← seed rolling DataFrame from QuestDB
       └─ register_bar_handler() ← hook called on every completed bar
              │
              ▼
         your _on_bar()        ← your logic runs here
              │
              ▼
         OrderRequest → order worker → exchange
```

The `BaseStrategy` base class handles NaN guards, lookback gates, gap
invalidation + recovery, heartbeat freeze detection, and bus-silence timeouts
automatically.  You cannot bypass or override these safety checks.

---

## 2. File location and naming

| Location | Purpose |
|---|---|
| `strategies/active/` | Loaded by the FileWatcher; live and backtestable |
| `strategies/inactive/` | Ignored by the FileWatcher; acts as a holding area |

**Rules:**

- File name becomes the strategy name everywhere in the system.
  `strategies/active/my_ema_bot.py` → strategy name `my_ema_bot`.
- Names must match `^[A-Za-z0-9_\-\.]+$`.  No spaces, slashes, or special
  characters.
- Exactly **one** `BaseStrategy` subclass per file.  Having zero or two raises
  an error at load time.
- Class names must be **globally unique** across all loaded files.

---

## 3. Required interface — BaseStrategy

```python
from bot_service.strategy.base import BaseStrategy
```

Every strategy must subclass `BaseStrategy` and implement all six abstract
properties plus `subscribe()`:

```python
class MyStrategy(BaseStrategy):

    @property
    def min_lookback(self) -> int:
        """Minimum number of bars required before handlers fire.
        
        The lookback gate in register_bar_handler() counts rows in the rolling
        DataFrame and silently drops handler calls until len(df) >= min_lookback.
        Set this to the maximum indicator warmup period your strategy needs.
        """
        return 22   # e.g. slow EMA period + 1

    @property
    def max_position_pct(self) -> float:
        """Fraction of portfolio per order.  Must be in (0, 1]."""
        return 0.05  # 5 %

    @property
    def stop_loss_pct(self) -> float:
        """Hard stop-loss percentage.  Informational — enforced upstream."""
        return 0.02  # 2 %

    @property
    def paper_trading(self) -> bool:
        """True → no real orders are sent; fills are simulated.
        Start here — switch to False only for live trading."""
        return True

    @property
    def bus_timeout_seconds(self) -> int:
        """Seconds of bar-event silence before bus_timeout fires.
        For illiquid symbols or slow timeframes use a larger value.
        """
        return 300   # 5 minutes

    @property
    def close_on_bus_timeout(self) -> bool:
        """True → attempt an emergency market-sell on all open positions
        if the bus goes silent for bus_timeout_seconds.
        Safe default is False for paper trading."""
        return False

    def subscribe(self) -> None:
        # see section 4
        ...
```

---

## 4. subscribe() — connecting to data

`subscribe()` is called once during startup.  Inside it you:

1. Call `self.get_history()` to seed the rolling DataFrame.
2. Call `self.register_bar_handler()` to register your callback.

```python
def subscribe(self) -> None:
    self.get_history("BTC-USDT", "1m", self.min_lookback)
    self.register_bar_handler("BTC-USDT", "1m", self._on_bar)
```

### Timeframe → QuestDB table mapping

| Timeframe | Table queried |
|---|---|
| `1s` | `snapshot_1s` |
| `1m`, `5m` | `snapshot_1m` |
| `15m`, `1h`, `4h`, `1d`, `1w` | `snapshot_15m` |

### get_history(symbol, tf, n_bars)

Queries QuestDB synchronously and seeds the rolling DataFrame for `(symbol, tf)`.
On failure it returns an empty DataFrame and schedules a retry every 30 seconds
in the background — your bar handler will not fire until data arrives.

### register_bar_handler(symbol, tf, handler)

Registers `handler` as the callback for completed bars on `(symbol, tf)`.
The base class wraps it with:

1. **Lookback gate** — silent drop while `len(df) < min_lookback`.
2. **NaN guard** — silent drop (+ Prometheus counter) while any cell in the last
   row is NaN.

Your handler receives the full rolling DataFrame with the new bar appended.

```python
def _on_bar(self, df: pd.DataFrame) -> None:
    # df has columns: ts, open, high, low, close, volume,
    #                 quote_volume, trade_count, is_complete, has_gap
    # plus any columns added by add_indicators()
    last = df.iloc[-1]
    ...
```

---

## 5. Placing orders

Import and build an `OrderRequest`, then post it:

```python
from bot_service.exchange import OrderRequest

req = OrderRequest(
    strategy=self._name,          # injected string; do not hardcode
    exchange=self._exchange,      # injected string: "kucoin" or "bybit"
    symbol="BTC-USDT",
    side="buy",                   # "buy" | "sell"
    order_type="market",          # "market" | "limit"
    order_role="entry",           # "entry" | "exit" | "stop"
    size=self.max_position_pct,   # fraction of portfolio
    limit_price=None,             # required only for order_type="limit"
    paper_trading=self.paper_trading,
)
self._order_worker.post(req)
```

**Guard before posting** — `self._exchange` and `self._order_worker` are
injected after construction but before `subscribe()` returns.  If either is
falsy the framework hasn't wired them yet; log and return:

```python
if not self._exchange:
    log.warning("exchange_not_injected_dropping_order", strategy=self._name)
    return
if self._order_worker is None:
    log.warning("order_worker_not_injected_dropping_order", strategy=self._name)
    return
```

---

## 6. Signal helpers

Three built-in signal helpers are available.  All return a `SignalResult`
(`.action`, `.confidence`, `.reason`) and never raise.  Bad inputs return
`HOLD_INSUFFICIENT` (action=`"hold"`, confidence=`0.0`).

### EMA / MA crossover

```python
from bot_service.strategy.signals.ma_cross import compute_ma_cross_signal

result = compute_ma_cross_signal(df, fast=9, slow=21)
# result.action: "buy" | "sell" | "hold"
# result.confidence: 0.0–1.0  (proportional to EMA separation)
# result.reason: "ma_cross_up" | "ma_cross_down" | "no_cross"
```

Uses pure `pandas.ewm()`.  Requires at least `slow + 1` rows.

### RSI

```python
from bot_service.strategy.signals.rsi import rsi_signal

# add_indicators() must compute the RSI column first:
def add_indicators(self, df: pd.DataFrame) -> None:
    df.ta.rsi(length=14, append=True)  # adds column RSI_14

result = rsi_signal(df, period=14, oversold=30.0, overbought=70.0)
# Fires "buy"  when RSI crosses up through oversold  (30 → recovery)
# Fires "sell" when RSI crosses down through overbought (70 → reversal)
```

Reads the `RSI_{period}` column written by `df.ta.rsi(append=True)`.
**You must call `add_indicators` for the column to exist.**

### OFI (Order Flow Imbalance)

```python
from bot_service.strategy.signals.ofi_signal import ofi_signal

result = ofi_signal(df, lookback=30, threshold=0.6)
# "buy"  when OFI z-score > threshold
# "sell" when OFI z-score < -threshold
```

Reads the pre-existing `ofi` column from `snapshot_1s`.
Only meaningful on the `1s` timeframe.

---

## 7. Indicator hook — add_indicators()

Override `add_indicators(df)` to append pandas-ta columns to the rolling
DataFrame before the bar handler fires:

```python
def add_indicators(self, df: pd.DataFrame) -> None:
    df.ta.rsi(length=14, append=True)    # adds RSI_14
    df.ta.ema(length=9, append=True)     # adds EMA_9
    df.ta.ema(length=21, append=True)    # adds EMA_21
```

**Rules:**
- Mutate `df` in-place.  Do not reassign the local variable.
- Exceptions are caught and logged — the bar handler still fires even if
  `add_indicators` fails.
- NaN rows added during indicator warmup do not permanently block handlers.
  The NaN guard checks only the **last row**.

---

## 8. Gap handling

When the aggregator detects a data gap (feed disconnect, rate-limit, etc.) it
publishes a `GapMarker`.  The base class automatically:

- Sets `self._signal_invalid[symbol] = True` — your handler receives
  `self._signal_invalid.get(symbol, False)` as the gate.
- Clears the invalid flag after `min_lookback` clean bars have arrived.

The recommended pattern in `_on_bar`:

```python
def _on_bar(self, df: pd.DataFrame) -> None:
    if self._signal_invalid.get(_SYMBOL, False):
        return
    ...
```

Override `handle_gap()` to reset any position tracking state:

```python
def handle_gap(self, gap: GapMarker) -> None:
    self._current_side = None   # reset your own state
    super().handle_gap(gap)     # let base class do its work — always call this
```

---

## 9. Orderbook access (optional)

Override `orderbook_mode` to receive orderbook updates:

| Value | Behaviour |
|---|---|
| `"none"` | No orderbook data (default) |
| `"snapshot_1s"` | 1-second orderbook snapshots |
| `"full_stream"` | Every orderbook update |
| `"both"` | Both snapshot_1s and full_stream |

```python
@property
def orderbook_mode(self) -> str:
    return "snapshot_1s"

def _on_orderbook(self, payload: dict) -> None:
    # payload from BusManager pub/sub thread — must be thread-safe
    ...
```

---

## 10. Funding rate access (optional)

Register interest in a funding rate stream inside `subscribe()`:

```python
def subscribe(self) -> None:
    self.register_funding_rate_handler("bybit", "BTCUSDT")
    self.get_history("BTCUSDT", "1m", self.min_lookback)
    self.register_bar_handler("BTCUSDT", "1m", self._on_bar)

def handle_funding_rate(self, event: FundingRate) -> None:
    # event.rate, event.symbol, event.exchange, event.ts
    ...
```

---

## 11. Activating and deactivating a strategy

**Development (hot-reload):**

```bash
# Activate
mv strategies/inactive/my_strategy.py strategies/active/

# Deactivate
mv strategies/active/my_strategy.py strategies/inactive/
```

The FileWatcher polls `strategies/active/` every 60 seconds
(`BOT_FILEWATCHER_INTERVAL_S`).  New files are loaded; removed files cause the
running strategy to be stopped on the next poll cycle.

**Production (Docker):**

The `Dockerfile` bakes `strategies/` into the image at build time:

```dockerfile
COPY strategies/ ./strategies/
```

After adding or removing a strategy file, rebuild and redeploy:

```bash
docker compose build bot-service
docker compose up -d bot-service
```

Hot-reload still works inside the running container; the 60-second poll picks
up file changes made after `docker exec` or volume-mounted overrides.

---

## 12. Backtesting

The same `BaseStrategy` file is used for backtesting via `POST /backtest/run`.
No separate file is needed.

The backtest runner wraps your class as a `backtrader` strategy internally;
you do not interact with `bt.Cerebro` directly.

**Dashboard workflow:**
1. Open `/backtests`.
2. Select the strategy from the dropdown (populated from `strategies/active/`).
3. Pick exchange, symbol, timeframe, date range, and capital.
4. Click **▶ Run Backtest**.
5. After completion the metrics card and equity chart populate automatically.

**API:**

```bash
curl -X POST http://localhost:8090/backtest/run \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_name": "ema_cross_kucoin",
    "symbol": "BTC-USDT",
    "exchange": "kucoin",
    "tf": "1m",
    "start_date": "2026-01-01",
    "end_date": "2026-02-01",
    "capital": 10000
  }'

# → {"run_id": "...", "status": "running"}

curl http://localhost:8090/backtest/run/{run_id}
# → {"status": "done", "result": {...}}
```

If there is no data in `snapshot_1s` / `snapshot_1m` for the selected
exchange + symbol + date range, the run fails with `InsufficientHistoryError`.
This is expected in a dev environment — populate the QuestDB table first.

---

## 13. Walk-forward validation

Walk-forward validation (`POST /backtest/validate`) requires a **second class**
in the same strategy file — a `bt.Strategy` subclass.  This is separate from
the `BaseStrategy` subclass used for live trading and regular backtests.

```python
import backtrader as bt

class MyStrategyValidate(bt.Strategy):
    """Walk-forward variant — backtrader native API."""

    def __init__(self):
        self.fast_ema = bt.indicators.ExponentialMovingAverage(period=9)
        self.slow_ema = bt.indicators.ExponentialMovingAverage(period=21)

    def next(self):
        if self.fast_ema[0] > self.slow_ema[0] and not self.position:
            self.buy()
        elif self.fast_ema[0] < self.slow_ema[0] and self.position:
            self.sell()
```

The validator discovers any `bt.Strategy` subclass in the file automatically.
If none is present, `POST /backtest/validate` returns 422.

---

## 14. Complete minimal example

Save as `strategies/active/my_ema_rsi_bot.py`:

```python
"""EMA + RSI confirmation strategy — paper trading.

Entry:
  BUY  when fast EMA crosses above slow EMA AND RSI is not overbought.
  SELL when fast EMA crosses below slow EMA AND RSI is not oversold.
"""
from __future__ import annotations

import pandas as pd
import structlog

from bot_service.bus.event_types import GapMarker
from bot_service.exchange import OrderRequest
from bot_service.strategy.base import BaseStrategy
from bot_service.strategy.signals.ma_cross import compute_ma_cross_signal

log = structlog.get_logger()

_SYMBOL = "BTC-USDT"
_TF = "1m"
_FAST = 9
_SLOW = 21
_RSI_PERIOD = 14
_RSI_COL = f"RSI_{_RSI_PERIOD}"


class MyEmaRsiBot(BaseStrategy):
    """EMA 9/21 with RSI confirmation — paper trading."""

    @property
    def min_lookback(self) -> int:
        return max(_SLOW + 1, _RSI_PERIOD + 2)

    @property
    def max_position_pct(self) -> float:
        return 0.05

    @property
    def stop_loss_pct(self) -> float:
        return 0.02

    @property
    def paper_trading(self) -> bool:
        return True

    @property
    def bus_timeout_seconds(self) -> int:
        return 300

    @property
    def close_on_bus_timeout(self) -> bool:
        return False

    def __init__(self, name: str, settings: object) -> None:
        super().__init__(name, settings)  # type: ignore[arg-type]
        self._current_side: str | None = None

    def add_indicators(self, df: pd.DataFrame) -> None:
        df.ta.rsi(length=_RSI_PERIOD, append=True)  # type: ignore[attr-defined]

    def subscribe(self) -> None:
        self.get_history(_SYMBOL, _TF, self.min_lookback)
        self.register_bar_handler(_SYMBOL, _TF, self._on_bar)

    def handle_gap(self, gap: GapMarker) -> None:
        self._current_side = None
        super().handle_gap(gap)

    def _on_bar(self, df: pd.DataFrame) -> None:
        if self._signal_invalid.get(_SYMBOL, False):
            return

        result = compute_ma_cross_signal(df, fast=_FAST, slow=_SLOW)
        if result.action == "hold":
            return

        # RSI confirmation: don't buy when overbought, don't sell when oversold
        if _RSI_COL in df.columns:
            rsi_val = df[_RSI_COL].iloc[-1]
            if result.action == "buy" and rsi_val > 70:
                return
            if result.action == "sell" and rsi_val < 30:
                return

        target_side = "long" if result.action == "buy" else "short"
        if self._current_side == target_side:
            return

        if not self._exchange or self._order_worker is None:
            log.warning("order_deps_not_injected", strategy=self._name)
            return

        log.info(
            "ema_rsi_signal",
            strategy=self._name,
            action=result.action,
            confidence=round(result.confidence, 4),
        )

        # Flip: close opposite first
        if self._current_side == "long":
            self._post("sell", "exit")
            self._current_side = None
        elif self._current_side == "short":
            self._post("buy", "exit")
            self._current_side = None

        self._post("buy" if result.action == "buy" else "sell", "entry")
        self._current_side = target_side

    def _post(self, side: str, role: str) -> None:
        self._order_worker.post(  # type: ignore[attr-defined]
            OrderRequest(
                strategy=self._name,
                exchange=self._exchange,
                symbol=_SYMBOL,
                side=side,
                order_type="market",
                order_role=role,
                size=self.max_position_pct,
                paper_trading=self.paper_trading,
            )
        )
```

---

## 15. Gotchas and constraints

### Class names are globally unique
If two files in `strategies/active/` define a class named `MyBot`, the second
file to load will fail with a name-collision error in the registry.  Always use
a distinctive name that includes the strategy's purpose and exchange.

### File stem = strategy name everywhere
The dashboard dropdown, `/strategies` API, and `/backtest/run` all use the
**file stem** (filename without `.py`).  Renaming a file after a backtest run
is recorded breaks history queries for that strategy.

### Don't import from inactive/
Inactive strategy files are not on the Python path.  They exist only as
developer storage.

### min_lookback governs both the gate AND history fetch
Pass `self.min_lookback` as `n_bars` to `get_history()`.  If you fetch fewer
bars than `min_lookback`, the handler will be silently blocked on every bar
until the rolling DataFrame grows to length.

### add_indicators() must mutate in-place
```python
# ✗ WRONG — has no effect on self._dfs
def add_indicators(self, df: pd.DataFrame) -> None:
    df = df.dropna()

# ✓ CORRECT
def add_indicators(self, df: pd.DataFrame) -> None:
    df.ta.rsi(length=14, append=True)
```

### RSI signal requires add_indicators()
`rsi_signal()` reads the `RSI_{period}` column.  If `add_indicators()` doesn't
compute it, every call returns `HOLD_INSUFFICIENT`.

### paper_trading=True for all new strategies
Switch to `False` only when the strategy has a validated track record and you
intend to send real orders to the exchange.

### Docker rebuild required for production
Changes to files in `strategies/` only take effect after rebuilding the image.
Hot-reload (FileWatcher) works for development containers but not for
immutable production deployments.

### subscribe() has a 30-second timeout
If `subscribe()` does not return within `BOT_SUBSCRIBE_TIMEOUT_S` (default 30 s),
the strategy is killed.  Long-running HTTP calls inside `subscribe()` must have
explicit timeouts.

### Walk-forward validation needs a separate bt.Strategy subclass
`POST /backtest/run` uses your `BaseStrategy` subclass.  
`POST /backtest/validate` needs a `bt.Strategy` subclass in the same file.
They are independent and do not share state.
