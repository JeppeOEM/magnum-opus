# Chapter 04 — Bot Service

**30-second summary:** The bot service is a Python FastAPI app that runs one or more
trading strategies as isolated asyncio threads. It watches a directory for `.py`
files, hot-reloads strategies when files change, and restarts crashed threads with
exponential backoff. Each strategy receives bar events from Redis, calls `on_bar()`,
and posts order requests to the `OrderQueueWorker` which applies risk gates and
places orders via exchange REST APIs.

All source lives under `bot-service/`.

---

## 1. Package Structure

```
bot-service/
├── bot_service/
│   ├── main.py                 FastAPI app + lifespan (startup/shutdown)
│   ├── config.py               Settings (pydantic-settings from env)
│   ├── bus/
│   │   ├── event_bus.py        BusManager (XREADGROUP + pub/sub threads)
│   │   └── event_types.py      BarClose, GapMarker, FundingRate, OrderFilled
│   ├── strategy/
│   │   ├── base.py             BaseStrategy abstract class
│   │   ├── registry.py         FileWatcher + hot-reload + watchdog
│   │   ├── order_worker.py     OrderQueueWorker (risk gate + REST + QuestDB)
│   │   ├── circuit_breaker.py  DailyLossCircuitBreaker
│   │   └── reconciliation.py   Startup reconciliation (open orders from QuestDB)
│   ├── exchange/
│   │   ├── paper.py            Paper trading exchange (simulated fills)
│   │   ├── kucoin/rest.py      KuCoin REST client
│   │   ├── kucoin/ws_private.py KuCoin private WebSocket (fills)
│   │   ├── bybit/rest.py       Bybit REST client
│   │   └── bybit/ws_private.py Bybit private WebSocket (fills)
│   ├── backtest/
│   │   ├── runner.py           Backtest engine (replays QuestDB data)
│   │   └── writer.py           Writes backtest results to QuestDB
│   ├── persistence/schema.py   QuestDB table definitions
│   └── metrics/prometheus.py   Prometheus gauges for strategies
└── strategies/active/          Hot-reload directory (user .py files go here)
```

---

## 2. Startup: FastAPI Lifespan

[`bot-service/bot_service/main.py`](../../bot-service/bot_service/main.py)

The app uses a FastAPI `@asynccontextmanager` lifespan. On startup:

```
1  Load Settings (config.py)
2  Create ExchangeClient (paper or live: kucoin/bybit)
3  Create DailyLossCircuitBreaker (if bot_daily_loss_limit_usd > 0)
4  Create BusManager
5  Create FileWatcher (strategies_dir = strategies/active/)
6  FileWatcher.initial_scan()
   └─ Scans strategies/active/*.py
   └─ For each .py: imports class, runs reconciliation, spawns thread
   └─ Returns union of all required Redis stream keys
7  For each stream key: bus_manager.add_stream(key)
8  bus_manager.start()   ← spawns bus-manager + bus-pubsub threads
9  Start FileWatcher.run_loop()  ← polls for file changes every N seconds
10 Start FileWatcher.watch_loop() ← watchdog: detects crashed threads
```

On shutdown:
- `file_watcher.stop_all()` — stops all strategy threads
- `bus_manager.stop()` — stops consumer threads

---

## 3. BusManager — The Redis Consumer

[`bot-service/bot_service/bus/event_bus.py`](../../bot-service/bot_service/bus/event_bus.py)

`BusManager` runs two daemon threads:

### Thread 1: `bus-manager` (XREADGROUP)

- Calls `XREADGROUP GROUP {group} {hostname}-{pid} STREAMS {key1} {key2} ... > COUNT 100 BLOCK 1000`.
- Consumer group is created with `MKSTREAM` (idempotent).
- `XACK` is called immediately after reading (ACK-before-process).
- On successful read, delivers each event to every registered strategy queue.
- On Redis error: exponential backoff (1s → 2s → 4s → cap 60s).
- After 3 consecutive cap-hits: `os.kill(os.getpid(), SIGTERM)` — the service dies
  rather than silently dropping data.

### Thread 2: `bus-pubsub` (Pub/Sub)

- `psubscribe("orderbook:*", "candles1s:*")`.
- Dispatches to per-strategy callbacks registered via `provision_pubsub()`.
- Strategies opt in via `orderbook_mode`: `"none"` (default), `"snapshot_1s"`,
  `"full_stream"`, or `"both"`.

