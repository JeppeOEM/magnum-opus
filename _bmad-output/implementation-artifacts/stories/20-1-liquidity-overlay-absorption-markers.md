# Story 20.1: Liquidity Overlay + Absorption Markers (Activate Epic 18 Stubs)

Status: done

## Story

As a trader,
I want the candlestick panel to show a POC line, Value Area band, and absorption diamond markers,
so that I can read footprint-derived price structure directly on the price chart.

**Pre-conditions:** Epic 19 complete (poc_price, value_area_high, value_area_low, absorption_detected now written to snapshot_1s). Epic 18 dashboard running with stub functions wired in update_candlestick.

## Acceptance Criteria

### AC 1 — liquidity_overlay renders POC line and Value Area band

1. When `poc_price`, `value_area_high`, `value_area_low` are all present as columns in `df` AND have at least one non-null row, `liquidity_overlay(fig, df)` adds:
   - An `add_hrect` call with `y0=va_low, y1=va_high` using fill colour `rgba(255,200,0,0.08)` and `line_width=0` (Value Area band)
   - A `go.Scatter` horizontal line at `y=poc_price` (most recent non-null poc_price), rendered as `mode='lines'`, `line=dict(color='#FFD700', dash='dot', width=1)`, `name='POC'`, `showlegend=False`, with x spanning the full timestamp range of `df["ts"]` (first and last ts as the two x-points)
2. When any of the three fields is absent from `df.columns` or all values are null, the function returns `fig` unchanged (no crash, no empty trace).
3. `va_low` and `va_high` are computed as the mean (or last non-null) of `value_area_low` and `value_area_high` respectively — use the most recent (last) non-null value across the visible window.
4. No `row=` or `col=` argument is passed to `add_hrect` or `add_trace` (figure is a simple go.Figure, not a subplot).

### AC 2 — absorption_overlay renders diamond markers

5. When `absorption_detected` is present in `df.columns` AND at least one row has a truthy value (True or 1), `absorption_overlay(fig, df)` adds a `go.Scatter` trace:
   - `mode='markers'`
   - `marker=dict(symbol='diamond', size=10, color='#FF9800', opacity=0.8)`
   - x = timestamps of candles where `absorption_detected` is truthy
   - y = `close` price of those candles
   - `name='Absorption'`, `showlegend=False`, `hoverinfo='skip'`
6. When `absorption_detected` is absent or all-false/null, the function returns `fig` unchanged.
7. No `row=` or `col=` argument passed to `add_trace`.

### AC 3 — Existing behaviour preserved

8. When neither field group is present (old historical rows pre-Epic 19), both overlays are no-ops — `update_candlestick` still renders candlestick + volume levels + volume bubbles correctly.
9. No crash when `df` is empty (both overlays must guard on `df.empty`).
10. Dashboard container starts without import errors; `curl localhost:8050` returns 200.

## Tasks / Subtasks

- [x] Task 1: Implement `liquidity_overlay` in `dashboard/callbacks.py` (AC 1, 3)
  - [x] 1.1 Guard: if `df.empty` or any of poc_price/value_area_high/value_area_low absent → return fig unchanged
  - [x] 1.2 Drop NaN rows for the three columns; if empty after drop → return fig unchanged
  - [x] 1.3 Compute `va_low` = last non-null value_area_low, `va_high` = last non-null value_area_high, `poc` = last non-null poc_price
  - [x] 1.4 Call `fig.add_hrect(y0=va_low, y1=va_high, fillcolor="rgba(255,200,0,0.08)", line_width=0)` (no row/col)
  - [x] 1.5 Add POC scatter: two-point go.Scatter spanning full ts range

- [x] Task 2: Implement `absorption_overlay` in `dashboard/callbacks.py` (AC 2, 3)
  - [x] 2.1 Guard: if `df.empty` or `absorption_detected` not in df.columns → return fig unchanged
  - [x] 2.2 Convert absorption_detected to bool/numeric; filter rows where truthy; if empty → return fig unchanged
  - [x] 2.3 Add diamond scatter at close price for absorption candles

- [x] Task 3: Verify
  - [x] 3.1 Rebuild dashboard container; confirm no import errors in logs
  - [x] 3.2 `curl localhost:8050` returns 200
  - [x] 3.3 Confirm old data (no poc_price etc.) still renders candlestick without crash (check logs for errors)

