---
id: 29-1
title: BacktestResultWriter TCP connection reuse
epic: 29
status: ready-for-dev
---

# Story 29-1: BacktestResultWriter TCP connection reuse

## Context

Fixes D-14-5-1. In `bot_service/backtest/writer.py`, `_sync_ilp_write` opens a new `Sender.from_conf(...)` TCP connection for every fill row. For a backtest with thousands of fills this means thousands of separate TLS/auth handshakes. The fix: open one `Sender` at construction time, reuse it for all writes, and close it on explicit teardown.

## What to build

### `bot_service/backtest/writer.py`

Restructure `BacktestResultWriter` to be a context manager that holds a single `Sender`:

```python
class BacktestResultWriter:
    def __init__(self, questdb_ilp_addr: str, strategy_name: str, exchange: str, symbol: str, fee_currency: str = "USDT") -> None:
        self._questdb_ilp_addr = questdb_ilp_addr
        self._strategy_name = strategy_name
        self._exchange = exchange
        self._symbol = symbol
        self._fee_currency = fee_currency
        self._tracker = _CostBasisTracker()
        self._sender: Sender | None = None

    def open(self) -> None:
        """Open the QuestDB ILP TCP connection."""
        host, _, port_str = self._questdb_ilp_addr.partition(":")
        self._sender = Sender.from_conf(f"tcp::addr={host}:{port_str or '9009'};")

    def close(self) -> None:
        """Flush and close the QuestDB ILP TCP connection."""
        if self._sender is not None:
            try:
                self._sender.flush()
                self._sender.close()
            except Exception as exc:
                log.warning("backtest_ilp_close_failed", error=str(exc))
            finally:
                self._sender = None

    def __enter__(self) -> BacktestResultWriter:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
```

In `write_fill`, replace `with Sender.from_conf(...) as sender:` with using `self._sender` directly. If `self._sender` is None (writer not opened), log CRITICAL and return — do not raise (fire-and-forget pattern).

Also update `run_backtest_and_persist` to use the writer as a context manager:

```python
def run_backtest_and_persist(strategy_cls, feed, writer, commission_info=None, starting_cash=10_000.0):
    with writer:
        # existing implementation
```

If the caller already opened the writer (already in `with` block), this would double-open. Use a `_is_open: bool` guard: `open()` is a no-op if already open; `close()` is safe to call multiple times.

## Acceptance Criteria

- `write_fill` called 1000 times on one `BacktestResultWriter` instance opens exactly 1 TCP connection (not 1000).
- `run_backtest_and_persist` opens and closes the sender once across the full run.
- If writer is used as context manager, `__exit__` flushes and closes even if an exception occurred during the run.
- Unit tests: mock `Sender.from_conf`; assert it is called once regardless of fill count (3+ fills).
- Existing `test_writer.py` tests still pass.

## Files
- `bot-service/bot_service/backtest/writer.py`
- `bot-service/tests/backtest/test_writer.py` (extend)

### Review Findings

- [x] [Review][Patch] `open()` uses `split(":")` — crashes on port-free address; changed to `partition(":")` with fallback port `"9009"` [`writer.py`]
- [x] [Review][Defer] Double flush in `close()` after per-row `flush()` failure — semantically redundant but harmless; fire-and-forget contract accepted