### Event Routing

`_route(stream_key, entry)` calls `parse_stream_entry()` to convert the raw Redis
hash to a typed event:
- `candles:close:{ex}:{sym}:{tf}` → `BarClose`
- `candles:ob:{ex}:{sym}` → (delivered via pub/sub, not stream)
- GapMarker events are injected on `queue_overflow` (drop-oldest semantics).

**Drop-oldest overflow:** When a strategy queue is full:
1. Drop 2 oldest items (one slot for GapMarker, one for the real event).
2. Inject a `GapMarker(gap_cause="queue_overflow")`.
3. Enqueue the real event.
4. Increment `inc_queue_drop` metric.

---

## 4. `StrategyHandle` — One Strategy Thread

Each loaded strategy gets a `StrategyHandle`:

```python
@dataclass(frozen=True)
class StrategyHandle:
    name: str
    thread: threading.Thread      # daemon thread running the asyncio loop
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[BusEvent]
    ready_event: threading.Event  # set once subscribe() completes
```

The thread runs `run_strategy_event_loop()` — an asyncio coroutine that:
1. Calls `strategy.run_subscribe()` — subscribes to the exchange's private WS feed.
2. Calls `strategy.start_heartbeat()`.
3. Loops, consuming from the queue:
   - `BarClose` → `strategy.on_bar(event)`
   - `GapMarker` → `strategy.handle_gap(event)`
   - `FundingRate` → `strategy.handle_funding_rate(event)`
   - Every 1s timeout → `strategy.ack_heartbeat()`

---

## 5. `BaseStrategy` — The Strategy Contract

[`bot-service/bot_service/strategy/base.py`](../../bot-service/bot_service/strategy/base.py)

All strategies subclass `BaseStrategy`. Key abstract and overridable methods:

| Method | When called | Override required? |
|--------|------------|-------------------|
| `on_bar(bar: BarClose)` | Every closed bar on subscribed symbols | Yes |
| `handle_gap(gap: GapMarker)` | When a data gap is detected | No (default: log) |
| `handle_funding_rate(fr: FundingRate)` | When funding rate arrives | No |
| `check_exit_conditions(bar, symbol) → bool` | Called from on_bar for managed positions | No |

Key fields strategies set in `__init__`:

```python
# Which bars to receive (symbol → [tf, ...])
self._bar_handlers: dict[str, list[str]] = {"BTC-USDT": ["1m", "15m"]}

# Positions to automatically manage (send 1s bars + OB data)
self._managed_positions: set[str] = {"BTC-USDT"}

# Funding rate stream keys to subscribe
self._funding_stream_keys: set[str] = set()

# OB real-time data mode
self.orderbook_mode: Literal["none", "snapshot_1s", "full_stream", "both"] = "none"

# Risk parameters
self.max_position_pct: float = 0.10   # max 10% of portfolio per symbol
self.paper_trading: bool = True
```

**Heartbeat mechanism:** `BaseStrategy` starts a background asyncio task
(`_heartbeat_loop`) that sets a watchdog timer. If `ack_heartbeat()` is not called
within `bot_heartbeat_timeout_s` seconds, the strategy logs a warning and calls
`_emergency_close()` to flatten all positions.

**Emergency close:** Sends market sell orders for all known positions, cancels open
orders. Called on heartbeat timeout, SIGTERM, or circuit breaker trip.

**Bus timeout loop:** A second background task (`_bus_timeout_loop`) checks that bar
events keep arriving. If the event bus stops delivering for `bot_subscribe_timeout_s`
seconds, the strategy assumes the bus is dead and triggers emergency close.

---

## 6. `OrderQueueWorker` — Risk Gate and Order Execution

[`bot-service/bot_service/strategy/order_worker.py`](../../bot-service/bot_service/strategy/order_worker.py)

The `OrderQueueWorker` is created per strategy and runs in the same asyncio loop
as the strategy thread.

### Three-Layer State Model

1. **In-memory:** `open_orders: dict[order_id → (req, placed)]` + `_position_notional`.
2. **QuestDB:** `order_events` table via ILP (append-only, fire-and-forget).
3. **Crash-safety invariant:** QuestDB write of `"placed"` happens **before**
   `open_orders[placed.order_id] = ...`. This means reconciliation can always
   recover from QuestDB.

