# Story 14.1: Shared Signal Layer — Pure Functions in strategy/signals/

## Status: review

## Story

**As** mrqdt,
**I want** all signal logic extracted into pure functions with no framework dependency,
**so that** the same function is called identically by live BaseStrategy handlers and Backtrader's `next()` — written once, tested once.

## Acceptance Criteria

- **AC1:** Given `bot_service/strategy/signals/` directory, when any `.py` file in it is inspected, then it contains no imports from `backtrader`, `bot_service.strategy.base`, `bot_service.bus_manager`, or any other framework module; functions accept pandas `DataFrame` and scalar parameters only and return typed signal objects (`SignalResult` dataclass).

- **AC2:** Given `SignalResult: @dataclass(frozen=True)` with fields `action: Literal['buy','sell','hold']`, `confidence: float`, `reason: str`, when a signal function is called, then it always returns a `SignalResult`; it never raises; if inputs are insufficient it returns `SignalResult(action='hold', confidence=0.0, reason='insufficient_data')`.

- **AC3:** Given a live `BaseStrategy` handler and a Backtrader `bt.Strategy.next()` calling the same signal function, when both are passed identical DataFrames, then both receive identical `SignalResult` outputs; there is no conditional import or runtime branching on live-vs-backtest context within the signal function.

- **AC4:** Given mypy --strict run on `strategy/signals/`, when executed, then zero errors; all function signatures are fully annotated including return types.

## Tasks / Subtasks

- [x] T1: Define `SignalResult` in `bot_service/strategy/signals/__init__.py`
  - [x] T1.1: `@dataclass(frozen=True)` with `action: Literal['buy','sell','hold']`, `confidence: float`, `reason: str`
  - [x] T1.2: Write failing L1 tests for SignalResult immutability and field defaults

- [x] T2: Implement `ma_cross_signal` in `bot_service/strategy/signals/ma_cross.py`
  - [x] T2.1: Function signature: `ma_cross_signal(df: pd.DataFrame, fast: int = 20, slow: int = 50) -> SignalResult`
  - [x] T2.2: Uses pure pandas `ewm()` — no pandas_ta import, fully importable with no side-effects
  - [x] T2.3: Insufficient data (< `slow` rows, or NaN in EMA columns) → return hold with reason='insufficient_data'
  - [x] T2.4: Cross-up (fast crosses above slow) → buy; cross-down → sell; no cross → hold
  - [x] T2.5: 5 L1 tests written and passing

- [x] T3: Implement `ofi_signal` in `bot_service/strategy/signals/ofi_signal.py`
  - [x] T3.1: Function signature: `ofi_signal(df: pd.DataFrame, lookback: int = 30, threshold: float = 0.6) -> SignalResult`
  - [x] T3.2: Uses `ofi` column from `snapshot_1s` (pre-computed, no re-derivation needed)
  - [x] T3.3: Insufficient data (< `lookback` non-NaN rows) → hold with reason='insufficient_data'
  - [x] T3.4: Rolling z-score of `ofi` over `lookback` bars; z > `threshold` → buy; z < -threshold → sell; else hold
  - [x] T3.5: Confidence = `min(abs(z_score) / (threshold * 2), 1.0)` — capped at 1.0
  - [x] T3.6: 5 L1 tests written and passing

- [x] T4: Create stub `bot_service/strategy/signals/funding_rate_arb.py`
  - [x] T4.1: `funding_rate_arb_signal(funding_rate, threshold_bps) -> SignalResult` — returns hold/not_implemented
  - [x] T4.2: Scalar float parameter (no DataFrame)
  - [x] T4.3: 1 L1 test confirming stub returns hold

- [x] T5: All tests pass — mypy --strict clean
  - [x] T5.1: `mypy --strict bot_service/` → 0 errors (30 source files)
  - [x] T5.2: 13 new L1 tests green; 127 total L1 green; no regressions

## Dev Notes

### What already exists (do not reinvent)

- `bot_service/strategy/signals/__init__.py` exists but is EMPTY — add `SignalResult` there
- `backtest/__init__.py` exists but is EMPTY — leave it alone (Story 14.2 fills it)
- `pandas_ta` is installed and the `df.ta` accessor is imported in `base.py` via `import pandas_ta as ta  # noqa: F401` — the accessor registers itself on import; signal functions can call `df.ta.ema(length=N)` directly without importing pandas_ta themselves
- `bot_service/strategy/base.py` already imports `from bot_service.bus.event_types import BarClose` — the `BarClose` event has a `df` attribute that is the rolling history DataFrame

### `SignalResult` design

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class SignalResult:
    action: Literal['buy', 'sell', 'hold']
    confidence: float  # 0.0 to 1.0
    reason: str

HOLD_INSUFFICIENT = SignalResult(action='hold', confidence=0.0, reason='insufficient_data')
```

Export `SignalResult` and `HOLD_INSUFFICIENT` from `signals/__init__.py`. All signal modules import from `bot_service.strategy.signals` (not `__init__` directly).

### `ma_cross.py` architecture

The MA cross is the simplest reference implementation:

```python
from __future__ import annotations

