# Epic 15 Retrospective: First Strategy Implementations

**Date:** 2026-05-10
**Epic Status:** Done (3 of 4 stories complete; story 15.4 blocked on aggregator prerequisite)

## Stories Completed

| Story | Title | Tests Added | Key Outcome |
|-------|-------|-------------|-------------|
| 15.1 | Fee Impact Analysis Gate | 5 L1 | `run_fee_impact_check()` with `mean_signal_edge > 2×(fee+slippage)` gate; outputs `fee_impact.json` per strategy |
| 15.2 | OFI Microstructure Bot | 8 L1 | `OFIBot(BaseStrategy)` subscribes to 1s bars; `_order_worker` injection added to `BaseStrategy`; `FileWatcher` updated to inject worker |
| 15.3 | MA-Cross Baseline | 9 L1 | `MACrossBot(BaseStrategy)` subscribes to 1m bars; `compute_ma_cross_signal` alias; paper-mode baseline for comparison |
| 15.4 | Funding Rate Arb (BLOCKED) | 0 | Story file created with prerequisite gate documented; blocked until `funding:{exchange}:{symbol}` Redis stream flows from aggregator |

**Total tests added:** 22 L1 (186 total at epic completion)

---

## What Went Well

### 1. The alias pattern keeps signal modules thin and the test surface minimal
Both `compute_ofi_signal = ofi_signal` and `compute_ma_cross_signal = ma_cross_signal` are single-line exports. The public API used by strategy classes is stable; the underlying function can evolve without touching the strategy file. Review confirmed this is the correct pattern — no logic duplication, no risk of divergence.

### 2. `_order_worker` injection is a single-point change with zero strategy-side boilerplate
Adding `self._order_worker: object | None = None` to `BaseStrategy.__init__` and one assignment in `FileWatcher._load_new` gave every strategy access to the order worker without requiring any per-strategy code. Both `OFIBot` and `MACrossBot` use the same `if self._order_worker is None: log.warning(...)` guard — consistent and reviewable.

### 3. EMA crossover test construction lesson: crossover must occur at the last two rows
The golden-cross test initially used 40 rows at 90 + 11 rows at 300 (51 total). The crossover happened at bar 40, not bar 50 — by bar 50, `ma_cross_signal` saw `prev_fast > prev_slow AND curr_fast > curr_slow`, no new crossover. The fix: 50 rows at 90 + 1 row at 300. Now `prev_fast ≈ prev_slow` and `curr_fast > curr_slow` — crossover detected at the last bar. This is now documented as a standing pattern for any EMA-crossover test fixture.

### 4. Review patches were small and targeted — no architectural surprises
All three review patches across stories 15.2 and 15.3 were single-location fixes: add a warning log, remove dead imports, change assert-exists to pytest.skip. No review required structural changes. The pre-review implementation quality was high because the story Dev Notes explicitly named the deferred items.

### 5. `pytest.skip` over `assert p.exists()` in result file tests is the right CI contract
Result files (`_results/OFIBot/fee_impact.json`, etc.) are gitignored and generated on the developer machine. Asserting existence would break CI on a fresh checkout with no generated files. `pytest.skip("run story T3 script from bot-service/")` makes the test clearly optional without hiding the requirement.

### 6. Blocking story 15.4 explicitly prevents wasted effort
Rather than implementing a stub or approximation, the story is created with a clear prerequisite gate (`redis-cli XLEN funding:bybit:BTCUSDT > 0`) and preserved acceptance criteria. No code was written for a system component that doesn't exist. This is the correct outcome.

---

## What Could Be Improved

### 1. `_order_worker` typed as `object | None` lacks a Protocol contract
The `object` type means mypy cannot verify that `_order_worker.post(req)` is a valid call. The `# type: ignore[attr-defined]` comment on that line is a symptom of the missing Protocol. A `OrderWorkerProtocol(Protocol)` with a `post(req: OrderRequest) -> None` method would close this gap without coupling `base.py` to the concrete `OrderQueueWorker`. Deferred as D-15-2-1.