### `_process(req)` — The Pipeline

```
1. _is_entry_dedup(req)
   └─ If an open entry order exists for (symbol, side) → drop (log debug)

2. _exceeds_hard_limit(req)
   └─ If single-order notional > max_order_notional_usd → block (log warning)

3. _exceeds_risk_gate(req)
   └─ If (current_notional + new_notional) > max_position_pct × portfolio → block

4. _place_and_persist(req)
   a. Call exchange_client.place_order(req)
   b. Write "placed" event to QuestDB (BEFORE updating open_orders)
   c. open_orders[order_id] = (req, placed)
   d. Update _position_notional
```

### Fill Handling

`handle_fill(fill: OrderFilled)`:
1. Dedup via `_seen_fill_ids` (bounded to 10,000 entries).
2. Look up `open_orders[fill.order_id]`.
3. Update `_position_notional` (subtract closed notional).
4. Call `_update_position()` — FIFO cost basis, returns realized PnL.
5. Write "filled" event to QuestDB with slippage + realized PnL.
6. Feed PnL to `DailyLossCircuitBreaker`.
7. Update Prometheus gauges: position size, unrealized PnL, drawdown.

### FIFO Cost Basis

`_update_position(symbol, side, qty, price)`:
- Buy: weighted average of existing + new position.
- Sell: realized PnL = `closed_qty × (fill_price − avg_entry_price)`.

---

## 7. FileWatcher — Hot-Reload and Watchdog

[`bot-service/bot_service/strategy/registry.py`](../../bot-service/bot_service/strategy/registry.py)

### `initial_scan()` (called once at startup)
- Scans `strategies/active/*.py`.
- Imports each file via `importlib.util.spec_from_file_location`.
- Rejects files with zero or >1 `BaseStrategy` subclasses.
- Rejects duplicate class names (same name in two files).
- Calls `_load_new()` for each valid class.

### `run_loop()` (polls every `bot_filewatcher_interval_s`)
- `_rescan()` detects three cases:
  - **New file:** `_load_new(class_name, path, cls)`.
  - **Removed file:** `_unload(class_name, reason="file_removed")`.
  - **Modified file** (mtime changed): `_hot_reload()` = unload + reload.

### `watch_loop()` (watchdog, polls every 5 s)
- Detects strategy threads where `not handle.thread.is_alive() and not stop_event.is_set()`.
- Calls `_handle_crash()`:
  1. Resets Prometheus gauges for the strategy.
  2. Deregisters from BusManager.
  3. Sleeps for current backoff (5s → 10s → 30s → 60s, capped).
  4. Re-imports the strategy class.
  5. Calls `_load_new()` again.
  6. `_pending_restart` set prevents `run_loop` from double-loading.

### `_load_new()` — Full Strategy Initialization

For each new strategy class:
1. Instantiate `cls(name=class_name, settings=settings)`.
2. Instantiate `OrderQueueWorker(...)`.
3. Run `run_startup_reconciliation()` — queries QuestDB for non-terminal orders
   and calls `order_worker.restore_open_order()` for each.
4. Wire up `strategy._exchange_client`, `strategy._order_worker`.
5. Create `StrategyHandle` with a new asyncio event loop thread.
6. Register pub/sub callbacks if `orderbook_mode != "none"`.
7. Collect all required Redis stream keys (`candles:close:*` + `candles:ob:*`).
8. Register with `BusManager`.

---

## 8. `DailyLossCircuitBreaker`

[`bot-service/bot_service/strategy/circuit_breaker.py`](../../bot-service/bot_service/strategy/circuit_breaker.py)

Tracks `daily_pnl` (resets at UTC midnight). If `daily_pnl < -limit_usd`:
1. Logs `CRITICAL daily_loss_limit_tripped`.
2. Calls the `on_circuit_breaker_trip` callback (provided by main).
3. Main's callback triggers `emergency_close()` on all strategies and SIGTERM.

The circuit breaker is shared across all strategies — if one strategy's losses
trip it, all strategies are stopped.

---

## 9. Exchange Clients

### Paper Trading [`exchange/paper.py`](../../bot-service/bot_service/exchange/paper.py)

Simulates fills synchronously: market orders fill immediately at mid-price,
limit orders fill when the price crosses the limit. Tracks positions in memory.
Used for forward-testing strategies without real capital.

