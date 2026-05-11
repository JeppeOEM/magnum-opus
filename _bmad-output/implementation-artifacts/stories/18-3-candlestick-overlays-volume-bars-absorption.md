# Story 18.3: Candlestick Panel — OHLCV + Volume Overlays

Status: done

## Story

As a trader,
I want a candlestick panel with volume level, bubble, and absorption overlays,
So that I can read price action and volume concentration in a single view.

**Pre-conditions:** Stories 18.1, 18.2a complete (subplot grid at row=1 col=1; candle-store populated).

## Acceptance Criteria

**AC 1 — Candlestick chart**
- `go.Candlestick` renders OHLCV at subplot `row=1, col=1`
- Up-candles green, down-candles red, dark background (inherits DARKLY)
- Panel replaces the "Candlestick" placeholder `dbc.Card` in layout.py

**AC 2 — Volume levels overlay**
- Semi-transparent horizontal `go.Bar` traces (opacity ≤ 0.4) overlaid at price levels
- Price levels binned from `buy_volume` and `volume` fields in candle-store
- POC (price level with highest total volume) highlighted in accent colour `#FFD700`
- Value Area (70% of total volume around POC) shaded in `rgba(255,200,0,0.15)`

**AC 3 — Volume bubbles overlay**
- `go.Scatter` markers on the candlestick at each candle's mid-price
- Marker size scales with `volume` (normalised: `volume / volume.max() * 30`, min size 4)
- Colour green when `buy_volume / volume > 0.5`, red otherwise; null-safe (skip candle if either field missing)

**AC 4 — Absorption stub**
- `absorption_overlay` callback exists in `callbacks.py`
- Renders zero markers (field `absorption_detected` not in Epic 18 data)
- Code structured to activate when field is available (Epic 20)

**AC 5 — Live update**
- When live-interval appends new candles to candle-store, chart extends to show new candle at right edge

**AC 6 — dcc.Graph placement**
- `dcc.Graph(id='candlestick-graph')` replaces placeholder in layout.py
- `dcc.Loading` wrapper retained around the graph

**AC 7 — Empty candle-store**
- When `candle-store=[]`, callback returns empty `go.Figure()` without crash

## Tasks / Subtasks

- [ ] Task 1: Create `dashboard/charts.py`
  - [ ] 1.1 `build_candlestick(df)` — returns `go.Figure` from BASE_FIGURE template with candlestick trace at row=1,col=1
  - [ ] 1.2 `add_volume_levels(fig, df)` — adds horizontal bar overlay
  - [ ] 1.3 `add_volume_bubbles(fig, df)` — adds scatter bubble overlay

- [ ] Task 2: Update `dashboard/layout.py`
  - [ ] 2.1 Replace "Candlestick" `dbc.Card` placeholder with `dcc.Graph(id='candlestick-graph', figure={})`

- [ ] Task 3: Update `dashboard/callbacks.py`
  - [ ] 3.1 Add `update_candlestick` callback: Input=candle-store, Output=candlestick-graph figure
  - [ ] 3.2 Add `absorption_overlay` stub callback: Input=candle-store, Output=... (can be a no-op graph update)

- [ ] Task 4: Verify
  - [ ] 4.1 Container starts with 200 response
  - [ ] 4.2 Candlestick visible in browser at localhost:8050

## Dev Notes

### charts.py — module structure

```python
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from app import BASE_FIGURE

def build_candlestick(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure(BASE_FIGURE)
    if df.empty:
        return fig
    fig.add_trace(
        go.Candlestick(
            x=df["ts"],
            open=df["open"], high=df["high"], low=df["low"], close=df["close"],
            increasing_line_color="#26A69A", decreasing_line_color="#EF5350",
            name="OHLCV",
        ),
        row=1, col=1,
    )
    return fig
```

### Volume level binning

Price levels are integer-bucketed by rounding to 2 significant figures of the price range:
```python
price_range = df["high"].max() - df["low"].min()
bucket_size = max(price_range / 100, 0.01)  # ~100 buckets
df["price_level"] = (df["close"] / bucket_size).round() * bucket_size
level_vol = df.groupby("price_level")["volume"].sum()
poc = level_vol.idxmax()
```

