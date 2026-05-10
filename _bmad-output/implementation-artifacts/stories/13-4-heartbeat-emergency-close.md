# Story 13.4: Heartbeat Emergency Close

## Status: done

## Story

**As** mrqdt,
**I want** a per-strategy configurable heartbeat that can emergency-close all open positions if the event bus goes silent,
**so that** microstructure strategies are never left holding positions when their signal feed dies.

## Acceptance Criteria

- **AC1:** Given a strategy class with `bus_timeout_seconds: int` and `close_on_bus_timeout: bool` abstract class attributes, when no BarClose is received for `bus_timeout_seconds` seconds, then the heartbeat OS thread fires: always logs WARN with strategy name, open_positions snapshot, and elapsed_seconds; always increments `bot_heartbeat_timeout_total{strategy}`; if `close_on_bus_timeout=True`, issues market-sell orders for all entries in `strategy._open_positions` via direct exchange REST (NOT via the asyncio order queue); if `close_on_bus_timeout=False`, logs and increments only.

- **AC2:** Given `close_on_bus_timeout=True` and the emergency close path, when market-sell orders are issued, then they are sent directly via `asyncio.run(exchange_client.place_order(req))` from the heartbeat OS thread — NOT via the Redis-backed order queue — so the emergency close works even if the event loop is frozen or Redis is down.

- **AC3:** Given an emergency close REST call that fails, when the exception is raised, then it is logged CRITICAL with symbol, side, quantity, and error; each symbol's close runs in its own daemon thread so one symbol's failure does NOT suppress close attempts for other open positions; each symbol's thread retries every 5 seconds until success or process exit.

- **AC4:** Given `bus_timeout_seconds` and `close_on_bus_timeout` defined as `@property @abstractmethod` on BaseStrategy, when a concrete strategy subclass omits either attribute, then `mypy --strict` raises an error at import time; test stubs and dynamically-loaded strategies must implement both.

## Tasks / Subtasks

- [x] T1: Add `inc_heartbeat_timeout` metric to prometheus.py (AC1)
  - [x] T1.1: Add `_heartbeat_timeout: Counter | None = None` module-level var
  - [x] T1.2: Add `inc_heartbeat_timeout(strategy: str) -> None` following existing counter pattern

- [x] T2: Add abstract properties to BaseStrategy (AC4)
  - [x] T2.1: Add `@property @abstractmethod def bus_timeout_seconds(self) -> int: ...`
  - [x] T2.2: Add `@property @abstractmethod def close_on_bus_timeout(self) -> bool: ...`
  - [x] T2.3: Update all test stubs (test_base_strategy.py, test_reconciliation.py, test_startup_reconciliation.py) to add both properties
  - [x] T2.4: Update `_minimal_strategy_src` in test_file_watcher.py and test_watchdog.py to include both properties

- [x] T3: Add bus timeout instance state to BaseStrategy.__init__ (AC1, AC2)
  - [x] T3.1: Add `self._last_event_ts: float = time.time()`
  - [x] T3.2: Add `self._open_positions: dict[str, float] = {}` (symbol → base-currency quantity)
  - [x] T3.3: Add `self._exchange_client: ExchangeClient | None = None` (set by registry before start_heartbeat)
  - [x] T3.4: Add `self._exchange: str = ""` (set by registry before start_heartbeat)
  - [x] T3.5: Add `ExchangeClient` and `OrderRequest` imports to base.py

- [x] T4: Update `on_bar` to track last event timestamp (AC1)
  - [x] T4.1: Set `self._last_event_ts = time.time()` at the start of `on_bar`

- [x] T5: Add bus timeout thread to `start_heartbeat` (AC1)
  - [x] T5.1: In `start_heartbeat`, spawn `_bus_timeout_loop` as second daemon thread named `bus-timeout-{name}`

- [x] T6: Implement `_bus_timeout_loop` (AC1, AC2)
  - [x] T6.1: Implement `_bus_timeout_loop`: 1s sleep poll; check `time.time() - self._last_event_ts >= self.bus_timeout_seconds`; on timeout call `_on_bus_timeout(elapsed)`
  - [x] T6.2: Implement `_on_bus_timeout(elapsed: float) -> None`: log WARN, inc counter, spawn per-symbol close threads if `close_on_bus_timeout=True` and `_exchange_client is not None`

- [x] T7: Implement `_emergency_close_symbol` (AC2, AC3)
  - [x] T7.1: Implement `_emergency_close_symbol(symbol: str, qty: float) -> None` — called in daemon thread; loop: build `OrderRequest(side="sell", order_type="market", order_role="exit")`; `asyncio.run(exchange_client.place_order(req))`; on success remove from `_open_positions` and return; on exception log CRITICAL and sleep 5s before retry

