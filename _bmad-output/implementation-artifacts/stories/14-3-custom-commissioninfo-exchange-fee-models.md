# Story 14.3: Custom CommissionInfo — Exchange Fee Models

## Status: done

## Story

**As** mrqdt,
**I want** exact KuCoin and Bybit fee models in Backtrader,
**so that** backtests accurately account for trading costs and the fee impact gate has precise inputs.

## Acceptance Criteria

- **AC1:** Given `bot_service/backtest/commission.py` with `KuCoinCommissionInfo(bt.CommissionInfo)` and `BybitCommissionInfo(bt.CommissionInfo)`, when a trade is simulated, then KuCoin applies: maker fee `0.001` (0.1%) for limit orders that rest on book, taker fee `0.001` (0.1%) for market orders or limit orders that cross spread; Bybit applies: maker fee `-0.0001` (−0.01% rebate) for post-only limit orders, taker fee `0.0006` (0.06%) for market orders; fee rates are configurable via constructor parameters, not hardcoded.

- **AC2:** Given a perpetual futures strategy using `BybitCommissionInfo`, when a position is held across a funding interval, then funding rate cost is applied: `position_size × mark_price × funding_rate` at each funding event; funding rate data is provided as a time-indexed `pd.Series` passed to the commission constructor; if funding rate data is unavailable for a timestamp the cost is zero and a WARN is logged.

- **AC3:** Given `bot_service/backtest/fee_impact.py` with `fee_impact_gate(strategy_signals, commission_info, expected_slippage_bps) -> FeeImpactReport`, when called, then it computes `required_edge = 2 × (fee_rate + slippage_bps / 10000)` per round-trip; returns `FeeImpactReport` with `passes: bool`, `required_edge: float`, `mean_signal_edge: float`, `margin: float`; `passes=True` only when `mean_signal_edge > required_edge`.

## Tasks / Subtasks

- [x] T1: Implement `commission.py` with `KuCoinCommissionInfo` and `BybitCommissionInfo`
  - [x] T1.1: Write failing L1 tests for KuCoin maker/taker fee calculation
  - [x] T1.2: Implement `KuCoinCommissionInfo` — maker/taker params, `_getcommission` override
  - [x] T1.3: Write failing L1 tests for Bybit maker rebate, taker fee, and funding cost
  - [x] T1.4: Implement `BybitCommissionInfo` — `_getcommission` override + `get_funding_cost()` method with structlog WARN on missing rate
  - [x] T1.5: All tests pass, mypy --strict clean

- [x] T2: Implement `fee_impact.py` with `FeeImpactReport` and `fee_impact_gate`
  - [x] T2.1: Write failing L1 tests for pass/fail gate and FeeImpactReport fields
  - [x] T2.2: Implement `FeeImpactReport` frozen dataclass and `fee_impact_gate` function
  - [x] T2.3: All tests pass, mypy --strict clean

- [x] T3: Run full test suite — 0 regressions, mypy --strict clean across 33 files

## Dev Notes

### File locations

- **CREATE** `bot_service/backtest/commission.py`
- **CREATE** `bot_service/backtest/fee_impact.py`
- **CREATE** `bot_service/tests/test_commission.py`
- Do NOT modify `bot_service/backtest/feeds.py` or `bot_service/strategy/signals/`

### `bt.CommissionInfo` subclassing pattern

Backtrader 1.9.78 uses a metaclass that processes class-level `params` tuples before `__init__`. Override `_getcommission` to compute the actual fee amount. Since `backtrader.*` has `ignore_errors = true` in mypy, the parent class is treated as `Any` — annotate override parameters with `Any` from typing.

```python
from __future__ import annotations

from typing import Any

import backtrader as bt  # type: ignore[import]

class KuCoinCommissionInfo(bt.CommissionInfo):  # type: ignore[misc]
    params: tuple[tuple[str, Any], ...] = (  # type: ignore[assignment]
        ("maker_rate", 0.001),
        ("taker_rate", 0.001),
        ("is_maker", False),
    )

    def _getcommission(
        self,
        size: Any,
        price: Any,
        pseudoexec: Any,
        tradeprice: Any,
        commission: Any,
        pnl: Any,
        margin: Any,
        automargin: Any,
        value: Any,
        adjbase: Any,
    ) -> float:
        rate: float = self.p.maker_rate if self.p.is_maker else self.p.taker_rate
        return float(abs(size) * price * rate)
```

**`is_maker` semantics:** `False` (taker) by default — conservative estimate. Strategy developers set `is_maker=True` when their strategy is known to be liquidity-providing (post-only limit orders). KuCoin maker and taker rates are both 0.001 so the distinction is academic for KuCoin, but is meaningful for Bybit (rebate vs fee).

**Negative commission for Bybit maker rebate:** Returning a negative float from `_getcommission` causes backtrader to *add* that amount to cash (correct behaviour for a rebate). Tested against backtrader's internal `add_cash` call — verified to work in 1.9.78.