## Dev Notes

### Dashboard architecture (post-fix)

After the fix in commit `0411bae`, each chart builder returns a **standalone `go.Figure()`** — NOT a make_subplots figure. This means:
- Do NOT pass `row=` or `col=` to `add_hrect`, `add_hline`, `add_shape`, or `add_trace` in the candlestick overlay functions.
- The `add_hrect` in `add_volume_levels` (charts.py) already omits row/col — follow the same pattern.

### Field availability

- New data (from candle-blue after migration fix) will have poc_price, value_area_high, value_area_low, absorption_detected populated.
- Historical data (95K+ rows before Epic 19) will have NULL for all these fields → QuestDB returns None in the dataset.
- pandas will represent these as NaN in float columns and None/NaN in bool columns.
- Always drop NaN before computing va_low/va_high/poc.

### Stub locations

Both stubs are in `dashboard/callbacks.py`:
- `absorption_overlay` at line ~88: currently returns fig unchanged
- `liquidity_overlay` at line ~95: currently checks for column presence but returns fig unchanged
- Both are already called inside `update_candlestick` (no wiring change needed)

### go.Scatter for horizontal line

Use two-point Scatter (not `add_hline`) to avoid Plotly's axis-reference issues on simple figures:
```python
fig.add_trace(go.Scatter(
    x=[df["ts"].iloc[0], df["ts"].iloc[-1]],
    y=[float(poc), float(poc)],
    mode="lines",
    line=dict(color="#FFD700", dash="dot", width=1),
    name="POC", showlegend=False,
))
```

### absorption_detected field type

QuestDB returns BOOLEAN as true/false JSON values. pandas will read them as Python bool. But the column may be mixed (None + True/False). Safe coercion:
```python
mask = pd.to_numeric(df["absorption_detected"], errors="coerce").fillna(0).astype(bool)
absorbed = df[mask]
```

### Project context

- Dashboard is Python / Dash + Plotly running in Docker on port 8050
- `dashboard/callbacks.py` is the only file to modify
- `dashboard/charts.py` is NOT touched (overlay functions live in callbacks.py by design established in Epic 18)
- No new dependencies needed (go.Scatter already imported via plotly.graph_objects as go)

### References

- Stub code: `dashboard/callbacks.py` lines 88-106
- Epic 18 story 18.3 (absorption stub established): `_bmad-output/implementation-artifacts/stories/18-3-candlestick-overlays-volume-bars-absorption.md`
- Epic 18 story 18.6 (liquidity_overlay stub established): `_bmad-output/implementation-artifacts/stories/18-6-layout-assembly-poc-liquidity-overlay.md`
- Epic 19 story 19.4 (absorption_detected field): `_bmad-output/implementation-artifacts/stories/19-4-unfinished-auction-absorption-group-b.md`
- Epic 19 story 19.2 (poc_price, value_area fields): `_bmad-output/implementation-artifacts/stories/19-2-value-area-signal-group-c.md`

### Review Findings

- [x] [Review][Patch] absorption_overlay never wired into update_candlestick — dead code, AC 2 unreachable [dashboard/callbacks.py]
- [x] [Review][Patch] "ts" column absent → uncaught KeyError in liquidity_overlay [dashboard/callbacks.py]
- [x] [Review][Patch] "close" column absent → uncaught KeyError in absorption_overlay [dashboard/callbacks.py]
- [x] [Review][Patch] va_low > va_high inversion passes to add_hrect silently wrong [dashboard/callbacks.py]
- [x] [Review][Defer] Single-row df produces zero-width POC line — low severity, visual only — deferred, pre-existing
- [x] [Review][Defer] all-NaN close in absorption_overlay → silent invisible trace — deferred, pre-existing

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- Implemented `liquidity_overlay`: guards on empty df + missing/all-null columns; computes last non-null va_low/va_high/poc; adds add_hrect for VA band and two-point go.Scatter for POC line (no row/col args — simple figure).
- Implemented `absorption_overlay`: guards on empty df + missing column; coerces to numeric bool; adds diamond scatter at close price for absorbed candles.
- All 6 edge-case tests pass in container. Dashboard returns 200 with callbacks returning 200 (data loading + rendering confirmed).
- No row/col args used (consistent with post-fix simple-figure architecture).

### File List

- dashboard/callbacks.py
