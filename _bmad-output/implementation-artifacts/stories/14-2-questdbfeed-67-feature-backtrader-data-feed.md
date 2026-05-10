# Story 14.2: QuestDBFeed — 67-Feature Backtrader Data Feed

## Status: done

## Story

**As** mrqdt,
**I want** a Backtrader data feed that queries QuestDB `snapshot_1s`, filters gap-contaminated bars, and exposes all microstructure features,
**so that** backtests use the same data as live signal computation with explicit gap handling.

## Acceptance Criteria

- **AC1:** Given `bot_service/backtest/feeds.py` with `QuestDBFeed(bt.feeds.PandasData)`, when instantiated with `exchange`, `symbol`, `start`, `end` parameters, then it queries QuestDB `snapshot_1s` via HTTP REST (`GET /exec`) for the given exchange/symbol/time range; returns a DataFrame sorted by `ts` ascending.

- **AC2:** Given rows in the query result where `gap_count > 0` (the `snapshot_1s` table has `gap_count INT`, not `has_gap BOOLEAN`), when the feed processes them, then each gap row is replaced with a NaN/NaT placeholder row (all numeric fields set to `float('nan')`, `ts` index set to the original timestamp); the index remains contiguous and sorted; Backtrader's `next()` can detect the gap by checking `math.isnan(self.data.close[0])`.

- **AC3:** Given the microstructure fields from `snapshot_1s`, when the feed is loaded into Backtrader, then each field is declared as a `bt.feeds.PandasData` line; all lines are accessible in strategy `next()` as `self.data.{field_name}[0]`; the OHLCV standard lines are mapped to the candle OHLCV columns.

- **AC4:** Given a QuestDB query that returns zero rows for the requested time range, when the feed is used, then it raises `InsufficientHistoryError` with exchange, symbol, start, and end; the backtest run does not silently proceed with an empty dataset.

## Tasks / Subtasks

- [x] T1: Define `InsufficientHistoryError` and `_fetch_snapshot_1s` query function
  - [x] T1.1: Created `bot_service/backtest/feeds.py`
  - [x] T1.2: `InsufficientHistoryError(RuntimeError)` with exchange/symbol/start/end attributes and __init__ message
  - [x] T1.3: `_fetch_snapshot_1s` with httpx.get, ts index, sorted ascending; `_validate_ident` SQL injection guard
  - [x] T1.4: 2 L1 tests for fetch (sorted, empty) + 1 for InsufficientHistoryError

- [x] T2: Implement gap-row replacement logic
  - [x] T2.1: L1 tests written before implementation
  - [x] T2.2: `_replace_gap_rows` — gap_count > 0 → NaN on all numeric+bool columns
  - [x] T2.3: ts index preserved; 2 L1 tests (gap replaced, no-gap unchanged)

- [x] T3: Implement `QuestDBFeed(bt.feeds.PandasData)`
  - [x] T3.1: 67 custom lines declared in `_CUSTOM_LINES` tuple
  - [x] T3.2: Standard OHLCV params + full custom params tuple
  - [x] T3.3: `__init__` fetches df, raises InsufficientHistoryError on empty, replaces gaps, sets `self.p.dataname` directly (backtrader metaclass workaround)
  - [x] T3.4: 2 L1 tests (empty raises, close line accessible)

- [x] T4: All tests pass — mypy --strict clean
  - [x] T4.1: mypy --strict clean (31 files); added `backtrader.*` override to pyproject.toml
  - [x] T4.2: 7 new L1 tests; 138 total green; no regressions

## Dev Notes

### `snapshot_1s` schema — complete field list

From `candle-service/migrations/001_snapshot_1s.sql`. The table has these columns (excluding the identity columns `ts`, `exchange`, `symbol`):

**OHLCV (8):** `open`, `high`, `low`, `close`, `volume`, `quote_volume`, `trade_count` (INT), `twap`