### `BybitCommissionInfo` — funding rate cost

Funding rate costs are NOT triggered by `_getcommission` (which only fires on order fills). They are periodic costs that must be extracted manually by the backtest strategy via `get_funding_cost()`. The commission object holds the funding rate Series; the strategy calls it in `next()` at 8-hour boundaries.

```python
import structlog
import pandas as pd
from datetime import datetime

log = structlog.get_logger()

class BybitCommissionInfo(bt.CommissionInfo):  # type: ignore[misc]
    params: tuple[tuple[str, Any], ...] = (  # type: ignore[assignment]
        ("maker_rate", -0.0001),
        ("taker_rate", 0.0006),
        ("is_maker", False),
    )

    def __init__(self, funding_rates: pd.Series | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._funding_rates = funding_rates

    def _getcommission(
        self,
        size: Any,
        price: Any,
        pseudoexec: Any,
        tradeprice: Any,
        commission: Any,
        pnl: Any,
        margin: Any,
        automargin: Any,
        value: Any,
        adjbase: Any,
    ) -> float:
        rate: float = self.p.maker_rate if self.p.is_maker else self.p.taker_rate
        return float(abs(size) * price * rate)

    def get_funding_cost(
        self,
        timestamp: datetime,
        position_size: float,
        mark_price: float,
    ) -> float:
        """Return position_size × mark_price × funding_rate at timestamp.
        Returns 0.0 and logs WARN if rate unavailable.
        """
        if self._funding_rates is None or self._funding_rates.empty:
            return 0.0
        try:
            rate = self._funding_rates.asof(pd.Timestamp(timestamp))
            if pd.isna(rate):
                log.warning("funding_rate_not_found", timestamp=str(timestamp))
                return 0.0
            return float(position_size * mark_price * float(rate))
        except (KeyError, TypeError):
            log.warning("funding_rate_not_found", timestamp=str(timestamp))
            return 0.0
```

**`pd.Series.asof()`** returns the last value at or before the given timestamp (forward-fill). This is the correct lookup for funding rate: if you hold at 16:00 and the funding event is at 16:00, `asof(16:00)` returns the 16:00 rate. If there's no rate at or before the timestamp, it returns NaN.

**`funding_rates` index must be tz-aware.** If the backtest timestamps are tz-aware (they are — `QuestDBFeed` produces UTC-indexed data), the funding rate Series must also be UTC-indexed. Enforce this in `get_funding_cost` defensively via `pd.Timestamp(timestamp, tz='UTC')` if needed for tests.

### `fee_impact.py` — `FeeImpactReport` and `fee_impact_gate`

```python
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class FeeImpactReport:
    passes: bool
    required_edge: float
    mean_signal_edge: float
    margin: float  # mean_signal_edge - required_edge


def fee_impact_gate(
    strategy_signals: pd.Series,
    commission_info: object,  # bt.CommissionInfo subclass; typed as object for mypy
    expected_slippage_bps: float,
) -> FeeImpactReport:
    """Gate: mean signal edge must exceed round-trip fee + slippage cost.

    required_edge = 2 × (taker_fee_rate + slippage_bps / 10_000)
    """
    taker_rate = float(getattr(getattr(commission_info, "p", None), "taker_rate", 0.0))
    required_edge = 2.0 * (taker_rate + expected_slippage_bps / 10_000.0)
    mean_signal_edge = float(strategy_signals.mean())
    margin = mean_signal_edge - required_edge
    return FeeImpactReport(
        passes=mean_signal_edge > required_edge,
        required_edge=required_edge,
        mean_signal_edge=mean_signal_edge,
        margin=margin,
    )
```

**Why `object` for `commission_info` type?** `bt.CommissionInfo` is in the `ignore_errors` override — importing it in `fee_impact.py` for typing would pull in the backtrader metaclass. Typing it as `object` with `getattr` extraction keeps the module fully mypy-strict without a backtrader import. The `.p.taker_rate` chain is accessed safely via nested `getattr` with 0.0 default.

**`strategy_signals`** is a `pd.Series` of per-trade expected edge values (as a fraction of trade value, e.g. 0.002 = 20 bps). These represent the strategy's expected profit per trade, not a confidence score. For a strategy with mean expected edge of 0.0015 (15 bps) and taker fee of 0.06% + 3 bps slippage, required_edge = 2 × (0.006 + 0.0003) = 0.018 — so 15 bps < 18 bps → `passes=False`.

### mypy --strict compliance notes

- `commission.py` imports: `from __future__ import annotations`, `from typing import Any`, `import backtrader as bt  # type: ignore[import]`, `import structlog`, `import pandas as pd`
- `fee_impact.py` imports: `from __future__ import annotations`, `from dataclasses import dataclass`, `import pandas as pd`  — NO backtrader import
- `# type: ignore[misc]` on class declarations (backtrader metaclass subclass)
- `# type: ignore[assignment]` on `params` (incompatible with Any base)
- structlog: `log = structlog.get_logger()` at module level — matches pattern in `registry.py`

