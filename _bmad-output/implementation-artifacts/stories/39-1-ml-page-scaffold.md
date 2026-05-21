---
id: 39-1
title: /ml page routing and 4-tab scaffold
epic: 39
status: ready-for-dev
---

# Story 39-1: /ml Page Routing and 4-Tab Scaffold

## Context

Adds the `/ml` page to the existing Dash dashboard with 4-tab navigation. All ML views will live here. This story wires up the routing and tab skeleton — chart content in subsequent stories.

## What to build

### `dashboard/layout_ml.py`

```python
import dash_bootstrap_components as dbc
from dash import dcc, html

ml_page_layout = dbc.Container([
    html.H4("ML Analytics", className="mt-3 mb-3"),
    dcc.Tabs(
        id="ml-tabs",
        value="data",
        children=[
            dcc.Tab(label="Data",     value="data",     className="dark-tab"),
            dcc.Tab(label="Clusters", value="clusters", className="dark-tab"),
            dcc.Tab(label="Models",   value="models",   className="dark-tab"),
            dcc.Tab(label="Live",     value="live",     className="dark-tab"),
        ],
        colors={"border": "#444", "primary": "#00bcd4", "background": "#333"},
    ),
    html.Div(id="ml-tab-content", className="mt-3"),
    # Refresh intervals
    dcc.Interval(id="ml-slow-interval",  interval=30_000, n_intervals=0),  # 30s
    dcc.Interval(id="ml-live-interval",  interval=1_000,  n_intervals=0),  # 1s
    dcc.Interval(id="ml-model-interval", interval=60_000, n_intervals=0),  # 60s
], fluid=True)
```

### `dashboard/layout.py` — add ML NavLink

Find the existing nav bar and add:

```python
dbc.NavLink("ML", href="/ml", active="exact"),
```

alongside the existing Charts / Bots / Backtests links.

### `dashboard/callbacks.py` — add /ml routing

In `render_page`:

```python
from layout_ml import ml_page_layout

def render_page(pathname):
    if pathname == "/bots":
        return bot_page_layout
    if pathname == "/backtests":
        return backtest_page_layout
    if pathname == "/ml":
        return ml_page_layout
    return _charts_page
```

### `dashboard/app.py` — import layout_ml

Add near the other layout imports:

```python
import layout_ml  # registers component IDs
```

### `dashboard/callbacks_ml.py` — tab content router

```python
from dash import Input, Output, callback, no_update

@callback(
    Output("ml-tab-content", "children"),
    Input("ml-tabs", "value"),
)
def render_ml_tab(tab):
    if tab == "data":
        from layout_ml_data import data_tab_layout
        return data_tab_layout
    if tab == "clusters":
        return html.Div("Clusters tab — coming in 39-3")
    if tab == "models":
        return html.Div("Models tab — coming in 39-4")
    if tab == "live":
        return html.Div("Live tab — coming in 39-5")
    return no_update
```

## Acceptance Criteria

1. Navigating to `/ml` renders `ml_page_layout` (4 dcc.Tabs visible).
2. Tab labels: "Data", "Clusters", "Models", "Live".
3. "ML" NavLink appears in the nav bar alongside Charts / Bots / Backtests.
4. `layout_ml` imported in `app.py` so component IDs register at startup.
5. Three `dcc.Interval` components present: 30s (slow), 1s (live), 60s (model refresh).
6. Clicking each tab renders placeholder content without errors.

## Dev Notes

- `callbacks_ml.py` must be imported in `app.py` for its callbacks to register.
- Tab content rendered via callback (not static layout) — keeps initial page load fast.
- Dark theme consistent with existing pages: `#222222` background, `#CCCCCC` font.