### 2. No position-flip logic: sell entry posted without closing an existing long
When `MACrossBot` (or `OFIBot`) receives a `sell` signal while already holding a long position, it posts a naked `sell` entry without first closing the long. For paper mode this is acceptable — the `OrderQueueWorker` serializes orders — but for live mode it would create double short exposure. This is a design gap in both strategy classes. Deferred as D-15-2-4 / D-15-3-1.

### 3. Fallback exchange hardcoded as "kucoin" should use configured default
`self._exchange or "kucoin"` in both bot files should reference `settings.default_exchange` or a class-level constant — not a hardcoded string literal. The configured default exchange is "bybit". This is a silent misconfiguration risk. Deferred as D-15-2-5 / D-15-3-2.

### 4. `sys.path.insert` repeated in `_import_*` helpers in each test module
Each test module uses a `sys.path.insert(0, strategies/active)` helper to import the bot class. This works but is fragile if the directory structure changes. A conftest.py `sys.path` fixture would be the correct fix. Deferred as D-15-2-7 / D-15-3-3.

### 5. `has_gap=True` rows included in EMA computation contaminate the signal
The `ma_cross_signal` function does not filter out rows where `has_gap=True` before computing EMA. Gap rows with interpolated or missing prices corrupt the EMA state. The fix would be to drop gap rows before EMA computation. Deferred as D-15-3-5.

### 6. Tests call `_on_bar` directly, bypassing lookback gate wrapper
Unit tests construct the bot and call `bot._on_bar(df)` directly. This bypasses the NaN guard and lookback gate that `register_bar_handler` applies. The tests validate signal dispatch correctly, but they do not validate that the guards work end-to-end. An integration-level test via `bot.on_bar(bar)` would give higher confidence. Deferred.

---

## Deferred Work Inventory

| Item | Source | Deferral Reason |
|------|---------|----------------|
| `_order_worker` typed as `object\|None`, missing Protocol contract | 15.2 Review | Architectural refactor; pre-existing in base.py |
| `size=max_position_pct` may need pct→quantity conversion in `OrderQueueWorker` | 15.2 Review | Pre-existing design question; depends on OrderQueueWorker implementation |
| Market order bypasses risk gate notional check (limit_price=None) | 15.2 Review | Pre-existing; risk gate works on limit price |
| No position-flip logic: sell entry without closing long | 15.2, 15.3 Review | Design decision for paper baseline |
| Fallback exchange hardcoded as "kucoin" vs configured default "bybit" | 15.2, 15.3 Review | Pre-existing pattern from spec |
| `asyncio.Queue` cross-loop safety for `order_worker.post()` | 15.2 Review | Architectural; pre-existing |
| `sys.path.insert` repeated per test file | 15.2, 15.3 Review | Refactor; conftest.py fixture is the fix |
| `subscribe()` history retry thread-safety race condition | 15.2 Review | Pre-existing in BaseStrategy |
| `has_gap=True` rows included in EMA computation | 15.3 Review | Pre-existing in `ma_cross_signal` |
| `isna().all()` allows single mid-series NaN to silently corrupt EMA | 15.3 Review | Pre-existing in `ma_cross_signal` |
| Gap-recovery counter shared across all TFs for same symbol | 15.3 Review | Architectural landmine for multi-TF strategies |

---

## Architecture Patterns Established

1. **Signal alias**: `compute_X_signal = x_signal` in `signals/X.py` — thin re-export, no logic copy; both `BaseStrategy` subclasses and tests import the `compute_*` name
2. **Strategy class structure**: `BaseStrategy` subclass with 6 `@property` overrides, `subscribe()` → `get_history + register_bar_handler`, `_on_bar(df)` → gap guard → signal call → `OrderRequest` → `_order_worker.post()`
3. **Worker injection**: `BaseStrategy._order_worker: object | None = None`; `FileWatcher._load_new` sets it after instantiation; strategy logs `WARNING` on None and drops the order
4. **Result files**: gitignored `_results/{StrategyName}/fee_impact.json` and `validation_report.json` generated by a one-off script from `bot-service/`; test uses `pytest.skip` if absent
5. **EMA crossover test fixture**: N-1 rows at stable price + 1 final row at divergent price forces crossover at `iloc[-1]` vs `iloc[-2]`; N must be ≥ slow EMA span
6. **BLOCKED story**: create story file with prerequisite gate documented; set sprint-status to `blocked`; no implementation started