**Mid-price (4):** `mid_price_open`, `mid_price_high`, `mid_price_low`, `vwmp`

**Spread (4):** `spread_high`, `spread_low`, `spread_mean`, `effective_spread`

**OB best quotes (4):** `best_bid_open`, `best_ask_open`, `best_bid`, `best_ask`

**OB depth at open (8):** `bid_depth_l1_open`, `ask_depth_l1_open`, `bid_depth_l2_open`, `ask_depth_l2_open`, `bid_depth_top10_open`, `ask_depth_top10_open`, `bid_depth_total_open`, `ask_depth_total_open`

**OB depth at close (8):** `bid_depth_l1_close`, `ask_depth_l1_close`, `bid_depth_l2_close`, `ask_depth_l2_close`, `bid_depth_top10_close`, `ask_depth_top10_close`, `bid_depth_total_close`, `ask_depth_total_close`

**Book shape (2):** `weighted_bid_price`, `weighted_ask_price`

**Market impact (2):** `depth_to_1pct_bid`, `depth_to_1pct_ask`

**OFI (2):** `ofi`, `ofi_l1`

**Trade flow (2):** `buy_volume`, `buy_count` (INT)

**Block trades (2):** `block_buy_volume`, `block_sell_volume`

**Trade distribution (7):** `max_trade_size`, `large_bid_orders` (INT), `large_ask_orders` (INT), `first_trade_offset_ms` (INT), `last_trade_offset_ms` (INT), `trade_clustering`, `max_consecutive_run` (INT)

**Volatility (4):** `realized_vol`, `realized_skewness`, `uptick_count` (INT), `downtick_count` (INT)

**OB activity (10):** `bid_order_arrivals` (INT), `ask_order_arrivals` (INT), `bid_cancel_count` (INT), `ask_cancel_count` (INT), `ob_modify_count` (INT), `avg_bid_order_size`, `avg_ask_order_size`, `best_bid_changes` (INT), `best_ask_changes` (INT), `quote_stuff_ratio`

**Trade microstructure (3):** `trade_sign_autocorr`, `inter_trade_interval_std_ms`, `num_trade_price_levels` (INT)

**Quality (3):** `is_partial` (BOOLEAN), `gap_count` (INT), `bar_count` (INT)

Total non-identity feature columns: 8+4+4+4+8+8+2+2+2+2+2+7+4+10+3+3 = 73 (the "67 microstructure features" in the spec excludes `ts_second` used in some references but the actual implementable count from DDL is 73; expose all 73).

**There is NO `has_gap` BOOLEAN column in `snapshot_1s`.** Use `gap_count > 0` as the gap indicator.

### QuestDB HTTP query pattern

Use the same synchronous `httpx.get` pattern established in `bot_service/strategy/reconciliation.py`:

```python
import httpx, pandas as pd
from datetime import datetime

def _fetch_snapshot_1s(
    questdb_http_addr: str,
    exchange: str,
    symbol: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    ts_start = int(start.timestamp() * 1_000_000)  # QuestDB uses microseconds
    ts_end   = int(end.timestamp()   * 1_000_000)
    query = (
        f"SELECT * FROM snapshot_1s "
        f"WHERE exchange = '{exchange}' AND symbol = '{symbol}' "
        f"AND ts >= {ts_start} AND ts < {ts_end} "
        f"ORDER BY ts ASC"
    )
    resp = httpx.get(
        f"{questdb_http_addr}/exec",
        params={"query": query},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    cols = [c["name"] for c in data.get("columns", [])]
    rows = data.get("dataset", [])
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows, columns=cols)
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").sort_index()
    return df
```

**WARNING: Do NOT use f-string interpolation for exchange/symbol without input validation.** Add a regex allowlist check identical to `_STRATEGY_NAME_RE` from reconciliation.py for both `exchange` and `symbol` before interpolating into the query.

### Gap row replacement

