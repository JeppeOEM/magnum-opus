# Story 20.2: Iceberg Cell Borders on OB Depth Heatmap

Status: done

## Story

As a trader,
I want iceberg-detected price levels highlighted on the OB Depth Heatmap,
so that I can spot hidden large orders visually within the depth context.

**Pre-conditions:** Epic 19 story 19.5 complete (iceberg_bid_detected, iceberg_ask_detected, iceberg_price written to snapshot_1s). Story 20.1 done. Dashboard running with heatmap panel operational.

## Acceptance Criteria

### AC 1 — add_iceberg_borders renders cyan/magenta markers on heatmap

1. A new function `add_iceberg_borders(fig: go.Figure, df: pd.DataFrame) -> go.Figure` is added to `dashboard/charts.py`.
2. When `iceberg_bid_detected` is present in `df.columns` and has at least one truthy row, a `go.Scatter` trace is added with:
   - `x` = timestamps of candles where `iceberg_bid_detected` is truthy
   - `y` = `iceberg_price` (numeric) for those candles
   - `mode='markers'`, `marker=dict(symbol='square-open', size=14, color='#00BCD4', line=dict(width=2))`
   - `name='Iceberg Bid'`, `showlegend=False`, `hoverinfo='skip'`
3. When `iceberg_ask_detected` is present and has at least one truthy row, a second trace:
   - `x` = timestamps, `y` = `iceberg_price`, colour `#E040FB`, `name='Iceberg Ask'`
   - Same marker style (square-open, size=14, line width=2)
4. When both bid and ask detected in the same candle row, both traces include that row (each checks its own flag independently).

### AC 2 — Graceful handling of missing/null fields

5. When any of `iceberg_bid_detected`, `iceberg_ask_detected`, or `iceberg_price` are absent from `df.columns`, the function returns `fig` unchanged (no crash).
6. When `df` is empty, the function returns `fig` unchanged.
7. Rows where `iceberg_price` is null/NaN after coercion are excluded from the scatter (no NaN y-values).
8. Rows where `ts` is absent → function returns `fig` unchanged (guard for `"ts"` column).

### AC 3 — Wired into update_heatmap

9. `update_heatmap` in `dashboard/callbacks.py` calls `charts.add_iceberg_borders(fig, df)` after `add_ob_depth_heatmap`.
10. `df` passed to `add_iceberg_borders` is the candle-store DataFrame (same `df` built from `candle_rows`).

### AC 4 — No regression

11. When iceberg fields are absent (historical data), the heatmap renders identically to before this story.
12. Dashboard returns 200; no import errors in container logs.

## Tasks / Subtasks

- [x] Task 1: Add `add_iceberg_borders` to `dashboard/charts.py` (AC 1, 2)
  - [x] 1.1 Guard: if `df.empty` or `"ts"` not in df.columns → return fig unchanged
  - [x] 1.2 Guard: if neither `iceberg_bid_detected` nor `iceberg_ask_detected` in df.columns → return fig unchanged
  - [x] 1.3 Coerce `iceberg_price` to numeric; drop rows where NaN
  - [x] 1.4 For bid: filter `iceberg_bid_detected` truthy rows; if any, add cyan square-open scatter trace
  - [x] 1.5 For ask: filter `iceberg_ask_detected` truthy rows; if any, add magenta square-open scatter trace

- [x] Task 2: Wire into `update_heatmap` in `dashboard/callbacks.py` (AC 3)
  - [x] 2.1 After `fig = charts.add_ob_depth_heatmap(fig, ob_rows or [])`, add `fig = charts.add_iceberg_borders(fig, df)`

- [x] Task 3: Verify (AC 4)
  - [x] 3.1 Rebuild dashboard; no import errors in logs
  - [x] 3.2 `curl localhost:8050` returns 200
  - [x] 3.3 Historical data (no iceberg fields) renders heatmap without crash

## Dev Notes

### Data source: candle-store, NOT ob-store

**Critical architecture point:** iceberg_bid_detected, iceberg_ask_detected, and iceberg_price come from the **candle-store** (QuestDB snapshot_1s, populated by Epic 19 story 19.5). They are NOT in the ob-store (Redis ob_features stream).

The `update_heatmap` callback already receives `candle_rows` and builds `df` from it. Pass that same `df` to `add_iceberg_borders`. Do NOT try to read iceberg fields from `ob_rows`.

### Simple figure — no row/col

The heatmap figure (`heatmap-graph`) is built by `build_delta_heatmap` which returns a simple `go.Figure()` (not a make_subplots figure — this was fixed in commit 0411bae). Do NOT pass `row=` or `col=` to `add_trace`.

### Coercion pattern for bool fields from QuestDB

QuestDB returns BOOLEAN columns as Python bool values in the JSON dataset. The field values can be True/False/None. Use the same coercion pattern from story 20.1:
```python
mask = pd.to_numeric(df["iceberg_bid_detected"], errors="coerce").fillna(0).astype(bool)
```

### Iceberg_price may be NaN when not detected

`iceberg_price` is NULL in rows where no iceberg was detected. After filtering for truthy `iceberg_bid_detected` rows, coerce `iceberg_price` to numeric and drop NaN before plotting. Do NOT plot a marker at y=NaN.

### Marker design

Use `marker_symbol='square-open'` (not filled) to create a border/outline effect that doesn't obscure the underlying heatmap. `line=dict(width=2)` on the marker dict gives a visible border. Size 14 makes the border span approximately one heatmap cell width.

### Files to modify

- `dashboard/charts.py` — add `add_iceberg_borders` function (new function at end of file)
- `dashboard/callbacks.py` — wire `add_iceberg_borders` into `update_heatmap`

### References

- Epic 19 story 19.5 (iceberg fields): `_bmad-output/implementation-artifacts/stories/19-5-divergence-cvd-iceberg-group-d.md`
- Story 20.1 (coercion pattern): `_bmad-output/implementation-artifacts/stories/20-1-liquidity-overlay-absorption-markers.md`
- Current `add_ob_depth_heatmap`: `dashboard/charts.py` lines 166-195
- Current `update_heatmap` callback: `dashboard/callbacks.py` lines 140-149

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- Added `add_iceberg_borders(fig, df)` to `dashboard/charts.py`: guards on empty df and missing ts/iceberg columns; coerces iceberg_bid_detected and iceberg_ask_detected via pd.to_numeric; drops NaN iceberg_price rows; adds cyan (#00BCD4) square-open scatter for bid detections and magenta (#E040FB) square-open scatter for ask detections; no row/col args (simple go.Figure).
- Wired into `update_heatmap` in `dashboard/callbacks.py` after `add_ob_depth_heatmap`.
- Rebuilt dashboard container; no import errors; curl localhost:8050 returns 200.
- [Review][Patch] Replaced `prices[mask].values` with `prices[mask]` (index-aligned assignment, no `.values` strip) in both bid and ask branches.
- [Review][Patch] Added `.replace({"true": 1, "false": 0, "True": 1, "False": 0})` normalisation before `pd.to_numeric` on both detection columns to handle string-encoded booleans from any future data path.
- [Review][Defer] Wrong ask price when both bid+ask flags fire simultaneously — upstream Go `if/else-if` writes single `iceberg_price`; requires new schema columns to fix; deferred.
- [Review][Defer] `hoverinfo="skip"` / `showlegend=False` UX — visual polish, deferred.

### File List

- dashboard/charts.py
- dashboard/callbacks.py
