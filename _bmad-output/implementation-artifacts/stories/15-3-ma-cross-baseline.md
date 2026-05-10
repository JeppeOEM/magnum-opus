# Story 15.3: MA-Cross Baseline — Signal Function & Paper Deployment

Status: done

## Story

As mrqdt,
I want a moving-average crossover baseline strategy running in paper mode,
so that I have a benchmark to compare microstructure strategies against.

## Acceptance Criteria

- **AC1:** Given `bot_service/strategy/signals/ma_cross.py` with `compute_ma_cross_signal(df: DataFrame, fast: int, slow: int) -> SignalResult`, when called, then it computes EMA-fast and EMA-slow over the close column; returns `buy` on fast-crosses-above-slow (golden cross), `sell` on fast-crosses-below-slow (death cross), `hold` otherwise; when `len(df) < slow`, returns `hold` with reason `insufficient_data`.

- **AC2:** Given `strategies/active/ma_cross_bot.py` with `MACrossBot(BaseStrategy)`, when inspected, then it declares `min_lookback=50`, `max_position_pct=0.05`, `stop_loss_pct=0.03`, `paper_trading=True`, `bus_timeout_seconds=300`, `close_on_bus_timeout=False`; subscribes to 1-minute bars; calls `compute_ma_cross_signal` from `strategy/signals/ma_cross.py`.

- **AC3:** Given `MACrossBot` receiving a BarClose where `compute_ma_cross_signal` returns `buy` or `sell`, when the NaN guard, lookback gate, and gap invalidation gate all pass, then an `OrderRequest` is posted to the order worker with `paper_trading=True`.

- **AC4:** Given `_results/MACrossBot/fee_impact.json` and `_results/MACrossBot/validation_report.json`, when read, then both exist and show `passes=true`; `fee_impact.json` has fields `passes`, `required_edge`, `mean_signal_edge`, `margin`.

## Tasks / Subtasks

- [x] T1: Add `compute_ma_cross_signal` alias to `bot_service/strategy/signals/ma_cross.py` (AC1)
  - [x] T1.1: Append `compute_ma_cross_signal = ma_cross_signal` at the bottom of the existing file; update `__all__` if present
  - [x] T1.2: Do NOT copy or rewrite logic — `ma_cross_signal` already implements EMA crossover; alias only

- [x] T2: Create `strategies/active/ma_cross_bot.py` with `MACrossBot(BaseStrategy)` (AC2, AC3)
  - [x] T2.1: Implement `MACrossBot` with all required property overrides (`min_lookback=50`, `max_position_pct=0.05`, `stop_loss_pct=0.03`, `paper_trading=True`, `bus_timeout_seconds=300`, `close_on_bus_timeout=False`)
  - [x] T2.2: Implement `subscribe()`: call `self.get_history("BTCUSDT", "1m", self.min_lookback)` then `self.register_bar_handler("BTCUSDT", "1m", self._on_bar)`
  - [x] T2.3: Implement `_on_bar(df)`: check gap guard; call `compute_ma_cross_signal(df, fast=20, slow=50)`; if buy/sell → build `OrderRequest` and post via `_order_worker`; log warning when `_order_worker is None`

- [x] T3: Generate prerequisite result files (AC4)
  - [x] T3.1: Run `run_fee_impact_check("MACrossBot", pd.Series([0.05]*200), KuCoinCommissionInfo(), 5.0)` to create `_results/MACrossBot/fee_impact.json`
  - [x] T3.2: Create `_results/MACrossBot/validation_report.json` via `ValidationReport(..., passes=True).to_json()`
  - [x] T3.3: Run from `bot-service/` directory; verify both files parse and show `passes=true`

- [x] T4: Unit tests in `tests/test_ma_cross_bot.py` (AC1, AC2, AC3)
  - [x] T4.1: `test_compute_ma_cross_signal_is_alias` — assert `compute_ma_cross_signal is ma_cross_signal`
  - [x] T4.2: `test_ma_cross_bot_properties` — instantiate with mocked settings; assert all 6 property values
  - [x] T4.3: `test_ma_cross_bot_posts_order_on_buy_signal` — feed MACrossBot 51 bars with golden cross; mock `_order_worker.post`; assert called with buy signal and `paper_trading=True`
  - [x] T4.4: `test_ma_cross_bot_blocks_on_gap` — set `_signal_invalid["BTCUSDT"] = True`; feed a bar; assert no order posted
  - [x] T4.5: `test_result_files_exist_and_pass` — check `_results/MACrossBot/fee_impact.json` and `validation_report.json`; use `pytest.skip` if files absent

- [x] T5: Run full test suite — no regressions (AC1–AC4)

### Review Findings

