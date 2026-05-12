# Story 18.4: Heatmap Panels — Delta + OB Depth + Volume Profile

Status: done

## Story

As a trader,
I want Delta Heatmap, OB Depth Heatmap, and Volume Profile panels with a shared price Y-axis,
So that I can read order flow pressure and resting liquidity aligned to the candlestick price levels.

**Pre-conditions:** Stories 18.1, 18.2a, 18.2b complete. ADR-18-01 grid in place (col=3 is rowspan=2).

## Acceptance Criteria

**AC 1 — Delta Heatmap**
- `go.Heatmap` at subplot col=3 (rowspan=2)
- Diverging red/green colorscale centred at zero
- Cell value = `2 * buy_volume - volume` per candle
- Price on Y-axis, time on X-axis
- Delta computed in Dash from QuestDB fields — no candle service changes

**AC 2 — OB Depth Heatmap**
- Second `go.Heatmap` in the same col=3 subplot
- Colour intensity = `bid_depth_l1 + ask_depth_l1` from ob-store entries
- Shares Y-axis with Delta panel (by virtue of rowspan=2 col-3 subplot)
- Empty ob-store → empty heatmap with axis labels, no crash

**AC 3 — Volume Profile panel**
- `go.Bar(orientation='h')` at subplot `row=1, col=2`
- Price on Y-axis (shared with candlestick via `shared_yaxes='rows'`)
- Bar length = total `volume` per price level (same bucketing as 18.3 volume levels)
- Replaces "Vol Profile" placeholder in layout.py

**AC 4 — Y-axis sync (ADR-18-03)**
- `relayoutData` callback: when user zooms candlestick X or Y axis, col=3 subplot Y-axis range updates to match
- If zoom sync causes visible jitter during testing, feature explicitly marked as known limitation and deferred — story does not fail on this

**AC 5 — dcc.Graph placement**
- `dcc.Graph(id='vol-profile-graph')` replaces Vol Profile placeholder in layout.py
- `dcc.Graph(id='heatmap-graph')` replaces Heatmaps placeholder in layout.py
- Both wrapped in `dcc.Loading`

**AC 6 — Empty candle-store / ob-store**
- When `candle-store=[]`, delta heatmap and vol profile return empty figures without crash
- When `ob-store=[]`, OB depth heatmap returns empty figure without crash

## Tasks / Subtasks

- [x] Task 1: Update `dashboard/charts.py`
  - [x] 1.1 `build_delta_heatmap(df)` — returns go.Figure with delta heatmap at col=3
  - [x] 1.2 `build_vol_profile(df)` — returns go.Figure with horizontal bar at row=1,col=2
  - [x] 1.3 `add_ob_depth_heatmap(fig, ob_rows)` — adds OB depth heatmap trace to col=3 figure

- [x] Task 2: Update `dashboard/layout.py`
  - [x] 2.1 Replace "Vol Profile" placeholder with `dcc.Graph(id='vol-profile-graph')`
  - [x] 2.2 Replace "Heatmaps" placeholder with `dcc.Graph(id='heatmap-graph')`

- [x] Task 3: Update `dashboard/callbacks.py`
  - [x] 3.1 Add `update_heatmap` callback: Input=candle-store+ob-store, Output=heatmap-graph figure
  - [x] 3.2 Add `update_vol_profile` callback: Input=candle-store, Output=vol-profile-graph figure
  - [x] 3.3 Add `sync_yaxis_zoom` callback: Input=candlestick-graph relayoutData, Output=heatmap-graph relayoutData (or figure update for y-range)

- [x] Task 4: Verify
  - [x] 4.1 Container starts with 200 response
  - [x] 4.2 No import errors or crash in logs

## Dev Notes

### Delta Heatmap — build_delta_heatmap

The delta heatmap uses price bins (same bucketing as volume levels) as Y, timestamps as X, and delta = `2*buy_volume - volume` as Z.

```python
def build_delta_heatmap(df: pd.DataFrame) -> go.Figure:
    fig = _base_fig()
    if df.empty:
        return fig
    required = ["ts", "close", "buy_volume", "volume"]
    if not all(c in df.columns for c in required):
        return fig
    
    df = df.dropna(subset=["buy_volume", "volume"]).copy()
    df["delta"] = 2 * df["buy_volume"] - df["volume"]
    
    fig.add_trace(
        go.Heatmap(
            x=df["ts"],
            y=df["close"],
            z=df["delta"],
            colorscale=[
                [0.0, "#EF5350"],   # red for negative delta
                [0.5, "#222222"],   # neutral dark centre
                [1.0, "#26A69A"],   # green for positive delta
            ],
            zmid=0,
            showscale=False,
            name="Delta",
        ),
        row=1, col=3,
    )
    fig.update_layout(
        paper_bgcolor="#222222",
        plot_bgcolor="#222222",
        font_color="#CCCCCC",
        margin=dict(l=10, r=10, t=20, b=30),
    )
    return fig
```

Note: col=3 is `rowspan=2` — both row=1,col=3 and row=2,col=3 map to the same subplot. Use `row=1, col=3` for all traces in this subplot.

### OB Depth Heatmap — add_ob_depth_heatmap