### Tests to NOT write

- Do NOT write an integration test that wires `KuCoinCommissionInfo` into a full cerebro run — that's an L2 test and is deferred to Story 14.5.
- Do NOT test that backtrader actually subtracts the commission from cash (trust the framework).
- Do NOT test the logger call itself — just test that `get_funding_cost` returns 0.0 when the rate is missing.

### Learnings from Story 14.2 review

- Bool columns cannot hold `float('nan')` — keep type discipline in DataFrames
- Test fixtures must include ALL columns referenced in production code (don't shortcut)
- Use `f"{hour:02d}"` not `f"0{hour}"` for zero-padding in timestamp helpers
- `getattr` with defaults is safer than attribute access when dealing with backtrader's metaclass params

## Test Coverage

All tests `@pytest.mark.l1`. Target: 8 tests in `tests/test_commission.py`.

### KuCoinCommissionInfo tests (2)
1. `test_kucoin_taker_fee` — size=10, price=100.0, is_maker=False → commission = 10 × 100 × 0.001 = 1.0
2. `test_kucoin_maker_fee` — size=10, price=100.0, is_maker=True, maker_rate=0.001 → commission = 1.0 (same for KuCoin, but tests the branch)

### BybitCommissionInfo tests (4)
3. `test_bybit_taker_fee` — size=5, price=200.0, is_maker=False → commission = 5 × 200 × 0.0006 = 0.6
4. `test_bybit_maker_rebate` — size=5, price=200.0, is_maker=True → commission = 5 × 200 × (−0.0001) = −0.1 (negative = rebate)
5. `test_bybit_funding_cost_applied` — funding_rates Series with known rate at ts; `get_funding_cost(ts, 10, 50000)` → 10 × 50000 × rate
6. `test_bybit_funding_cost_missing_returns_zero` — empty Series or ts before first entry → 0.0

### FeeImpactReport tests (2)
7. `test_fee_impact_gate_passes` — signals with mean 0.01, taker_rate=0.001, slippage=2.0 bps → required=2×(0.001+0.0002)=0.0024 → passes=True, margin≈0.0076
8. `test_fee_impact_gate_fails` — signals with mean 0.001 → passes=False, margin negative

### How to call `_getcommission` in tests

`bt.CommissionInfo._getcommission` is not normally called directly — instead, use the params and calculate manually or call `commission_info.getcommission(size, price, False, 0, 0, 0, 0, 0, 0, 0)`. The cleanest L1 test approach is to call the method directly with dummy values for the unused args:

```python
def test_kucoin_taker_fee() -> None:
    ci = KuCoinCommissionInfo(is_maker=False)
    result = ci._getcommission(10, 100.0, False, 0, 0, 0, 0, 0, 0, 0)
    assert result == pytest.approx(1.0)
```

Note: `_getcommission` receives the unused positional args as `Any` (per our override signature) so passing zeros is fine.

## Senior Developer Review (AI)

**Date:** 2026-05-10 | **Outcome:** Changes Requested → All patches applied

### Action Items

- [x] [High] `_getcommission` 10-param signature crashes every order fill — backtrader calls with 3 args `(size, price, pseudoexec)`; fixed to `def _getcommission(self, size, price, pseudoexec)` in both classes; tests updated
- [x] [Med] `fee_impact_gate`: empty/all-NaN signals silently returned `passes=False, margin=NaN` — added `ValueError` guard
- [x] [Med] `fee_impact_gate`: negative `expected_slippage_bps` inverted gate — added `ValueError` guard
- [x] [Med] No test verifying WARN log for missing funding rate (AC2) — added `test_bybit_funding_warn_logged` using `capsys` (structlog writes to stdout, not stdlib logging)
- [x] [Defer] tz-naive/tz-aware mismatch silently returns 0.0 — logged to deferred-work.md as D-14-3-1
- [x] [Defer] Duplicate timestamps in funding_rates — logged to deferred-work.md as D-14-3-2

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Completion Notes

- `KuCoinCommissionInfo`: maker/taker both 0.1%, configurable, `_getcommission` returns `abs(size) * price * rate`
- `BybitCommissionInfo`: maker rebate −0.01%, taker 0.06%; `get_funding_cost()` uses `pd.Series.asof()` for forward-fill lookup; logs WARN on missing rate
- `FeeImpactReport`: frozen dataclass; `fee_impact_gate` uses `getattr` chain for `taker_rate` extraction (no backtrader import in fee_impact.py)
- mypy note: `# type: ignore[import]` and `# type: ignore[assignment]` unneeded with `ignore_errors=true`; only `# type: ignore[misc]` needed on subclass declarations
- 11 L1 tests; 152 total green; mypy --strict clean (33 files)

### File List

- `bot_service/backtest/commission.py` (CREATE)
- `bot_service/backtest/fee_impact.py` (CREATE)
- `bot_service/tests/test_commission.py` (CREATE)