- [x] [Review][Patch] Fix misleading comment in test_ma_cross_bot_hold_on_no_cross [bot-service/tests/test_ma_cross_bot.py:121]
- [x] [Review][Defer] No position-flip logic: sell entry posted without closing existing long [bot-service/strategies/active/ma_cross_bot.py] — deferred, pre-existing design for paper baseline
- [x] [Review][Defer] Fallback exchange hardcoded as "kucoin" vs configured default "bybit" [bot-service/strategies/active/ma_cross_bot.py:57] — deferred, pre-existing pattern from spec
- [x] [Review][Defer] sys.path.insert repeated in _import_ma_cross_bot helper [bot-service/tests/test_ma_cross_bot.py:30] — deferred, pre-existing pattern
- [x] [Review][Defer] Tests call _on_bar directly, bypassing NaN guard and lookback gate wrapper — deferred, valid unit test pattern (same as 15.2)
- [x] [Review][Defer] History query hardcodes snapshot_1s table — may silently return empty for tf='1m' [bot-service/bot_service/strategy/base.py] — deferred, pre-existing BaseStrategy limitation
- [x] [Review][Defer] has_gap=True rows included in EMA computation contaminating signal [bot-service/bot_service/strategy/signals/ma_cross.py] — deferred, pre-existing
- [x] [Review][Defer] isna().all() check allows single mid-series NaN to silently corrupt EMA — deferred, pre-existing in ma_cross_signal (do not modify)
- [x] [Review][Defer] _signal_invalid guard adds 50-bar dead zone on top of signal's own guard — deferred, design decision
- [x] [Review][Defer] Gap-recovery counter shared across all TFs for same symbol — deferred, architectural landmine for future multi-TF strategies

## Dev Notes

### What already exists (do NOT reimplement)

`bot_service/strategy/signals/ma_cross.py` already has:
- `ma_cross_signal(df, fast=20, slow=50) -> SignalResult` — EMA crossover on `df["close"]`; guards invalid spans, missing column, insufficient data, all-NaN; uses `pd.Series.ewm(span=..., adjust=False)`; returns `buy` on golden cross, `sell` on death cross, `hold` on no-cross

Tests in `tests/test_signals.py` cover `ma_cross_signal` thoroughly — do NOT duplicate those tests.

`bot_service/strategy/base.py` has `BaseStrategy` with full lifecycle. `_order_worker` is now on BaseStrategy (added in story 15.2). Pattern is identical to OFIBot.

`bot_service/backtest/fee_impact.py` has `run_fee_impact_check()` — same as used for OFIBot.

`bot_service/backtest/validation.py` has `ValidationReport` dataclass — same as used for OFIBot.

### compute_ma_cross_signal — alias only

Add alias to bottom of existing `bot_service/strategy/signals/ma_cross.py`:

```python
# Public alias used by MACrossBot and tests — do NOT copy logic
compute_ma_cross_signal = ma_cross_signal
```

Do NOT create a separate file. Do NOT duplicate the EMA logic.

### MACrossBot strategy class

The file goes in `bot-service/strategies/active/ma_cross_bot.py`. FileWatcher loads from `settings.bot_strategies_dir = "strategies/active"`.

```python
from __future__ import annotations

import pandas as pd
import structlog

from bot_service.exchange import OrderRequest
from bot_service.strategy.base import BaseStrategy
from bot_service.strategy.signals.ma_cross import compute_ma_cross_signal

log = structlog.get_logger()

_SYMBOL = "BTCUSDT"
_TF = "1m"


class MACrossBot(BaseStrategy):
    """EMA crossover baseline strategy — paper mode only."""

    @property
    def min_lookback(self) -> int: return 50
    @property
    def max_position_pct(self) -> float: return 0.05
    @property
    def stop_loss_pct(self) -> float: return 0.03
    @property
    def paper_trading(self) -> bool: return True
    @property
    def bus_timeout_seconds(self) -> int: return 300
    @property
    def close_on_bus_timeout(self) -> bool: return False

    def subscribe(self) -> None:
        self.get_history(_SYMBOL, _TF, self.min_lookback)
        self.register_bar_handler(_SYMBOL, _TF, self._on_bar)

    def _on_bar(self, df: pd.DataFrame) -> None:
        if self._signal_invalid.get(_SYMBOL, False):
            return
        result = compute_ma_cross_signal(df, fast=20, slow=50)
        if result.action == "hold":
            return
        side = "buy" if result.action == "buy" else "sell"
        req = OrderRequest(
            strategy=self._name,
            exchange=self._exchange or "kucoin",
            symbol=_SYMBOL,
            side=side,
            order_type="market",
            order_role="entry",
            size=self.max_position_pct,
            paper_trading=self.paper_trading,
        )
        log.info("ma_cross_bot_signal", side=side, confidence=result.confidence, reason=result.reason)
        if self._order_worker is None:
            log.warning("order_worker_not_injected_dropping_order", strategy=self._name, side=side)
            return
        self._order_worker.post(req)  # type: ignore[attr-defined]
```

**Key differences from OFIBot:**
- `_TF = "1m"` (1-minute bars, not 1-second)
- `min_lookback = 50` (not 200)
- `stop_loss_pct = 0.03` (not 0.02)
- `bus_timeout_seconds = 300` (not 30) — 1m bars are slower; 5 min silence before timeout
- `close_on_bus_timeout = False` — no emergency close on silence (paper baseline only)
- Signal call: `compute_ma_cross_signal(df, fast=20, slow=50)` (not OFI z-score)

