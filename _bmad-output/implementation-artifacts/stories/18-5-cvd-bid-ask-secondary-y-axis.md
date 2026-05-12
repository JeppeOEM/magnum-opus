# Story 18.5: Secondary Panels — CVD + Bid/Ask Volume

Status: done

## Story

As a trader,
I want CVD and Bid/Ask Volume panels in the bottom row,
so that I can read cumulative aggressor direction and volume-side dominance over time.

**Pre-conditions:** Stories 18.2a, 18.2b complete. candle-store contains `buy_volume` and `volume` from `snapshot_1s`.

## Acceptance Criteria

**AC 1 — CVD Panel**
- CVD computed as `cumsum(2 * buy_volume - volume)` over candle-store rows sorted ascending by `ts`
- `go.Scatter` with `fill='tozeroy'` at subplot `row=2, col=1`
- Fill colour: green (`rgba(38,166,154,0.3)`) where CVD > 0, red (`rgba(239,83,80,0.3)`) where CVD ≤ 0 — use two separate clipped traces (positive clip + negative clip) to achieve per-segment colouring
- No `cvd_divergence` markers rendered — field not yet emitted by candle service

**AC 2 — Bid/Ask Volume Panel**
- Ask bars (`buy_volume`, green) extend above zero as `go.Bar` at subplot `row=2, col=2`
- Bid bars (`volume - buy_volume`, negated so they extend below zero, red) as a second `go.Bar`
- Ratio line (`buy_volume / volume`) on secondary Y-axis (range 0–1) as `go.Scatter`
- Secondary Y-axis declared via `{"secondary_y": True}` in the `specs` for `row=2, col=2`

**AC 3 — app.py specs update**
- `make_subplots` `specs` updated to `[[{}, {}, {"rowspan": 2}], [{}, {"secondary_y": True}, None]]`
- All existing panels (candlestick, vol-profile, heatmap, cvd) continue to work after this change

**AC 4 — dcc.Graph placement**
- `dcc.Graph(id='cvd-graph')` replaces CVD placeholder in layout.py, wrapped in `dcc.Loading`
- `dcc.Graph(id='bidask-graph')` replaces Bid/Ask placeholder in layout.py, wrapped in `dcc.Loading`

**AC 5 — Empty / NaN handling**
- `candle-store=[]` → both panels return empty figure without crash
- Candles with `buy_volume=None/NaN` excluded from CVD cumsum and Bid/Ask bars without crashing

## Tasks / Subtasks

- [x] Task 1: Update `dashboard/app.py`
  - [x] 1.1 Change `specs` in `make_subplots` to `[[{}, {}, {"rowspan": 2}], [{}, {"secondary_y": True}, None]]`

- [x] Task 2: Add functions to `dashboard/charts.py`
  - [x] 2.1 `build_cvd_panel(df)` — returns go.Figure with two clipped CVD scatter traces (positive green, negative red) at row=2, col=1
  - [x] 2.2 `build_bidask_panel(df)` — returns go.Figure with ask bars (green, above zero), bid bars (red, below zero), and ratio Scatter on secondary_y at row=2, col=2

- [x] Task 3: Update `dashboard/layout.py`
  - [x] 3.1 Replace CVD placeholder `dbc.Card` with `dcc.Loading(dcc.Graph(id='cvd-graph', ...))`
  - [x] 3.2 Replace Bid/Ask placeholder `dbc.Card` with `dcc.Loading(dcc.Graph(id='bidask-graph', ...))`

- [x] Task 4: Update `dashboard/callbacks.py`
  - [x] 4.1 Add `update_cvd` callback: Input=candle-store, Output=cvd-graph figure
  - [x] 4.2 Add `update_bidask` callback: Input=candle-store, Output=bidask-graph figure

- [x] Task 5: Verify
  - [x] 5.1 Container starts with 200 response
  - [x] 5.2 No import errors or crash in logs

## Dev Notes

