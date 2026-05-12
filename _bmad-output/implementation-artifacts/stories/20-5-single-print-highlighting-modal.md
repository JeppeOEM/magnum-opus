# Story 20.5: Single Print Highlighting in Footprint Modal

Status: done

## Story

As a trader,
I want single-print price levels highlighted in the footprint modal,
so that I can see which price levels had very low volume (market moved through quickly) and are likely to be revisited.

**Pre-conditions:** Story 20.4 done. Footprint modal showing buy/sell bars with imbalance highlighting. `single_print_levels_json` written to `snapshot_1s` by candle-service (Epic 19 story 19.4). Candle-store loads all `snapshot_1s` columns via `SELECT *`.

## Acceptance Criteria

### AC 1 — Single print levels highlighted in footprint chart

1. `build_footprint_chart(footprint_json_str, single_print_levels=None)` accepts an optional second argument: a set/frozenset of price strings that are single-print levels.
2. When `single_print_levels` is provided and non-empty, bars at those price levels use amber color: buy bars `#FFC107`, sell bars `#FF6F00` (overrides imbalance colors — single print indicates low volume regardless of imbalance).
3. Non-single-print levels retain their story 20.4 colors (imbalance-aware green/red or normal teal/red).
4. Chart title is extended to include single print count: `f"↑ Stack {stack_buy}  ↓ Stack {stack_sell}  | SP: {sp_count}"` where `sp_count = len(single_print_levels & set_of_chart_price_keys)`.

### AC 2 — single_print_levels passed from callback

5. `open_footprint_modal` in `dashboard/callbacks.py` extracts `single_print_levels_json` from the clicked candle row.
6. Parses it with `json.loads` (guard on None/failure → pass `None` to chart function, modal still opens).
7. Passes `frozenset(...)` of price strings to `build_footprint_chart`.

### AC 3 — Graceful handling

8. When `single_print_levels_json` is absent from the row → passes `None` to chart function; chart renders without single-print highlighting.
9. When `single_print_levels_json` is `"[]"` → empty set passed; no levels highlighted; SP count shows 0.
10. When `single_print_levels_json` fails JSON parse → passes `None`; chart renders without highlighting (no crash).
11. `build_footprint_chart` with `single_print_levels=None` behaves identically to story 20.4 (no change in color logic).

### AC 4 — No regression

12. Dashboard returns 200; no import errors.
13. Modal still opens on click; footprint chart with imbalance highlighting still works.

## Tasks / Subtasks

- [x] Task 1: Extend `build_footprint_chart` signature and coloring in `dashboard/charts.py` (AC 1, 3)
  - [x] 1.1 Add `single_print_levels=None` parameter to function signature
  - [x] 1.2 After computing `bar_colors_buy` and `bar_colors_sell` from imbalance logic, apply single-print override: for each price level's string key, if it is in `single_print_levels`, set buy color to `#FFC107` and sell color to `#FF6F00`
  - [x] 1.3 Compute `sp_count` = number of chart price levels that are in `single_print_levels` (use original string keys from `price_pairs`)
  - [x] 1.4 Update title to include `| SP: {sp_count}`

- [x] Task 2: Extract and pass `single_print_levels` in `open_footprint_modal` in `dashboard/callbacks.py` (AC 2, 3)
  - [x] 2.1 After extracting `fp_json`, read `sp_json = row.get("single_print_levels_json")`
  - [x] 2.2 Parse with `json.loads` in try/except → `sp_levels = frozenset(sp_json_parsed)` or `None` on failure/absent
  - [x] 2.3 Call `charts.build_footprint_chart(fp_json, single_print_levels=sp_levels)`

- [x] Task 3: Verify (AC 4)
  - [x] 3.1 Rebuild dashboard; no import errors
  - [x] 3.2 `curl localhost:8050` returns 200
  - [x] 3.3 No errors in logs

## Dev Notes

### single_print_levels_json format

Stored in `snapshot_1s.single_print_levels_json` as a JSON array of price strings, sorted ascending. Format:
```json
["100.0", "100.5", "102.0"]
```
Empty: `"[]"`. The price strings exactly match keys in `footprint_json` (both come from the same `footprintMap` in the candle-service accumulator).

### Single print definition (from candle-service auction.go)

A price level is single-print when: `levelVol < 0.10 × meanVol` where `levelVol = buyVol + sellVol` for that level, and `meanVol = totalVol / n` across all footprint levels. Price must also be within `[low, high]` of the bar.

We do NOT recompute this in the dashboard — we use the pre-computed `single_print_levels_json` from the candle row, which is authoritative.

### Color precedence in bar coloring

Single print OVERRIDES imbalance colors. Rationale: single print means the level had so little activity that the imbalance signal is noise. The amber color draws the trader's eye to "thin" price areas.

```python
# After building bar_colors_buy and bar_colors_sell from imbalance logic:
if single_print_levels:
    for i, (_, key) in enumerate(price_pairs):
        if key in single_print_levels:
            bar_colors_buy[i] = "#FFC107"
            bar_colors_sell[i] = "#FF6F00"
```

### sp_count computation

Count how many chart levels are in the single print set (some levels in `single_print_levels_json` may not appear in `footprint_json` if the footprint was subsetted, though this is unlikely):
```python
sp_count = sum(1 for _, key in price_pairs if key in single_print_levels) if single_print_levels else 0
```

### Parsing in callback

```python
sp_json = row.get("single_print_levels_json")
sp_levels = None
if sp_json:
    try:
        parsed = json.loads(sp_json)
        if isinstance(parsed, list):
            sp_levels = frozenset(parsed)
    except Exception:
        pass
```

Note: `import json` is already at the top of `callbacks.py`? No — `callbacks.py` does not currently import `json`. Add `import json` to callbacks.py imports.

Actually — check the current imports in callbacks.py: `import copy`, `import os`, `import pandas as pd`, `from dash import ...`, `import plotly.graph_objects as go`, `import charts`, `import data`. Add `import json`.

### Files to modify

- `dashboard/charts.py` — extend `build_footprint_chart` signature and add single-print coloring
- `dashboard/callbacks.py` — add `import json`, extract `single_print_levels_json`, pass to chart function

### References

- Single print Go implementation: `candle-service/internal/features/auction.go` lines 52–73
- Format: JSON array of price strings sorted ascending, `"[]"` when empty
- Story 20.4 imbalance coloring: `dashboard/charts.py:build_footprint_chart` (uses `bar_colors_buy/sell` lists)
- Story 20.3 modal callback: `dashboard/callbacks.py:open_footprint_modal`

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- Extended `build_footprint_chart` in `dashboard/charts.py` with optional `single_print_levels` parameter; after imbalance coloring, overrides bar colors for single-print levels (#FFC107 buy, #FF6F00 sell); computes sp_count; extends title annotation to include "| SP: N".
- Added `import json` to `dashboard/callbacks.py`; `open_footprint_modal` extracts `single_print_levels_json` from candle row, parses with try/except, passes `frozenset` to `build_footprint_chart`.
- Rebuilt dashboard; no import errors; curl localhost:8050 returns 200.
- [Review][Patch] Normalized price keys through `str(float(p))` in both frozenset construction (callback) and lookup (charts) to guard against decimal format mismatches ("100.5" vs "100.50").
- [Review][Patch] Added `isinstance(p, str)` guard in frozenset construction to reject non-string JSON elements.
- [Review][Defer] sp_count title shows "SP: 0" even for pre-migration rows where field is absent — cosmetically misleading but acceptable.
- [Review][Defer] None vs empty frozenset conflation — no functional impact.

### File List

- dashboard/charts.py
- dashboard/callbacks.py