import pandas as pd

from bot_service.strategy.signals import HOLD_INSUFFICIENT, SignalResult


def ma_cross_signal(
    df: pd.DataFrame, fast: int = 20, slow: int = 50
) -> SignalResult:
    if len(df) < slow:
        return HOLD_INSUFFICIENT
    # pandas_ta .ta accessor registered by importing pandas_ta in base.py;
    # signal functions must NOT import pandas_ta themselves (no framework dependency)
    # Instead use the pre-registered accessor on the df object.
    fast_col = f"EMA_{fast}"
    slow_col = f"EMA_{slow}"
    df = df.copy()
    df[fast_col] = df["close"].ewm(span=fast, adjust=False).mean()
    df[slow_col] = df["close"].ewm(span=slow, adjust=False).mean()
    ...
```

**IMPORTANT:** Signal functions must NOT import `pandas_ta` — the project-context explicitly forbids framework imports in `strategy/signals/`. Use `pd.Series.ewm()` for EMA computation directly. This keeps signals importable with no framework side-effects.

The pandas_ta accessor trick only works in the live strategy context where `base.py` has already registered it. For the pure signal functions, use pure pandas operations:
- EMA: `series.ewm(span=N, adjust=False).mean()`
- Rolling z-score: `(series - series.rolling(N).mean()) / series.rolling(N).std()`

### `ofi_signal.py` architecture

The `ofi` column is already in `snapshot_1s` and delivered in the DataFrame:

```python
def ofi_signal(
    df: pd.DataFrame, lookback: int = 30, threshold: float = 0.6
) -> SignalResult:
    if "ofi" not in df.columns:
        return HOLD_INSUFFICIENT
    ofi = df["ofi"].dropna()
    if len(ofi) < lookback:
        return HOLD_INSUFFICIENT
    recent = ofi.iloc[-lookback:]
    mean = recent.mean()
    std = recent.std()
    if std == 0.0:
        return SignalResult(action='hold', confidence=0.0, reason='zero_variance')
    z = (ofi.iloc[-1] - mean) / std
    confidence = min(abs(z) / (threshold * 2), 1.0)
    if z > threshold:
        return SignalResult(action='buy', confidence=confidence, reason=f'ofi_z={z:.2f}')
    if z < -threshold:
        return SignalResult(action='sell', confidence=confidence, reason=f'ofi_z={z:.2f}')
    return SignalResult(action='hold', confidence=confidence, reason=f'ofi_z={z:.2f}')
```

### `funding_rate_arb.py` stub

```python
def funding_rate_arb_signal(
    funding_rate: float, threshold_bps: float = 10.0
) -> SignalResult:
    return SignalResult(action='hold', confidence=0.0, reason='not_implemented')
```

Note that this function takes a scalar `float`, not a DataFrame. This is intentional — funding rate arb is based on a rate event, not bar history. Epic 15 will replace the stub body.

### Cross-cutting: no framework imports rule

The no-framework-imports constraint means:
- NO: `import pandas_ta`
- NO: `from bot_service.strategy.base import BaseStrategy`
- NO: `import backtrader`
- YES: `import pandas as pd`
- YES: `import math`
- YES: `from bot_service.strategy.signals import SignalResult, HOLD_INSUFFICIENT`

Signal functions MUST be importable in a fresh Python process with only `pandas` installed. Add an L1 test that imports the module in a subprocess (or via `importlib`) to verify no side-effects.

### mypy compliance checklist

- `Literal['buy', 'sell', 'hold']` requires `from typing import Literal`
- `from __future__ import annotations` at top of every file
- Every function parameter annotated (including `df: pd.DataFrame`, `fast: int`, etc.)
- Return type `-> SignalResult` explicit on every function
- No `Any` without inline comment

### File layout

Files to CREATE (all new):
- `bot_service/strategy/signals/__init__.py` (UPDATE from empty — add SignalResult + HOLD_INSUFFICIENT)
- `bot_service/strategy/signals/ma_cross.py` (CREATE)
- `bot_service/strategy/signals/ofi_signal.py` (CREATE)
- `bot_service/strategy/signals/funding_rate_arb.py` (CREATE)
- `tests/test_signals.py` (CREATE — 12 L1 tests total)

Files to NOT TOUCH:
- `bot_service/strategy/base.py` — no changes needed for this story
- `bot_service/backtest/__init__.py` — Story 14.2 fills this

## Test Coverage

All tests are `@pytest.mark.l1`. Target: 12 tests.

### SignalResult tests (2)
1. `test_signal_result_frozen` — attempt `result.action = 'buy'` on existing result → raises `FrozenInstanceError`
2. `test_hold_insufficient_constant` — `HOLD_INSUFFICIENT.action == 'hold'` and `reason == 'insufficient_data'`

### ma_cross_signal tests (5)
3. `test_ma_cross_insufficient_data` — DataFrame with 10 rows, `slow=50` → returns `HOLD_INSUFFICIENT`
4. `test_ma_cross_nan_rows` — DataFrame with 60 rows all NaN close → returns hold (reason = 'insufficient_data')
5. `test_ma_cross_buy_signal` — construct DataFrame where fast EMA just crossed above slow EMA → returns `SignalResult(action='buy', ...)`
6. `test_ma_cross_sell_signal` — construct DataFrame where fast EMA just crossed below slow EMA → returns `SignalResult(action='sell', ...)`
7. `test_ma_cross_hold_no_cross` — flat close price sequence → returns hold (no cross)

### ofi_signal tests (5)
8. `test_ofi_insufficient_data` — DataFrame with 10 rows, `lookback=30` → `HOLD_INSUFFICIENT`
9. `test_ofi_no_ofi_column` — DataFrame with 60 rows but no `ofi` column → `HOLD_INSUFFICIENT`
10. `test_ofi_buy_signal` — 30-row DataFrame with large positive final OFI relative to mean → `SignalResult(action='buy', ...)`
11. `test_ofi_sell_signal` — 30-row DataFrame with large negative final OFI → `SignalResult(action='sell', ...)`
12. `test_ofi_zero_variance` — all OFI values identical → `SignalResult(action='hold', reason='zero_variance', ...)`

### funding_rate_arb_signal test (1 in existing test count)
Included in the 12 above count as test 2 covers HOLD_INSUFFICIENT constant which applies.
Add: `test_funding_rate_arb_stub` — `funding_rate_arb_signal(100.0)` returns `SignalResult(action='hold', reason='not_implemented', ...)`

*(Total: 13 L1 tests)*

### Constructing test DataFrames

For MA cross tests, build DataFrames with a synthetic close series:
```python
import numpy as np, pandas as pd