```python
def add_ob_depth_heatmap(fig: go.Figure, ob_rows: list[dict]) -> go.Figure:
    if not ob_rows:
        return fig
    import pandas as pd
    ob_df = pd.DataFrame(ob_rows)
    required_ob = ["ts", "best_bid", "bid_depth_l1", "ask_depth_l1"]
    if not all(c in ob_df.columns for c in required_ob):
        return fig
    ob_df = ob_df.dropna(subset=["bid_depth_l1", "ask_depth_l1"]).copy()
    for col in ["bid_depth_l1", "ask_depth_l1", "best_bid"]:
        ob_df[col] = pd.to_numeric(ob_df[col], errors="coerce")
    ob_df = ob_df.dropna()
    ob_df["depth"] = ob_df["bid_depth_l1"] + ob_df["ask_depth_l1"]
    
    fig.add_trace(
        go.Heatmap(
            x=ob_df["ts"],
            y=ob_df["best_bid"],
            z=ob_df["depth"],
            colorscale="Blues",
            showscale=False,
            opacity=0.6,
            name="OB Depth",
        ),
        row=1, col=3,
    )
    return fig
```

Note: ob_features stream entries are all strings (Redis stores everything as strings). Cast to numeric before operations.

### Volume Profile — build_vol_profile

```python
def build_vol_profile(df: pd.DataFrame) -> go.Figure:
    fig = _base_fig()
    if df.empty:
        return fig
    required = ["close", "volume"]
    if not all(c in df.columns for c in required):
        return fig
    
    df = df.dropna(subset=["volume"]).copy()
    price_range = df["close"].max() - df["close"].min()
    if price_range <= 0:
        return fig
    bucket_size = max(price_range / 50, 0.01)
    df["price_level"] = (df["close"] / bucket_size).round() * bucket_size
    level_vol = df.groupby("price_level")["volume"].sum().reset_index()
    
    fig.add_trace(
        go.Bar(
            x=level_vol["volume"],
            y=level_vol["price_level"],
            orientation="h",
            marker_color="rgba(100,130,210,0.5)",
            name="Vol Profile",
            showlegend=False,
        ),
        row=1, col=2,
    )
    fig.update_layout(
        paper_bgcolor="#222222",
        plot_bgcolor="#222222",
        font_color="#CCCCCC",
        margin=dict(l=5, r=5, t=20, b=30),
        bargap=0.05,
    )
    return fig
```

### Y-axis sync callback (ADR-18-03)

Implement as a callback that reads `relayoutData` from the candlestick graph and updates the heatmap figure's yaxis range:

```python
@callback(
    Output("heatmap-graph", "figure", allow_duplicate=True),
    Input("candlestick-graph", "relayoutData"),
    State("heatmap-graph", "figure"),
    prevent_initial_call=True,
)
def sync_yaxis_zoom(relay_data, heatmap_fig):
    if not relay_data or not heatmap_fig:
        return no_update
    y_range = None
    if "yaxis.range[0]" in relay_data and "yaxis.range[1]" in relay_data:
        y_range = [relay_data["yaxis.range[0]"], relay_data["yaxis.range[1]"]]
    elif "yaxis.autorange" in relay_data:
        y_range = None  # reset
    if y_range is None:
        return no_update
    import copy
    fig = copy.deepcopy(heatmap_fig)
    fig["layout"]["yaxis"]["range"] = y_range
    return fig
```

Note: `relayoutData` is a dict from Plotly; it fires on every zoom/pan. The callback is lightweight since it only modifies the yaxis range in the figure dict.

### Callbacks for update_heatmap and update_vol_profile

```python
@callback(
    Output("heatmap-graph", "figure"),
    Input("candle-store", "data"),
    Input("ob-store", "data"),
)
def update_heatmap(candle_rows, ob_rows):
    df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
    fig = charts.build_delta_heatmap(df)
    fig = charts.add_ob_depth_heatmap(fig, ob_rows or [])
    return fig

@callback(
    Output("vol-profile-graph", "figure"),
    Input("candle-store", "data"),
)
def update_vol_profile(candle_rows):
    df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
    return charts.build_vol_profile(df)
```

### layout.py replacements

```python
# Replace Vol Profile placeholder:
dcc.Loading(
    dcc.Graph(id="vol-profile-graph", figure={}, style={"height": "420px"}),
    id="loading-vol-profile",
),

# Replace Heatmaps placeholder:
dcc.Loading(
    dcc.Graph(id="heatmap-graph", figure={}, style={"height": "720px"}),
    id="loading-heatmaps",
),
```

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- All three chart functions implemented in charts.py: build_delta_heatmap (diverging red/green, zmid=0), add_ob_depth_heatmap (Blues colorscale, opacity 0.6), build_vol_profile (horizontal bar, 50 price buckets)
- layout.py already had dcc.Graph placeholders with correct IDs wrapped in dcc.Loading
- callbacks.py wired update_heatmap, update_vol_profile, and sync_yaxis_zoom; sync_yaxis_zoom uses deepcopy to avoid mutating the store
- Empty-store guards: all three chart functions return base figure immediately on empty input — no crash
- Container started cleanly, HTTP 200, no import errors in logs
- Y-axis sync implemented; no visible jitter observed during manual test

### File List

- dashboard/charts.py
- dashboard/layout.py
- dashboard/callbacks.py

### Review Findings

- [x] [Review][Patch] Move `import copy as _copy` to module level — placed inside hot callback body, evaluated on every invocation [dashboard/callbacks.py:124]
- [x] [Review][Patch] Drop NaN `close` values before building delta heatmap y-axis — `dropna(subset=["buy_volume","volume"])` does not cover close; NaN close values passed inline to Heatmap y= [dashboard/charts.py:137]
- [x] [Review][Patch] Float-cast relay_data range values in `sync_yaxis_zoom` — values written to layout.yaxis.range without explicit float() cast [dashboard/callbacks.py:130]
- [x] [Review][Defer] Unbounded `ob-store` / `candle-store` growth in `live_update` [dashboard/callbacks.py:61,67] — deferred, pre-existing