- [x] T8: Update registry._load_new to inject exchange client (AC2)
  - [x] T8.1: Set `strategy._exchange_client = self._exchange_client` before `_create_handle_and_thread`
  - [x] T8.2: Set `strategy._exchange = self._exchange` before `_create_handle_and_thread`

- [x] T9: Update reconciliation to populate `_open_positions` (AC1)
  - [x] T9.1: In `run_startup_reconciliation`, after restoring open_orders, set `strategy._open_positions[row["symbol"]] = float(row.get("requested_size", 0.0))` for each restored order (all restoration paths: paper, degraded, normal). Note: field is `requested_size`, not `size`.

- [x] T10: Write L1 tests in `tests/test_heartbeat.py` (all ACs)
  - [x] T10.1: `test_bus_timeout_close_on_true_calls_rest` — elapsed > timeout, close=True → place_order called
  - [x] T10.2: `test_bus_timeout_close_on_false_no_rest` — elapsed > timeout, close=False → no place_order
  - [x] T10.3: `test_bus_timeout_increments_counter` — timeout fires → inc_heartbeat_timeout called
  - [x] T10.4: `test_emergency_close_retry_on_failure` — first place_order raises → logs CRITICAL, retries
  - [x] T10.5: `test_emergency_close_multiple_positions_no_suppression` — two positions, first fails → second still attempted

- [x] T11: All tests pass — mypy --strict clean

## Dev Notes

### What already exists (do not reinvent)

**`bot_service/strategy/base.py`** — read the COMPLETE file before touching. Key existing state:
- `_heartbeat_ack: threading.Event` — for freeze detection (existing, separate from bus timeout)
- `start_heartbeat()` — spawns `_heartbeat_loop` daemon thread; THIS story adds a SECOND thread
- `_heartbeat_loop()` — posts threading.Event every 5s, expects ack within 10s, sends SIGTERM on timeout
- `ack_heartbeat()` — called by event loop every ~1s to signal liveness; unchanged by this story
- `_managed_positions: set[str]` — symbols with open positions; DIFFERENT from `_open_positions` dict (which tracks qty)
- `_loop: asyncio.AbstractEventLoop | None = None` — set by registry; follow same pattern for `_exchange_client`

**`bot_service/exchange/__init__.py`** — `ExchangeClient` is a Protocol (structural). `OrderRequest.size` is in base currency (e.g., BTC for BTCUSDT). For market sell: `side="sell"`, `order_type="market"`, `order_role="exit"`. `BybitRESTClient.place_order` creates fresh `httpx.AsyncClient` per request — safe to call via `asyncio.run()` from OS thread.

**`bot_service/strategy/reconciliation.py`** — `run_startup_reconciliation` already restores orders from QuestDB. Row dict keys include `symbol` and `size`. Set `strategy._open_positions[row["symbol"]] = row["size"]` in T9 in all three restoration branches (paper, degraded, normal). The function signature is unchanged.

**`bot_service/metrics/prometheus.py`** — follow the exact same lazy-init pattern as every other counter. Module-level `_heartbeat_timeout: Counter | None = None`, lazy init under `_lock`, then `counter.labels(strategy=strategy).inc()`.

**`bot_service/strategy/registry.py`** — `_load_new` already sets `strategy._loop = loop` inside `_create_handle_and_thread`. Set `strategy._exchange_client` and `strategy._exchange` directly in `_load_new` BEFORE calling `_create_handle_and_thread` (so they are available when `start_heartbeat()` is called inside the thread).

### Bus timeout vs freeze detection — two separate mechanisms

- **Freeze detection** (existing): detects frozen asyncio event loop. Even if no market events arrive, `ack_heartbeat()` fires every ~1s via `asyncio.TimeoutError` path. The freeze heartbeat WILL fire if the loop is genuinely frozen.
- **Bus timeout** (new): detects event bus silence — no BarClose events received. `_last_event_ts` is updated only in `on_bar()`. If market data stops but the asyncio loop keeps running, the freeze heartbeat stays happy but the bus timeout fires.

These are complementary. A strategy can configure both independently.

### Abstract property pattern (matches existing code)

```python
@property
@abstractmethod
def bus_timeout_seconds(self) -> int: ...

@property
@abstractmethod
def close_on_bus_timeout(self) -> bool: ...
```

All test stubs must implement these as properties:
```python
@property
def bus_timeout_seconds(self) -> int:
    return 300

@property
def close_on_bus_timeout(self) -> bool:
    return False
```

