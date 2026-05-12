# Story 18.6: Liquidity Overlay + Full Layout Assembly

Status: done

## Story

As a developer,
I want the complete dashboard layout assembled with all panels at correct proportions and the liquidity overlay stub in place,
so that the dashboard matches the agreed design end-to-end and is ready for frontend retirement.

**Pre-conditions:** All of stories 18.1–18.5 complete and passing.

## Acceptance Criteria

**AC 1 — Full layout at correct proportions, no scroll**
- Layout structure: left column (Bootstrap `width=6`) contains two stacked sub-rows — top: candlestick (`width=10`) + vol-profile (`width=2`); bottom: cvd (`width=6`) + bidask (`width=6`). Right column (Bootstrap `width=6`) contains heatmap at height=700px spanning both sub-rows.
- All five panels visible simultaneously without scrolling on a 1920×1080 display (total page height ≤ ~950px)
- Heights: candlestick=420px, vol-profile=420px, heatmap=700px (matches candlestick+cvd combined), cvd=280px, bidask=280px

**AC 2 — Liquidity overlay stub**
- A helper function `liquidity_overlay(fig, df)` exists in `callbacks.py` (same pattern as `absorption_overlay`)
- Returns `fig` unchanged when `poc_price`, `value_area_high`, `value_area_low` are absent from `df.columns` (Epic 18: fields never present)
- Called inside `update_candlestick` callback on non-empty df (activates automatically in Epic 20 when fields appear)
- Stub comment documents intended Epic 20 implementation: `add_hrect` for Value Area band + `go.Scatter` for POC horizontal line at `row=1, col=1`

**AC 3 — HTTP 200 and no errors**
- `curl localhost:8050` returns 200
- No import errors or crash in container logs

**AC 4 — Vite frontend untouched**
- `frontend/` directory still present (retirement is story 18.7)

## Tasks / Subtasks

- [x] Task 1: Restructure `dashboard/layout.py` for proper heatmap-spans-both-rows layout
  - [x] 1.1 Wrap left side (candlestick+vol-profile + cvd+bidask) in a `dbc.Col(width=6)` with two stacked `dbc.Row` children
  - [x] 1.2 Move heatmap to a right `dbc.Col(width=6)` at height=700px
  - [x] 1.3 Adjust candlestick/vol-profile inner row: candlestick `width=10`, vol-profile `width=2` (within left width=6 column)
  - [x] 1.4 Verify cvd/bidask inner row stays `width=6` + `width=6` within the left column
  - [x] 1.5 Remove now-unused `_PLACEHOLDER_STYLE` variable if nothing else references it

- [x] Task 2: Add `liquidity_overlay` stub to `dashboard/callbacks.py`
  - [x] 2.1 Add `liquidity_overlay(fig, df)` helper function following `absorption_overlay` pattern
  - [x] 2.2 Wire call into `update_candlestick`: `fig = liquidity_overlay(fig, df)` after existing overlays on non-empty df

- [x] Task 3: Verify
  - [x] 3.1 Container starts with 200 response
  - [x] 3.2 No import errors or crash in logs
  - [x] 3.3 `frontend/` directory exists unchanged

## Dev Notes

### Layout Restructure — the critical change

Stories 18-1 through 18-5 assembled panels in two flat Bootstrap rows:
```
Row 1: Col(candlestick, w=5) + Col(vol-profile, w=2) + Col(heatmap, w=5, h=720)
Row 2: Col(cvd, w=6) + Col(bidask, w=6)
```
This makes Bootstrap Row 1 height = 720px (driven by heatmap), total page ≈ 1070px — scrolls at 1080p.

Story 18.6 restructures to a 2-column outer layout:
```
Outer Row:
  Left Col (w=6):
    Inner Row 1: Col(candlestick, w=10, h=420) + Col(vol-profile, w=2, h=420)
    Inner Row 2: Col(cvd, w=6, h=280) + Col(bidask, w=6, h=280)
  Right Col (w=6):
    heatmap (h=700)   ← 420+280 = 700px matches left side total
```

Total page height: 40 (dropdown) + 700 (chart area) + ~30 (padding/margins) = ~770px. Fits on 1080p.

Width proportions with outer width=6 / inner width=10+2:
- Candlestick: 6/12 × 10/12 = 41.7% (close to target 38%)
- Vol profile: 6/12 × 2/12 = 8.3% (close to target 12%)
- Heatmap: 6/12 = 50% ✓

The Plotly BASE_FIGURE column_widths=[0.38, 0.12, 0.50] handles exact internal proportions — Bootstrap just needs to give each graph sufficient space.

### New layout.py structure