```python
def _replace_gap_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Replace rows where gap_count > 0 with all-NaN, preserving ts index."""
    gap_mask = df["gap_count"] > 0
    numeric_cols = df.select_dtypes(include=["number", "bool"]).columns
    df = df.copy()
    df.loc[gap_mask, numeric_cols] = float("nan")
    return df
```

After replacement, the ts index remains contiguous — there are no dropped rows, only NaN-filled rows. Backtrader's `next()` detects gap bars via `math.isnan(self.data.close[0])`.

### `bt.feeds.PandasData` extension pattern

The canonical way to add custom lines to a PandasData feed:

```python
import backtrader as bt

_CUSTOM_LINES = (
    "twap", "mid_price_open", "mid_price_high", "mid_price_low", "vwmp",
    # ... all other non-OHLCV columns
)

class QuestDBFeed(bt.feeds.PandasData):
    # Standard OHLCV lines are already in bt.feeds.PandasData.
    # Add all custom microstructure lines:
    lines = _CUSTOM_LINES  # type: ignore[assignment]

    # Map standard lines to our DataFrame column names.
    # PandasData default params: open=-1 means "not present" — override to actual column name.
    params = (
        ("datetime", None),     # use DataFrame index
        ("open", "open"),
        ("high", "high"),
        ("low", "low"),
        ("close", "close"),
        ("volume", "volume"),
        ("openinterest", -1),   # not present in snapshot_1s
        # Custom lines — map each to its column name string:
        ("twap", "twap"),
        ("mid_price_open", "mid_price_open"),
        # ... all others
    ) + tuple((col, col) for col in _CUSTOM_LINES if col not in (
        "twap", "mid_price_open", ...  # already listed above
    ))
```

**Simpler pattern using dict comprehension for custom lines:**

```python
# Build params as a tuple of 2-tuples: (line_name, df_col_name)
_STANDARD_PARAMS: tuple[tuple[str, str | int], ...] = (
    ("datetime", None),
    ("open", "open"),
    ("high", "high"),
    ("low", "low"),
    ("close", "close"),
    ("volume", "volume"),
    ("openinterest", -1),
)
_CUSTOM_PARAMS: tuple[tuple[str, str], ...] = tuple(
    (col, col) for col in _CUSTOM_LINES
)

class QuestDBFeed(bt.feeds.PandasData):
    lines: tuple[str, ...] = _CUSTOM_LINES
    params: tuple[tuple[str, str | int | None], ...] = _STANDARD_PARAMS + _CUSTOM_PARAMS
```

**mypy note:** `bt.feeds.PandasData` uses class-level `lines` and `params` with metaclass magic. mypy --strict will flag them as incompatible. Use `# type: ignore[assignment]` on the class body assignments as needed (not a blanket file-level ignore).

### `InsufficientHistoryError`

```python
class InsufficientHistoryError(RuntimeError):
    def __init__(self, exchange: str, symbol: str, start: datetime, end: datetime) -> None:
        super().__init__(
            f"No snapshot_1s data for {exchange}:{symbol} [{start} → {end}]"
        )
        self.exchange = exchange
        self.symbol = symbol
        self.start = start
        self.end = end
```

### Full `QuestDBFeed.__init__` flow

```python
def __init__(
    self,
    questdb_http_addr: str,
    exchange: str,
    symbol: str,
    start: datetime,
    end: datetime,
    **kwargs: Any,
) -> None:
    df = _fetch_snapshot_1s(questdb_http_addr, exchange, symbol, start, end)
    if df.empty:
        raise InsufficientHistoryError(exchange, symbol, start, end)
    df = _replace_gap_rows(df)
    # Drop exchange and symbol columns — Backtrader doesn't need them as lines
    df = df.drop(columns=["exchange", "symbol"], errors="ignore")
    super().__init__(dataname=df, **kwargs)
```

### Input validation (SQL injection guard)