For `_minimal_strategy_src` (string-generated classes), class attributes suffice at runtime (Python allows class attrs to satisfy abstract properties), but add them for clarity:
```python
    bus_timeout_seconds = 300
    close_on_bus_timeout = False
```

### Emergency close threading design (AC3)

Each symbol's close runs in a separate daemon thread to avoid blocking:
```python
def _on_bus_timeout(self, elapsed: float) -> None:
    pos = dict(self._open_positions)  # snapshot
    log.warning("bus_timeout", strategy=self._name, open_positions=pos, elapsed_seconds=round(elapsed, 1))
    inc_heartbeat_timeout(self._name)
    if not self.close_on_bus_timeout or self._exchange_client is None:
        return
    for symbol, qty in pos.items():
        if qty <= 0.0:
            continue
        t = threading.Thread(
            target=self._emergency_close_symbol,
            args=(symbol, qty),
            daemon=True,
            name=f"emergency-close-{self._name}-{symbol}",
        )
        t.start()
```

### asyncio.run() from OS thread (AC2)

`asyncio.run()` is safe from an OS thread that has no running event loop. `BybitRESTClient` creates a fresh `httpx.AsyncClient` per request, so there is no shared state between event loops. `asyncio.run()` creates a new event loop, runs the coroutine, and closes the loop.

```python
def _emergency_close_symbol(self, symbol: str, qty: float) -> None:
    client = self._exchange_client
    if client is None:
        return
    while True:
        try:
            req = OrderRequest(
                strategy=self._name,
                exchange=self._exchange,
                symbol=symbol,
                side="sell",
                order_type="market",
                order_role="exit",
                size=qty,
                paper_trading=self.paper_trading,
            )
            asyncio.run(client.place_order(req))
            self._open_positions.pop(symbol, None)
            return
        except Exception as exc:
            log.critical(
                "emergency_close_failed",
                strategy=self._name,
                symbol=symbol,
                side="sell",
                qty=qty,
                error=str(exc),
            )
            time.sleep(5.0)
```

### mypy --strict imports for base.py

Add to imports:
```python
from bot_service.exchange import ExchangeClient, OrderRequest
```

### _open_positions population in reconciliation

In `run_startup_reconciliation`, in each restoration branch, add:
```python
strategy._open_positions[row["symbol"]] = row["size"]
```

This means all three branches (paper, degraded, normal) set position sizes from QuestDB data.

### Testing `_on_bus_timeout` directly (pattern from Story 13.3)

Test `_on_bus_timeout` and `_emergency_close_symbol` directly (same approach as testing `_handle_crash`):

```python
async def test_bus_timeout_close_on_true_calls_rest(tmp_path: Path) -> None:
    strategy = _make_strategy(bus_timeout_seconds=60, close_on_bus_timeout=True)
    mock_client = MagicMock()
    mock_client.place_order = AsyncMock(return_value=MagicMock())
    strategy._exchange_client = mock_client
    strategy._exchange = "bybit"
    strategy._open_positions = {"BTCUSDT": 0.001}

    # Patch asyncio.run so we can intercept the call without actually running it
    with patch("bot_service.strategy.base.asyncio.run") as mock_run:
        strategy._on_bus_timeout(elapsed=65.0)
        # Wait for daemon thread to start
        import time; time.sleep(0.2)

    mock_run.assert_called()
```

Note: testing with `asyncio.run` patched is cleaner than mocking `place_order` since the call is from an OS thread.

Alternative — call `_emergency_close_symbol` directly (no thread spawning):
```python
with patch("bot_service.strategy.base.asyncio.run") as mock_run:
    strategy._emergency_close_symbol("BTCUSDT", 0.001)

mock_run.assert_called_once()
```

### Counter check in tests

```python
from unittest.mock import patch
with patch("bot_service.strategy.base.inc_heartbeat_timeout") as mock_inc:
    strategy._on_bus_timeout(elapsed=65.0)
mock_inc.assert_called_once_with(strategy._name)
```

### Minimal stub for test file

```python
class _BusStrat(BaseStrategy):
    @property
    def min_lookback(self) -> int: return 1
    @property
    def max_position_pct(self) -> float: return 0.05
    @property
    def stop_loss_pct(self) -> float: return 0.02
    @property
    def paper_trading(self) -> bool: return True
    @property
    def bus_timeout_seconds(self) -> int: return 60
    @property
    def close_on_bus_timeout(self) -> bool: return True
    def subscribe(self) -> None: pass
```

### Review Findings

