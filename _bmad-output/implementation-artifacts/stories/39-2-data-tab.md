---
id: 39-2
title: Data tab — regime timeline and microprice deviation
epic: 39
status: ready-for-dev
---

# Story 39-2: Data Tab — Regime Timeline and Microprice Deviation

## Context

The Data tab gives a single-glance view of market conditions: what regime is the market in, and is the microprice deviation signal active? Both charts refresh every 30s from QuestDB.

## What to build

### `dashboard/layout_ml_data.py`

```python
from dash import dcc, html
import dash_bootstrap_components as dbc

REGIME_COLORS = {
    "TRENDING_UP":   "#4CAF50",
    "TRENDING_DOWN": "#F44336",
    "HIGH_VOL":      "#FF9800",
    "THIN_BOOK":     "#FFC107",
    "RANGING":       "#9E9E9E",
}

data_tab_layout = dbc.Row([
    dbc.Col([
        dbc.Row([
            dbc.Col(dcc.Dropdown(
                id="ml-symbol-dropdown",
                placeholder="Select symbol...",
                style={"backgroundColor": "#333", "color": "#ccc"},
            ), width=4),
        ], className="mb-3"),
        dcc.Graph(id="regime-timeline-graph", style={"height": "200px"}),
        dcc.Graph(id="microprice-deviation-graph", style={"height": "300px"}),
    ])
])
```

### `dashboard/callbacks_ml.py` — Data tab callbacks

```python
import os
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, no_update
import data as dashboard_data

_QUESTDB_URL = os.environ.get("QUESTDB_HTTP_ADDR", "http://questdb:9000")
_DARK = dict(paper_bgcolor="#222222", plot_bgcolor="#222222", font_color="#CCCCCC")

REGIME_COLORS = {
    "TRENDING_UP": "#4CAF50", "TRENDING_DOWN": "#F44336",
    "HIGH_VOL": "#FF9800", "THIN_BOOK": "#FFC107", "RANGING": "#9E9E9E",
}


@callback(
    Output("ml-symbol-dropdown", "options"),
    Input("ml-slow-interval", "n_intervals"),
)
def update_ml_symbol_options(n):
    # Reuse existing symbol fetching logic
    try:
        symbols = dashboard_data.fetch_symbols(_QUESTDB_URL)
        return [{"label": s, "value": s} for s in symbols]
    except Exception:
        return []


@callback(
    Output("regime-timeline-graph", "figure"),
    Output("microprice-deviation-graph", "figure"),
    Input("ml-slow-interval", "n_intervals"),
    Input("ml-symbol-dropdown", "value"),
)
def update_data_tab(n, selected):
    if not selected:
        empty = go.Figure(layout=go.Layout(**_DARK))
        return empty, empty

    exchange, symbol = selected.split(":", 1)
    sql = (
        f"SELECT ts, microprice_mid_delta, hawkes_intensity "
        f"FROM snapshot_1s "
        f"WHERE exchange='{exchange}' AND symbol='{symbol}' "
        f"AND ts > dateadd('h', -24, now()) "
        f"ORDER BY ts"
    )
    try:
        import httpx
        resp = httpx.get(_QUESTDB_URL + "/exec", params={"query": sql}, timeout=10)
        data = resp.json()
        cols = [c["name"] for c in data["columns"]]
        df = pd.DataFrame(data["dataset"], columns=cols)
        df["ts"] = pd.to_datetime(df["ts"])
    except Exception:
        empty = go.Figure(layout=go.Layout(**_DARK))
        return empty, empty

    # Regime timeline — fetch current regime from Redis via QuestDB-HTTP proxy
    # For simplicity, use a placeholder bar chart with regime annotation
    regime_fig = go.Figure(layout=go.Layout(
        title="Current Regime (from regime:{exchange}:{symbol})",
        **_DARK,
        height=200,
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=False, showticklabels=False),
    ))
    # Microprice deviation chart
    if not df.empty and "microprice_mid_delta" in df.columns:
        mp_vals = pd.to_numeric(df["microprice_mid_delta"], errors="coerce")
        dev_fig = go.Figure(layout=go.Layout(
            title="Microprice Deviation from Mid (bps)",
            **_DARK,
            height=300,
        ))
        dev_fig.add_trace(go.Scatter(
            x=df["ts"], y=mp_vals,
            mode="lines", line=dict(color="#00BCD4", width=1),
            name="microprice_mid_delta",
        ))
        dev_fig.add_hline(y=0, line_dash="dot", line_color="#888")
        dev_fig.add_hline(y=1.0, line_dash="dash", line_color="#4CAF5066", annotation_text="+1 bps")
        dev_fig.add_hline(y=-1.0, line_dash="dash", line_color="#F4433666", annotation_text="-1 bps")
    else:
        dev_fig = go.Figure(layout=go.Layout(**_DARK))

    return regime_fig, dev_fig
```

## Acceptance Criteria

1. Data tab renders symbol dropdown, regime timeline chart, and microprice deviation chart.
2. Symbol dropdown options populated from QuestDB (same symbols as Charts page).
3. Microprice deviation chart: line chart of `microprice_mid_delta` over last 24 hours with ±1 bps reference lines.
4. Both charts use `_DARK` theme (`paper_bgcolor="#222222"`).
5. Charts refresh when 30s interval fires or symbol changes.
6. When no data available, empty charts rendered (no crash).

## Dev Notes

- The full regime timeline (coloured bands by regime over time) requires storing regime history — the Redis SET only has current regime. A future enhancement can store regime to QuestDB. For now, show a static indicator with the current regime fetched from ml-service API.
- `dashboard_data.fetch_symbols()` should already exist from the Charts page — reuse it.