```python
import re

_IDENT_RE = re.compile(r"^[A-Za-z0-9_\-\.]+$")

def _validate_ident(value: str, field: str) -> None:
    if not _IDENT_RE.match(value):
        raise ValueError(f"Invalid {field}: {value!r}")
```

Call `_validate_ident(exchange, "exchange")` and `_validate_ident(symbol, "symbol")` in `_fetch_snapshot_1s` before string interpolation.

### Learnings from Story 14.1 review

- Always guard against missing columns before DataFrame access (KeyError violates never-raise invariant)
- Add `__post_init__` or equivalent validation so constraints are enforced in the type itself
- Test helpers should use deterministic, explicit data rather than relying on floating-point EWM convergence
- `# type: ignore[misc]` or `# type: ignore[assignment]` are acceptable for backtrader metaclass conflicts — add per-line, not file-wide

### File to NOT touch

- `bot_service/strategy/signals/` — Story 14.1 owns this, don't modify
- `bot_service/backtest/__init__.py` — leave as `from __future__ import annotations` stub
- `bot_service/strategy/base.py` — no changes needed

## Test Coverage

All tests `@pytest.mark.l1`. Target: 7 tests.

### `InsufficientHistoryError` tests (1)
1. `test_insufficient_history_error` — construct with known args, assert str contains exchange+symbol, assert `.exchange` and `.symbol` attributes

### `_fetch_snapshot_1s` tests (2)
2. `test_fetch_returns_dataframe_sorted_by_ts` — mock httpx.get to return 3 out-of-order rows; assert result is DataFrame with ts as DatetimeIndex sorted ascending
3. `test_fetch_empty_returns_empty_dataframe` — mock returns empty dataset; assert `df.empty` is True

### Gap replacement tests (2)
4. `test_gap_rows_replaced_with_nan` — DataFrame with 3 rows (gap_count=[0,1,0]); after `_replace_gap_rows`, row 1 has NaN close; rows 0 and 2 have original values; ts index unchanged
5. `test_no_gap_rows_unchanged` — DataFrame with all gap_count=0; after replacement, all values identical to input

### `QuestDBFeed` tests (2)
6. `test_questdbfeed_raises_on_empty_data` — mock `_fetch_snapshot_1s` to return empty DataFrame; `QuestDBFeed(...)` raises `InsufficientHistoryError`
7. `test_questdbfeed_has_close_line` — mock `_fetch_snapshot_1s` to return 10-row DataFrame with all required columns; construct `QuestDBFeed`; assert `feed.close` is accessible (line exists)

**Note:** Full 73-line accessibility (`self.data.ofi[0]`, etc.) is an L2 test against real QuestDB. Story 14.2 only has L1 tests. The L2 integration test is deferred — it requires a live QuestDB container (testcontainers) and a populated `snapshot_1s` table, which is best validated in Story 14.5 as part of backtest end-to-end.

## Senior Developer Review (AI)

Review applied. Key findings resolved:
- `_replace_gap_rows`: changed to `select_dtypes(include=["number"]).columns.difference(["gap_count"])` — bool `is_partial` column no longer receives float NaN; `gap_count` retained for diagnostics
- `_fetch_snapshot_1s`: added timezone-aware guard (`ValueError` on naive datetimes) and QuestDB error-field detection (`RuntimeError` on `{"error": ...}` HTTP 200 responses)
- `_full_df` test fixture: expanded from 13 to all 79 columns — cerebro.run() now exercises the full line mapping
- `_make_ts`: fixed `f"0{hour}"` → `f"{hour:02d}"` for zero-padding correctness
- Added 3 tests: cerebro integration run, naive datetime guard, QuestDB error response

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### File List

- `bot_service/backtest/feeds.py` (CREATE)
- `bot_service/backtest/__init__.py` (existing stub, not modified)
- `bot_service/pyproject.toml` (UPDATE — backtrader.* mypy override)
- `tests/test_feeds.py` (CREATE)