- [x] [Review][Patch] Thread explosion — `_on_bus_timeout` spawns new threads every 1s with no re-entry guard [base.py:_on_bus_timeout] — Fixed: added `_emergency_close_lock` + `_emergency_close_in_flight: set[str]`; gate in `_on_bus_timeout`, cleanup in `_emergency_close_symbol` finally block
- [x] [Review][Patch] Double market-sell risk within REST latency window — same root fix as above [base.py:_emergency_close_symbol]
- [x] [Review][Patch] PaperExchangeClient fill task silently cancelled by asyncio.run; skip paper trading [base.py:_emergency_close_symbol] — Fixed: early return with WARN log if `self.paper_trading`
- [x] [Review][Patch] Flaky tests using time.sleep(0.2/0.3) for daemon thread sync [tests/test_heartbeat.py] — Fixed: replaced with `threading.Event.wait(timeout=2.0)` in T10.1 and T10.5; added `_LiveCloseStrat` (paper_trading=False) for tests needing live REST path
- [x] [Review][Defer] `_open_positions` stale after live trading — only set at reconciliation [base.py] — deferred, out of scope for this story; Epic 16 position tracking will address
- [x] [Review][Dismiss] asyncio.run() from daemon thread correctness — no bug
- [x] [Review][Dismiss] >= vs > off-by-one — irrelevant for second-scale timeouts
- [x] [Review][Dismiss] _exchange_client injection timing — happens-before guaranteed by thread.start() chain
- [x] [Review][Dismiss] Circular import risk — no circular dependency confirmed
- [x] [Review][Dismiss] Class-level attrs for abstract properties in watchdog stubs — valid Python

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- T1: Added `inc_heartbeat_timeout(strategy)` to prometheus.py following existing lazy-init Counter pattern.
- T2: Added `bus_timeout_seconds` and `close_on_bus_timeout` as `@property @abstractmethod` to BaseStrategy. Updated all 5 stubs in test_reconciliation.py, 1 stub in test_startup_reconciliation.py, 1 stub in test_base_strategy.py, and both `_minimal_strategy_src` helpers in test_file_watcher.py / test_watchdog.py.
- T3: Added `_last_event_ts`, `_open_positions`, `_exchange_client`, `_exchange` to BaseStrategy.__init__; added `ExchangeClient` and `OrderRequest` imports.
- T4: Added `self._last_event_ts = time.time()` at top of `on_bar`.
- T5: `start_heartbeat` now spawns a second daemon thread (`bus-timeout-{name}`) running `_bus_timeout_loop`.
- T6: `_bus_timeout_loop` polls every 1s; fires `_on_bus_timeout(elapsed)` when elapsed >= bus_timeout_seconds. `_on_bus_timeout` logs WARN, increments counter, and spawns per-symbol daemon threads if `close_on_bus_timeout=True`.
- T7: `_emergency_close_symbol` loops: builds OrderRequest(market sell), calls `asyncio.run(client.place_order(req))`, removes from `_open_positions` on success, logs CRITICAL and sleeps 5s on failure before retry.
- T8: `registry._load_new` now sets `strategy._exchange_client` and `strategy._exchange` before calling `_create_handle_and_thread`.
- T9: `reconciliation.run_startup_reconciliation` now sets `strategy._open_positions[row["symbol"]] = float(row.get("requested_size", 0.0))` in all three restoration branches (paper, degraded, normal). Used `requested_size` (not `size`) to match the actual QuestDB query column name.
- T10: 5 L1 tests in `tests/test_heartbeat.py` — all pass. Tests cover close=True REST call, close=False no REST call, counter increment, retry on failure (CRITICAL log + 5s sleep), multiple positions (all attempted).
- T11: 157 tests pass (152 pre-existing + 5 new), mypy --strict clean (27 source files).

### File List

- bot_service/metrics/prometheus.py (UPDATE — add `inc_heartbeat_timeout`)
- bot_service/strategy/base.py (UPDATE — add abstract properties, bus timeout thread, emergency close)
- bot_service/strategy/reconciliation.py (UPDATE — populate `_open_positions` from QuestDB rows)
- bot_service/strategy/registry.py (UPDATE — inject `_exchange_client` and `_exchange` in `_load_new`)
- tests/test_base_strategy.py (UPDATE — add `bus_timeout_seconds` and `close_on_bus_timeout` to stubs)
- tests/test_reconciliation.py (UPDATE — add properties to stubs)
- tests/test_startup_reconciliation.py (UPDATE — add properties to stub)
- tests/test_file_watcher.py (UPDATE — update `_minimal_strategy_src`)
- tests/test_watchdog.py (UPDATE — update `_minimal_strategy_src`)
- tests/test_heartbeat.py (CREATE — 5 L1 unit tests)