### Live: KuCoin

- **REST** [`exchange/kucoin/rest.py`](../../bot-service/bot_service/exchange/kucoin/rest.py):
  `place_order()`, `cancel_order()`, `get_open_orders()`, `get_position()`.
- **WS Private** [`exchange/kucoin/ws_private.py`](../../bot-service/bot_service/exchange/kucoin/ws_private.py):
  Subscribes to `/spotMarket/tradeOrders` — delivers `OrderFilled` events to the
  strategy's `order_worker.handle_fill()`.

### Live: Bybit
Same pattern — REST + WS private channel (`order` topic) for fills.

---

## 10. Reconciliation

[`bot-service/bot_service/strategy/reconciliation.py`](../../bot-service/bot_service/strategy/reconciliation.py)

Called during `_load_new()` (and on watchdog restart):

1. Query QuestDB `order_events` for rows where `strategy = <name>` and
   `status NOT IN ('filled', 'cancelled', 'rejected', 'failed')`.
2. For each non-terminal order: call `exchange_client.get_order_status()` via REST.
3. If still open → `order_worker.restore_open_order()`.
4. If filled → write the fill event to QuestDB and update position state.
5. Query QuestDB for the last known position and call `order_worker.restore_position()`.

This ensures the worker's in-memory position tracking matches reality after a restart.

---

## 11. Backtest API

`POST /backtest` runs a strategy against historical QuestDB data:
1. Load the strategy class from the file path.
2. `BacktestRunner` replays `snapshot_1s` rows as `BarClose` events.
3. Paper exchange simulates fills against the replay data.
4. Results are written to the `backtest_runs` and `backtest_trades` QuestDB tables.
5. The dashboard's Backtests tab fetches and displays these results.

---

## 12. Configuration

[`bot-service/bot_service/config.py`](../../bot-service/bot_service/config.py)

Key settings (all from environment, prefix `BOT_`):

| Env var | Default | Purpose |
|---------|---------|---------|
| `BOT_EXCHANGE` | `paper` | `paper`, `kucoin`, or `bybit` |
| `BOT_STRATEGIES_DIR` | `strategies/active` | Directory to watch |
| `BOT_PORTFOLIO_VALUE_USD` | `1000` | Total portfolio size |
| `BOT_DAILY_LOSS_LIMIT_USD` | `0` | 0 = disabled |
| `BOT_HEARTBEAT_TIMEOUT_S` | `30` | Before emergency close |
| `BOT_SUBSCRIBE_TIMEOUT_S` | `10` | WS subscribe timeout |
| `BOT_FILEWATCHER_INTERVAL_S` | `5` | File poll interval |
| `BOT_QUEUE_MAX_DEPTH` | `1000` | Per-strategy queue size |
| `BOT_CONSUMER_GROUP` | `bot-service` | Redis consumer group |
| `BOT_RECONCILIATION_TIMEOUT_S` | `30` | Startup reconciliation timeout |
| `REDIS_URL` | `redis://redis:6379` | Redis connection URL |
| `QUESTDB_HTTP_ADDR` | `http://questdb:9000` | QuestDB HTTP |
| `QUESTDB_ILP_ADDR` | `questdb:9009` | QuestDB ILP TCP |

---

## 13. Writing a Strategy

Create `strategies/active/my_strategy.py`:

```python
from bot_service.strategy.base import BaseStrategy
from bot_service.bus.event_types import BarClose
from bot_service.exchange import OrderRequest

class MyStrategy(BaseStrategy):
    def __init__(self, name, settings):
        super().__init__(name, settings)
        self._bar_handlers = {"BTC-USDT": ["1m"]}
        self.max_position_pct = 0.05
        self.paper_trading = True

    def on_bar(self, bar: BarClose) -> None:
        if bar.symbol != "BTC-USDT" or bar.tf != "1m":
            return
        # Example: buy when close > open
        if bar.close and bar.open and bar.close > bar.open:
            self._order_worker.post(OrderRequest(
                strategy=self._name,
                exchange=self._exchange,
                symbol=bar.symbol,
                side="buy",
                order_type="market",
                size=0.001,
                order_role="entry",
            ))
```

The file watcher picks it up within `BOT_FILEWATCHER_INTERVAL_S` seconds. No restart needed.