### Critical: app.py BASE_FIGURE change

`dashboard/app.py` defines `BASE_FIGURE` (the subplot template all panels share via `_base_fig()`). The current `specs` has `{}` for row=2,col=2. This story must change it to `{"secondary_y": True}` to enable the secondary Y-axis for the Bid/Ask panel:

```python
# Before (current)
specs=[[{}, {}, {"rowspan": 2}], [{}, {}, None]]

# After (this story)
specs=[[{}, {}, {"rowspan": 2}], [{}, {"secondary_y": True}, None]]
```

**This change affects all panels** — verify existing panels (candlestick, vol-profile, heatmap) still render after this change. They should be unaffected because they don't use row=2,col=2.

### CVD Panel — build_cvd_panel

Use two separate Scatter traces with clipped y values to achieve per-region fill colouring (positive = green fill, negative = red fill). Plotly's `fill='tozeroy'` doesn't support per-point colours, so this is the standard pattern:

```python
def build_cvd_panel(df: pd.DataFrame) -> go.Figure:
    fig = _base_fig()
    if df.empty:
        return fig
    required = ["ts", "buy_volume", "volume"]
    if not all(c in df.columns for c in required):
        return fig

    df = df.copy()
    df["buy_volume"] = pd.to_numeric(df["buy_volume"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df = df.dropna(subset=["buy_volume", "volume", "ts"]).sort_values("ts")
    if df.empty:
        return fig

    df["cvd"] = (2 * df["buy_volume"] - df["volume"]).cumsum()

    pos = df["cvd"].clip(lower=0)
    neg = df["cvd"].clip(upper=0)

    fig.add_trace(
        go.Scatter(
            x=df["ts"], y=pos,
            fill="tozeroy",
            fillcolor="rgba(38,166,154,0.3)",
            line=dict(color="#26A69A", width=1),
            mode="lines",
            name="CVD+",
            showlegend=False,
        ),
        row=2, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df["ts"], y=neg,
            fill="tozeroy",
            fillcolor="rgba(239,83,80,0.3)",
            line=dict(color="#EF5350", width=1),
            mode="lines",
            name="CVD-",
            showlegend=False,
        ),
        row=2, col=1,
    )
    fig.update_layout(
        paper_bgcolor="#222222",
        plot_bgcolor="#222222",
        font_color="#CCCCCC",
        margin=dict(l=40, r=10, t=10, b=30),
    )
    return fig
```

### Bid/Ask Panel — build_bidask_panel

Ask bars are positive (buy_volume), bid bars are negative (-(volume - buy_volume) = buy_volume - volume). The ratio line goes on the secondary Y-axis (range 0–1).

The `add_trace(..., secondary_y=True)` parameter is a keyword argument to Plotly's `Figure.add_trace` when the figure has secondary_y specs. It routes the trace to the secondary axis of the specified subplot.

```python
def build_bidask_panel(df: pd.DataFrame) -> go.Figure:
    fig = _base_fig()
    if df.empty:
        return fig
    required = ["ts", "buy_volume", "volume"]
    if not all(c in df.columns for c in required):
        return fig

    df = df.copy()
    df["buy_volume"] = pd.to_numeric(df["buy_volume"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df = df.dropna(subset=["buy_volume", "volume"])
    df = df[df["volume"] > 0].copy()
    if df.empty:
        return fig

    ask_vol = df["buy_volume"]
    bid_vol = -(df["volume"] - df["buy_volume"])
    ratio = (df["buy_volume"] / df["volume"]).clip(0, 1)

    fig.add_trace(
        go.Bar(
            x=df["ts"], y=ask_vol,
            marker_color="#26A69A",
            name="Ask Vol",
            showlegend=False,
        ),
        row=2, col=2,
    )
    fig.add_trace(
        go.Bar(
            x=df["ts"], y=bid_vol,
            marker_color="#EF5350",
            name="Bid Vol",
            showlegend=False,
        ),
        row=2, col=2,
    )
    fig.add_trace(
        go.Scatter(
            x=df["ts"], y=ratio,
            line=dict(color="#CCCCCC", width=1),
            mode="lines",
            name="Ratio",
            showlegend=False,
        ),
        row=2, col=2, secondary_y=True,
    )
    fig.update_yaxes(range=[0, 1], row=2, col=2, secondary_y=True)
    fig.update_layout(
        paper_bgcolor="#222222",
        plot_bgcolor="#222222",
        font_color="#CCCCCC",
        margin=dict(l=40, r=10, t=10, b=30),
        barmode="overlay",
    )
    return fig
```