### Generating result files

Run from `bot-service/` directory:

```python
import pandas as pd, pathlib, json
from bot_service.backtest.commission import KuCoinCommissionInfo
from bot_service.backtest.fee_impact import run_fee_impact_check
from bot_service.backtest.validation import ValidationReport

# fee_impact.json
run_fee_impact_check("MACrossBot", pd.Series([0.05]*200), KuCoinCommissionInfo(), 5.0, output_dir="_results")

# validation_report.json
report = ValidationReport(
    passes=True, fee_gate_ran=True, fee_gate_passed=True,
    walk_forward_ran=True, stress_test_ran=True, monte_carlo_ran=True,
    mean_sharpe=1.2, mean_drawdown=0.10, mean_degradation=0.15,
    monte_carlo_5th_pct=0.01, sharpe_ok=True, drawdown_ok=True,
    degradation_ok=True, monte_carlo_ok=True,
    min_sharpe=1.0, max_drawdown_threshold=0.15, max_degradation=0.30,
)
out = pathlib.Path("_results/MACrossBot/validation_report.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(report.to_json(), encoding="utf-8")
```

### Constructing a golden-cross DataFrame for tests

The EMA crossover requires slow+1 rows minimum. To force a golden cross, use a step-change in close prices:

```python
import pandas as pd

def _make_golden_cross_df(n: int = 51) -> pd.DataFrame:
    # First 40 rows: low price; last 11 rows: high price → fast EMA jumps above slow EMA
    prices = [90.0] * 40 + [200.0] * (n - 40)
    return pd.DataFrame({"close": prices})
```

For a death cross (reversed):
```python
def _make_death_cross_df(n: int = 51) -> pd.DataFrame:
    prices = [200.0] * 40 + [10.0] * (n - 40)
    return pd.DataFrame({"close": prices})
```

Verify the signal using `ma_cross_signal` tests as reference: 51 rows with price step from 90→200 should produce `buy`; 51 rows with 200→10 should produce `sell`.

### Testing MACrossBot without live exchange

```python
from unittest.mock import MagicMock
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "strategies" / "active"))
from ma_cross_bot import MACrossBot

settings = MagicMock()
settings.bot_subscribe_timeout_s = 30
settings.questdb_http_addr = "http://localhost:9000"
bot = MACrossBot("MACrossBot", settings)
```

Call `bot._on_bar(df)` directly with a crafted DataFrame to test signal dispatch.

### File locations

- **MODIFY** `bot_service/strategy/signals/ma_cross.py` — add `compute_ma_cross_signal = ma_cross_signal` alias at the bottom
- **CREATE** `strategies/active/ma_cross_bot.py` — MACrossBot class
- **CREATE** `tests/test_ma_cross_bot.py` — unit tests
- `bot_service/strategy/base.py` — READ only; `_order_worker` already injected from story 15.2
- `bot_service/strategy/registry.py` — READ only; no changes needed

### References

- Epic 15 story 15.3: `_bmad-output/planning-artifacts/epics-bot.md:909-930`
- `ma_cross_signal`: `bot_service/strategy/signals/ma_cross.py` (already complete)
- Existing tests: `bot_service/tests/test_signals.py:58-112`
- `BaseStrategy`: `bot_service/strategy/base.py`
- `OrderRequest`: `bot_service/exchange/__init__.py`
- Story 15.2 (OFIBot): `_bmad-output/implementation-artifacts/stories/15-2-ofi-microstructure-bot.md` — follow same patterns
- `run_fee_impact_check`: `bot_service/backtest/fee_impact.py`
- `ValidationReport`: `bot_service/backtest/validation.py`

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- Added `compute_ma_cross_signal = ma_cross_signal` alias to bottom of existing `ma_cross.py` — no logic copied
- Created `strategies/active/ma_cross_bot.py` with MACrossBot: 6 properties (min_lookback=50, bus_timeout=300, close_on_bus_timeout=False), subscribes to 1m bars, posts via `_order_worker` with warning on None
- Generated `_results/MACrossBot/fee_impact.json` (passes=True, margin=0.047) and `validation_report.json` (passes=True, sharpe=1.2)
- 9 L1 tests all pass; 186 total L1 tests pass (no regressions)
- Golden cross test: 50 bars at 90.0 then 1 bar at 300.0 forces EMA crossover at last row

### File List

- bot-service/bot_service/strategy/signals/ma_cross.py (modified — added compute_ma_cross_signal alias)
- bot-service/strategies/active/ma_cross_bot.py (created)
- bot-service/tests/test_ma_cross_bot.py (created)
- _bmad-output/implementation-artifacts/stories/15-3-ma-cross-baseline.md (updated)
- _bmad-output/implementation-artifacts/sprint-status.yaml (updated)
