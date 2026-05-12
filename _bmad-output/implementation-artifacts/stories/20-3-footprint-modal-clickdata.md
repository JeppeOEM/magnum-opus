# Story 20.3: Footprint Modal on Candlestick clickData

Status: done

## Story

As a trader,
I want to click any candlestick bar and see the per-price-level footprint (buy/sell volume ladder) for that bar,
so that I can inspect micro-auction structure at any point in the session.

**Pre-conditions:** Story 20.2 done. Dashboard running. `footprint_json` written to `snapshot_1s` by candle-service (Epic 19 story 19.1). Candle-store already loads `footprint_json` via `SELECT *`.

## Acceptance Criteria

### AC 1 — Modal appears on candlestick click

1. A `dbc.Modal` with `id="footprint-modal"` is added to `dashboard/layout.py`.
2. The modal contains a `dcc.Graph` with `id="footprint-chart"` in its body.
3. A callback wired to `Input("candlestick-graph", "clickData")` and `State("candle-store", "data")` opens the modal (`is_open=True`) and populates `footprint-chart` with the footprint figure for the clicked candle.
4. When `clickData` is None or the clicked candle has no `footprint_json`, the modal does NOT open (returns `no_update`).

### AC 2 — Footprint chart content

5. The footprint chart is a horizontal bidirectional bar chart:
   - Y-axis: price levels sorted **descending** (highest price at top, lowest at bottom) — matching a ladder view
   - Buy volume: positive x (green, `#26A69A`)
   - Sell volume: negative x (red, `#EF5350`)
   - Both as `go.Bar` traces with `orientation="h"` on the same x-axis, `barmode="overlay"` or `barmode="relative"`
6. Chart uses the dark theme (`paper_bgcolor="#222222"`, `plot_bgcolor="#222222"`, `font_color="#CCCCCC"`).
7. Modal title shows the clicked candle's timestamp.

### AC 3 — Graceful handling

8. When `footprint_json` is absent from candle-store columns → `no_update` (modal stays closed).
9. When `footprint_json` for the clicked row is `None`/null → `no_update`.
10. When `footprint_json` is present but fails JSON parse → `no_update`, no crash.
11. When `clickData` is malformed (no `points` or no `x`) → `no_update`, no crash.

### AC 4 — No regression

12. Dashboard returns 200; no import errors.
13. No change to existing callbacks or chart outputs.

## Tasks / Subtasks

- [x] Task 1: Add modal to `dashboard/layout.py` (AC 1)
  - [x] 1.1 Import `dbc.Modal`, `dbc.ModalHeader`, `dbc.ModalBody` (already available via dash_bootstrap_components)
  - [x] 1.2 Add `dbc.Modal([dbc.ModalHeader(id="footprint-modal-title"), dbc.ModalBody(dcc.Graph(id="footprint-chart", figure={}))], id="footprint-modal", is_open=False, size="lg")` to layout after the dcc.Store block

- [x] Task 2: Add `build_footprint_chart` to `dashboard/charts.py` (AC 2)
  - [x] 2.1 Parse `footprint_json` string (json.loads); guard on failure → return empty figure
  - [x] 2.2 Sort price levels descending; build buy_vols and sell_vols lists
  - [x] 2.3 Return `go.Figure` with two `go.Bar(orientation="h")` traces: buy (positive x, green) and sell (negative x, red)
  - [x] 2.4 Apply dark theme; set `barmode="overlay"`, y-axis `autorange="reversed"` not needed since sorted descending

- [x] Task 3: Add `open_footprint_modal` callback to `dashboard/callbacks.py` (AC 1, 3)
  - [x] 3.1 `@callback(Output("footprint-modal", "is_open"), Output("footprint-chart", "figure"), Output("footprint-modal-title", "children"), Input("candlestick-graph", "clickData"), State("candle-store", "data"), prevent_initial_call=True)`
  - [x] 3.2 Guard: `clickData` None or no `points` → `no_update, no_update, no_update`
  - [x] 3.3 Extract `clicked_ts = clickData["points"][0]["x"]`
  - [x] 3.4 Find matching candle row in `candle_rows` where `row["ts"] == clicked_ts`; if not found → `no_update`
  - [x] 3.5 Read `footprint_json` from row; if None → `no_update`
  - [x] 3.6 Call `charts.build_footprint_chart(footprint_json_str)`; if returns empty fig → `no_update`
  - [x] 3.7 Return `True, fig, f"Footprint: {clicked_ts}"`

- [x] Task 4: Verify (AC 4)
  - [x] 4.1 Rebuild dashboard container; no import errors
  - [x] 4.2 `curl localhost:8050` returns 200
  - [x] 4.3 No errors in logs after rebuild