Value Area: sort levels by volume descending, cumsum until ≥ 70% of total. The min/max of those levels is `value_area_low` / `value_area_high`.

Add a single horizontal bar trace per significant level (top 20 by volume to avoid chart overcrowding):
```python
top_levels = level_vol.nlargest(20)
for price, vol in top_levels.items():
    color = "#FFD700" if price == poc else "rgba(100,100,200,0.3)"
    fig.add_hline(y=price, line_color=color, opacity=0.4, row=1, col=1)
```

Note: Horizontal bars (`go.Bar` orientation='h') on the candlestick subplot require x=volume, y=price. Use `add_trace` not `add_hline`.

### Volume bubbles

```python
valid = df.dropna(subset=["buy_volume", "volume"])
valid = valid[valid["volume"] > 0]
mid_price = (valid["high"] + valid["low"]) / 2
marker_size = (valid["volume"] / valid["volume"].max() * 30).clip(lower=4)
colors = ["#26A69A" if b/v > 0.5 else "#EF5350" for b, v in zip(valid["buy_volume"], valid["volume"])]
fig.add_trace(
    go.Scatter(
        x=valid["ts"], y=mid_price,
        mode="markers",
        marker=dict(size=marker_size, color=colors, opacity=0.6),
        name="Vol Bubbles",
        showlegend=False,
    ),
    row=1, col=1,
)
```

### DataFrame conversion from candle-store

`candle-store` is a `list[dict]`. Convert in the callback:
```python
import pandas as pd
df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
```

Column names from QuestDB match field names: `ts`, `open`, `high`, `low`, `close`, `volume`, `buy_volume`, etc.

### Callback wiring

```python
@callback(
    Output("candlestick-graph", "figure"),
    Input("candle-store", "data"),
)
def update_candlestick(candle_rows):
    df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
    from charts import build_candlestick, add_volume_levels, add_volume_bubbles
    fig = build_candlestick(df)
    if not df.empty:
        fig = add_volume_levels(fig, df)
        fig = add_volume_bubbles(fig, df)
    return fig
```

Import `charts` inside the callback to avoid circular imports at module load time.

### layout.py update

Replace:
```python
dcc.Loading(
    dbc.Card("Candlestick", body=True, style={**_PLACEHOLDER_STYLE, "height": "420px"}),
    id="loading-candlestick",
),
```
With:
```python
dcc.Loading(
    dcc.Graph(id="candlestick-graph", figure={}, style={"height": "420px"}),
    id="loading-candlestick",
),
```

### Dockerfile COPY — add charts.py

Update:
```
COPY app.py layout.py callbacks.py data.py charts.py ./
```

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

### File List

- `dashboard/charts.py` — NEW (build_candlestick, add_volume_levels, add_volume_bubbles)
- `dashboard/callbacks.py` — UPDATED (update_candlestick, absorption_overlay stub)
- `dashboard/layout.py` — UPDATED (candlestick-graph dcc.Graph replaces placeholder)
- `dashboard/Dockerfile` — UPDATED (charts.py added to COPY)

### Review Findings

- [x] [Review][Patch] P1: `go.Figure(BASE_FIGURE)` shallow copy — traces accumulate on BASE_FIGURE singleton across callbacks. Fixed: `copy.deepcopy(BASE_FIGURE)` via `_base_fig()` helper [`charts.py`] — applied
- [x] [Review][Patch] P2: `go.Bar` with numeric x-values on timestamp x-axis causes invisible/nonsense positions. Fixed: replaced with `add_shape(type='rect', xref='x domain', yref='y')` — subplot-relative horizontal bars that don't conflict with timestamp axis [`charts.py`] — applied
- [x] [Review][Defer] `import charts` inside callback hides import errors — moved to module-level import in `callbacks.py` (applied eagerly)
- [x] [Review][Defer] `absorption_overlay` signature inconsistent with activation pattern — changed to `(fig, df) -> fig` convention matching add_volume_* functions (applied eagerly)