def _make_df(n: int, close: list[float] | None = None) -> pd.DataFrame:
    if close is None:
        close = [100.0 + i * 0.01 for i in range(n)]
    return pd.DataFrame({"close": close, "ofi": [0.0] * n})
```

For cross-up: create a series that trends down for 40 bars then sharply reverses up for the last 20 — the fast EMA will cross above the slow EMA near the end.

## Senior Developer Review (AI)

**Date:** 2026-05-10
**Outcome:** Approved — all 10 findings addressed (6 patched, 4 test improvements); 131 tests green, mypy clean

### Review Findings

- [x] [Review][Patch] ma_cross_signal raises KeyError on missing 'close' column — added `if "close" not in df.columns` guard [ma_cross.py]
- [x] [Review][Patch] ofi_signal uses stale non-null data when trailing bars have NaN OFI — added `if pd.isna(df["ofi"].iloc[-1])` guard [ofi_signal.py]
- [x] [Review][Patch] ofi_signal returns NaN confidence when lookback=1 (std of 1 element = NaN) — changed guard to `if not (std > 0)` and added `lookback < 2` precondition [ofi_signal.py]
- [x] [Review][Patch] ofi_signal NaN confidence when OFI contains Inf — added `replace([inf, -inf], nan).dropna()` with length recheck [ofi_signal.py]
- [x] [Review][Patch] ma_cross_signal raises ValueError on fast=0 or slow=0 — added `if fast < 1 or slow < 1` precondition [ma_cross.py]
- [x] [Review][Patch] ma_cross_signal off-by-one: guard `< slow` → `< slow + 1` for proper warm-up + prev/curr validity [ma_cross.py]
- [x] [Review][Patch] SignalResult does not enforce confidence ∈ [0,1] — added `__post_init__` with ValueError [signals/__init__.py]
- [x] [Review][Test] test_ma_cross_nan_rows asserted `action=='hold'` not `== HOLD_INSUFFICIENT` — fixed to strict equality [test_signals.py]
- [x] [Review][Test] No test for missing 'close' column — added `test_ma_cross_no_close_column` and `test_ma_cross_invalid_span` [test_signals.py]
- [x] [Review][Test] No test for trailing NaN staleness in ofi_signal — added `test_ofi_trailing_nan_returns_insufficient` [test_signals.py]

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Completion Notes

- `SignalResult` frozen dataclass + `HOLD_INSUFFICIENT` sentinel in `signals/__init__.py`
- `ma_cross_signal`: uses pure pandas `ewm()` (no pandas_ta import — respects no-framework-import rule); detects last-bar crossover
- `ofi_signal`: rolling z-score of pre-computed `ofi` column; zero-variance guard returns dedicated reason
- `funding_rate_arb_signal`: stub returning hold/not_implemented; Epic 15 will implement
- Test helpers use flat→spike pattern to force deterministic cross at the final bar
- 13 new L1 tests; 127 total L1 green; mypy --strict clean (30 files)

### File List

- `bot_service/strategy/signals/__init__.py` (UPDATE)
- `bot_service/strategy/signals/ma_cross.py` (CREATE)
- `bot_service/strategy/signals/ofi_signal.py` (CREATE)
- `bot_service/strategy/signals/funding_rate_arb.py` (CREATE)
- `tests/test_signals.py` (CREATE)
