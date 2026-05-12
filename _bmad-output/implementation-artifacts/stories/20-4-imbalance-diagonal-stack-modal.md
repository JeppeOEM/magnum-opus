# Story 20.4: Imbalance Diagonal Stack in Footprint Modal

Status: done

## Story

As a trader,
I want the footprint modal to highlight buy-imbalanced and sell-imbalanced price levels with distinct colors,
and show the longest consecutive imbalance stack counts,
so that I can read auction diagonal imbalance directly from the per-bar footprint view.

**Pre-conditions:** Story 20.3 done. Footprint modal operational. `footprint_json` in candle-store. `imbalance_stack_buy` and `imbalance_stack_sell` scalar fields in snapshot_1s (written by candle-service Epic 19).

## Acceptance Criteria

### AC 1 — Imbalance highlighting in footprint chart

1. `build_footprint_chart` computes per-level imbalance from `footprint_json`:
   - Buy-imbalanced: `buy_vol > 3 × sell_vol AND sell_vol > 0`
   - Sell-imbalanced: `sell_vol > 3 × buy_vol AND buy_vol > 0`
2. Buy bars at buy-imbalanced levels use color `#00E676` (bright green); normal buy bars remain `#26A69A`.
3. Sell bars at sell-imbalanced levels use color `#FF1744` (bright red); normal sell bars remain `#EF5350`.
4. Both buy and sell bars for the same level can be flagged independently (each checks its own ratio).

### AC 2 — Stack count annotation

5. A text annotation is added to the footprint chart: `f"↑ Stack {stack_buy}  ↓ Stack {stack_sell}"` rendered as a chart title or `go.layout.Annotation`.
6. `stack_buy` and `stack_sell` are computed from the footprint data (longest consecutive imbalanced run), not from the candle-store scalar fields — so they work even for historical bars.
7. When all levels have zero buy or sell volume (no imbalance possible), stack counts show 0.

### AC 3 — Graceful handling

8. When `footprint_json` is absent/null → modal does not open (pre-existing guard from 20.3 unchanged).
9. Imbalance computation inside try/except block — any failure returns empty fig (modal stays closed).
10. Chart with zero imbalanced cells renders correctly (all bars normal color, annotation shows "↑ Stack 0  ↓ Stack 0").

### AC 4 — No regression

11. The modal still opens on candlestick click and shows the footprint chart (20.3 AC 1 still passes).
12. Dashboard returns 200; no import errors.

## Tasks / Subtasks

- [x] Task 1: Extend `build_footprint_chart` in `dashboard/charts.py` (AC 1, 2, 3)
  - [x] 1.1 After computing `prices_numeric` and cell data, compute per-level `buy_imb` and `sell_imb` boolean lists using 3× threshold
  - [x] 1.2 Build `bar_colors_buy` and `bar_colors_sell` lists: bright/normal color per level
  - [x] 1.3 Pass `marker_color=bar_colors_buy` (list) to Buy trace and `marker_color=bar_colors_sell` to Sell trace (Plotly accepts per-bar color as a list)
  - [x] 1.4 Compute `stack_buy` and `stack_sell` (longest consecutive True run in imbalance boolean lists)
  - [x] 1.5 Add annotation via `fig.update_layout(title=dict(text=f"↑ Stack {stack_buy}  ↓ Stack {stack_sell}", font=dict(size=12, color="#CCCCCC"), x=0.5))`

- [x] Task 2: Verify (AC 4)
  - [x] 2.1 Rebuild dashboard; no import errors
  - [x] 2.2 `curl localhost:8050` returns 200
  - [x] 2.3 No errors in logs

## Dev Notes

### Imbalance computation (match candle-service logic exactly)

```python
BUY_IMBALANCE_RATIO = 3.0

buy_imb = []
sell_imb = []
for _, key in price_pairs:
    cell = fp[key]
    if not isinstance(cell, dict):
        buy_imb.append(False)
        sell_imb.append(False)
        continue
    b = float(cell.get("b") or 0)
    s = float(cell.get("s") or 0)
    buy_imb.append(s > 0 and b > BUY_IMBALANCE_RATIO * s)
    sell_imb.append(b > 0 and s > BUY_IMBALANCE_RATIO * b)
```

### Longest consecutive run helper

```python
def _longest_run(flags: list[bool]) -> int:
    max_len = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        max_len = max(max_len, cur)
    return max_len
```

### Per-bar color lists in Plotly

Plotly `go.Bar` accepts `marker_color` as either a scalar string or a list of strings (one per bar). Pass a list to set individual bar colors:
```python
bar_colors_buy  = ["#00E676" if imb else "#26A69A" for imb in buy_imb]
bar_colors_sell = ["#FF1744" if imb else "#EF5350" for imb in sell_imb]
```

### price_pairs structure from story 20.3

`price_pairs` is `[(float_price, str_key), ...]` sorted descending. The indices of `price_pairs` align exactly with `buy_imb`, `sell_imb`, and the bar index in both traces.

### Files to modify

- `dashboard/charts.py` — extend `build_footprint_chart` only; no other file changes

### References

- Imbalance Go implementation: `candle-service/internal/features/imbalance.go` lines 27–76
- 3× threshold definition: `cell.BuyVol > 3*cell.SellVol AND cell.SellVol > 0` (line 57)
- Story 20.3 footprint chart: `dashboard/charts.py:build_footprint_chart`
- Existing scalar fields written by candle-service: `imbalance_buy_count`, `imbalance_sell_count`, `imbalance_stack_buy`, `imbalance_stack_sell`, `imbalance_ratio` (in snapshot_1s, but NOT used here — compute from footprint_json directly for per-bar accuracy)

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- Extended `build_footprint_chart` in `dashboard/charts.py`: computes per-level buy_imb/sell_imb using 3× threshold (matching candle-service imbalance.go); builds per-bar color lists (bright #00E676 for buy-imbalanced, #FF1744 for sell-imbalanced); computes stack_buy/stack_sell via _longest_run helper; adds chart title annotation. All inside existing try/except block.
- Added `_longest_run` helper function to charts.py.
- Rebuilt dashboard; no import errors; curl localhost:8050 returns 200.
- [Review][NoAction] "Stack direction inversion" finding rejected: longest-run length is mathematically invariant under list reversal; reviewer's own examples confirm equal counts. Visual descending order is correct for the chart.
- [Review][Defer] float() crash on non-numeric cell values: inside existing try/except, Go always emits numeric JSON, practically unreachable.

### File List

- dashboard/charts.py