```python
import dash_bootstrap_components as dbc
from dash import dcc

SYMBOL_OPTIONS = [
    {"label": "BTC-USDT (KuCoin)", "value": "kucoin:BTC-USDT"},
    {"label": "ETH-USDT (KuCoin)", "value": "kucoin:ETH-USDT"},
    {"label": "BTC-USDT (Bybit)", "value": "bybit:BTC-USDT"},
]

layout = dbc.Container(
    [
        dbc.Row(
            dbc.Col(
                dcc.Dropdown(
                    id="symbol-dropdown",
                    options=SYMBOL_OPTIONS,
                    value="kucoin:BTC-USDT",
                    clearable=False,
                    style={"marginBottom": "8px"},
                )
            )
        ),
        dbc.Row(
            [
                # Left column: top panels + bottom panels stacked
                dbc.Col(
                    [
                        dbc.Row(
                            [
                                dbc.Col(
                                    dcc.Loading(
                                        dcc.Graph(id="candlestick-graph", figure={}, style={"height": "420px"}),
                                        id="loading-candlestick",
                                    ),
                                    width=10,
                                ),
                                dbc.Col(
                                    dcc.Loading(
                                        dcc.Graph(id="vol-profile-graph", figure={}, style={"height": "420px"}),
                                        id="loading-vol-profile",
                                    ),
                                    width=2,
                                ),
                            ],
                        ),
                        dbc.Row(
                            [
                                dbc.Col(
                                    dcc.Loading(
                                        dcc.Graph(id="cvd-graph", figure={}, style={"height": "280px"}),
                                        id="loading-cvd",
                                    ),
                                    width=6,
                                ),
                                dbc.Col(
                                    dcc.Loading(
                                        dcc.Graph(id="bidask-graph", figure={}, style={"height": "280px"}),
                                        id="loading-bidask",
                                    ),
                                    width=6,
                                ),
                            ],
                        ),
                    ],
                    width=6,
                ),
                # Right column: heatmap spanning full height of both left sub-rows
                dbc.Col(
                    dcc.Loading(
                        dcc.Graph(id="heatmap-graph", figure={}, style={"height": "700px"}),
                        id="loading-heatmaps",
                    ),
                    width=6,
                ),
            ],
        ),
        # dcc.Store components — data layer, invisible
        dcc.Store(id="candle-store", data=[]),
        dcc.Store(id="last-ts", data=None),
        dcc.Store(id="ob-store", data=[]),
        dcc.Store(id="ob-cursor", data="0"),
        dcc.Store(id="ob-cursor-symbol", data=""),
        dcc.Interval(id="live-interval", interval=1000, n_intervals=0),
    ],
    fluid=True,
    style={"padding": "12px"},
)
```

Note: `_PLACEHOLDER_STYLE` can be removed — no placeholder Cards remain.

### liquidity_overlay stub

Follow the `absorption_overlay` pattern already in `callbacks.py` (line 87). Add as a helper function (not a Dash callback), and call it from `update_candlestick`:

```python
def liquidity_overlay(fig: go.Figure, df: "pd.DataFrame") -> go.Figure:
    # Stub: renders nothing until poc_price/value_area_high/value_area_low
    # are available from Epic 20 candle service output.
    # Activation: if fields present, add:
    #   fig.add_hrect(y0=va_low, y1=va_high, fillcolor="rgba(255,200,0,0.08)", row=1, col=1)
    #   fig.add_hline(y=poc_price, line_color="#FFD700", line_dash="dot", row=1, col=1)
    if not all(c in df.columns for c in ["poc_price", "value_area_high", "value_area_low"]):
        return fig
    # (Epic 20 implementation activates here when fields arrive)
    return fig
```

Update `update_candlestick`:
```python
@callback(
    Output("candlestick-graph", "figure"),
    Input("candle-store", "data"),
)
def update_candlestick(candle_rows):
    df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
    fig = charts.build_candlestick(df)
    if not df.empty:
        fig = charts.add_volume_levels(fig, df)
        fig = charts.add_volume_bubbles(fig, df)
        fig = liquidity_overlay(fig, df)
    return fig
```

### Files to touch

- `dashboard/layout.py` — full rewrite with nested layout (keep all graph IDs and store IDs identical)
- `dashboard/callbacks.py` — add `liquidity_overlay` helper + wire into `update_candlestick`

### Files NOT to touch

- `dashboard/app.py` — BASE_FIGURE unchanged
- `dashboard/charts.py` — no chart function changes
- `dashboard/data.py` — no data changes
- `frontend/` — must remain untouched (retirement is story 18.7)

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- layout.py full rewrite: nested 2-column outer Bootstrap row (left w=6, right w=6); left col has inner row 1 (candlestick w=10 h=420px + vol-profile w=2 h=420px) and inner row 2 (cvd w=6 h=280px + bidask w=6 h=280px); right col has heatmap h=700px; _PLACEHOLDER_STYLE removed
- callbacks.py: liquidity_overlay(fig, df) stub added following absorption_overlay pattern; returns fig unchanged when poc_price/value_area_high/value_area_low absent; wired into update_candlestick after existing overlays on non-empty df
- Container 200 response, no import errors, no crashes; frontend/ directory untouched

### File List

- dashboard/layout.py
- dashboard/callbacks.py

### Review Findings

- [x] [Review][Patch] `liquidity_overlay` stub missing note that `add_volume_levels` already renders VA band and POC — when Epic 20 activates, dual rendering will occur [dashboard/callbacks.py]
- [x] [Review][Defer] No tests for dashboard — pre-existing condition across all Epic 18 [dashboard/]