### Callbacks

```python
@callback(
    Output("cvd-graph", "figure"),
    Input("candle-store", "data"),
)
def update_cvd(candle_rows):
    df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
    return charts.build_cvd_panel(df)


@callback(
    Output("bidask-graph", "figure"),
    Input("candle-store", "data"),
)
def update_bidask(candle_rows):
    df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
    return charts.build_bidask_panel(df)
```

### layout.py replacements

Current bottom row uses `dbc.Card` placeholders at `width=6` each. Replace with:

```python
# CVD (width=6)
dcc.Loading(
    dcc.Graph(id="cvd-graph", figure={}, style={"height": "280px"}),
    id="loading-cvd",
)

# Bid/Ask (width=6)
dcc.Loading(
    dcc.Graph(id="bidask-graph", figure={}, style={"height": "280px"}),
    id="loading-bidask",
)
```

### Existing files to preserve

- `dashboard/app.py` — only change `specs` line; keep all other make_subplots params identical
- `dashboard/charts.py` — append new functions; do not modify existing functions
- `dashboard/callbacks.py` — append new callbacks; do not modify existing callbacks
- `dashboard/layout.py` — replace only the two placeholder Card elements; keep all dcc.Store, dcc.Interval, and Bootstrap structure intact

### Subplot grid reminder

```
row=1, col=1: candlestick (38%)
row=1, col=2: vol profile (12%)
row=1, col=3: heatmap (50%, rowspan=2)
row=2, col=1: CVD (38%)
row=2, col=2: Bid/Ask (12%, secondary_y=True)
row=2, col=3: None (covered by rowspan)
```

CVD and Bid/Ask are each in separate `dcc.Graph` elements (Bootstrap width=6 each). Within each graph, the trace targets its assigned subplot column of BASE_FIGURE. Columns 1 and 2 visually appear narrow within the graph because of the 38/12/50 column_widths, but this is the ADR-18-01 design.

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- app.py specs updated: row=2,col=2 now has secondary_y=True; all other panels unaffected
- build_cvd_panel: two clipped Scatter traces (pos clip green, neg clip red) with fill='tozeroy', CVD = cumsum(2*buy_volume - volume) sorted ascending by ts
- build_bidask_panel: ask bars (buy_volume, green), bid bars (-(volume-buy_volume), red), ratio Scatter on secondary_y clamped 0-1; barmode='overlay'
- layout.py: CVD and Bid/Ask placeholder Cards replaced with dcc.Graph wrapped in dcc.Loading
- callbacks.py: update_cvd and update_bidask callbacks added
- Container 200 response, no import errors

### File List

- dashboard/app.py
- dashboard/charts.py
- dashboard/layout.py
- dashboard/callbacks.py

### Review Findings

- [x] [Review][Patch] `build_bidask_panel` missing `"ts"` in dropna subset — ts NaN rows survive to Bar x-axis; AC 5 requires NaN excluded [dashboard/charts.py]
- [x] [Review][Patch] `build_bidask_panel` missing `sort_values("ts")` — bars can render out of temporal order after live-update appends; inconsistent with build_cvd_panel [dashboard/charts.py]
- [x] [Review][Defer] Unbounded `candle_rows` growth for CVD cumsum — pre-existing D-18-4-1