## Dev Notes

### footprint_json format

Stored in `snapshot_1s.footprint_json` as a JSON string. Format:
```json
{"100.5": {"b": 123.4, "s": 45.6}, "101.0": {"b": 67.8, "s": 89.0}}
```
Keys are price-level strings (numeric). Values: `b` = buy volume, `s` = sell volume.

The candle-store already fetches `SELECT *` from `snapshot_1s`, so `footprint_json` is already present in each row dict when the candle-service has emitted it. Historical rows (pre-Epic 19) will have `footprint_json=None`.

### clickData structure (Plotly Candlestick)

When a user clicks a candlestick bar, Plotly fires:
```json
{
  "points": [{
    "x": "2026-05-12T14:23:01.000000000Z",
    "open": 103200.5,
    "high": 103210.0,
    "low": 103190.0,
    "close": 103205.0,
    "curveNumber": 0,
    "pointNumber": 42
  }]
}
```
Use `clickData["points"][0]["x"]` to get the timestamp, then match against `row["ts"]` in candle-store.

### Modal component IDs

- `id="footprint-modal"` — the Modal itself (is_open controlled by callback)
- `id="footprint-modal-title"` — the ModalHeader children (set to timestamp string)
- `id="footprint-chart"` — the Graph inside ModalBody

### build_footprint_chart return for empty/error

Return `go.Figure()` with dark layout but no traces when footprint_json is invalid or empty. The callback detects empty figure by checking `len(fig.data) == 0` and returns `no_update` instead of opening the modal.

### Bidirectional bar chart construction

```python
prices_sorted = sorted(fp.keys(), key=float, reverse=True)  # descending
buy_vols  = [float(fp[p].get("b", 0)) for p in prices_sorted]
sell_vols = [-float(fp[p].get("s", 0)) for p in prices_sorted]  # negative x

fig.add_trace(go.Bar(y=prices_sorted, x=buy_vols,  orientation="h", name="Buy",  marker_color="#26A69A", showlegend=False))
fig.add_trace(go.Bar(y=prices_sorted, x=sell_vols, orientation="h", name="Sell", marker_color="#EF5350", showlegend=False))
fig.update_layout(barmode="overlay", ...)
```

### Files to modify

- `dashboard/layout.py` — add `dbc.Modal` block after dcc.Store components
- `dashboard/charts.py` — add `build_footprint_chart(footprint_json_str: str) -> go.Figure`
- `dashboard/callbacks.py` — add `open_footprint_modal` callback

### References

- footprint_json accumulation: `candle-service/internal/accumulator/accumulator.go` lines 600–612
- footprint format: `{"price": {"b": buy_vol, "s": sell_vol}, ...}`
- dbc.Modal docs: standard dash-bootstrap-components Modal — `is_open`, `size="lg"`, `dbc.ModalHeader`, `dbc.ModalBody`
- clickData structure: standard Plotly Candlestick click event `points[0]["x"]`
- Existing dark theme: `_DARK` dict in `dashboard/charts.py` lines 5–9

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- Added `build_footprint_chart(footprint_json_str)` to `dashboard/charts.py`: full try/except wraps entire body (json.loads + comprehensions + add_trace); sorts by numeric price value descending (not string sort); uses numeric float y-axis for proportional price ladder; buy=positive/green, sell=negative/red, barmode="overlay".
- Replaced inline `__import__("json")` with top-level `import json` in charts.py.
- Added `dbc.Modal` with id="footprint-modal" to `dashboard/layout.py` after dcc.Store block; contains `dcc.Graph(id="footprint-chart")` in ModalBody, size="lg".
- Added `open_footprint_modal` callback to `dashboard/callbacks.py`: guards on None clickData, missing points, no matching row, None footprint_json, empty fig. Normalizes timestamps to second precision with `pd.Timestamp(ts).floor("s")` to handle Plotly's clickData x-value format normalization.
- Rebuilt dashboard; no import errors; curl localhost:8050 returns 200.
- [Review][Patch] Timestamp normalization: Plotly strips T/Z/microseconds from clickData x; now comparing both sides via pd.Timestamp.floor("s").
- [Review][Patch] Full try/except wraps comprehensions and add_trace to prevent Dash worker crash from malformed cells.
- [Review][Patch] Numeric y-axis (float prices) instead of categorical string keys for proportional price spacing.
- [Review][Patch] Replaced __import__("json") with top-level import json.
- [Review][Defer] Modal stays open on symbol switch — UX issue, deferred.
- [Review][Defer] Re-open after client-side dismiss race — complex Dash state, deferred.

### File List

- dashboard/charts.py
- dashboard/layout.py
- dashboard/callbacks.py
