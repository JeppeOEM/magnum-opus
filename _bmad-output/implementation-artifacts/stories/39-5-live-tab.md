---
id: 39-5
title: Live tab — signal feed and deviation gauge
epic: 39
status: ready-for-dev
---

# Story 39-5: Live Tab — Signal Feed and Deviation Gauge

## Context

The Live tab is the trader-facing view: real-time signal table from `ai:{symbol}:signals`, a microprice deviation gauge, and the BTC/ETH spread z-score chart. Refreshes every 1s.

## What to build

### `dashboard/callbacks_ml.py` — live tab additions

```python
import redis

_REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")

SIGNAL_COLORS = {
    "REVERT": "rgba(76,175,80,0.2)",
    "TREND":  "rgba(244,67,54,0.2)",
    "FLAT":   "rgba(0,0,0,0)",
}


def _fetch_signals(symbol: str, count: int = 20) -> list[dict]:
    r = redis.from_url(_REDIS_URL, decode_responses=True)
    try:
        entries = r.xrevrange(f"ai:{symbol}:signals", count=count)
        rows = []
        for _id, fields in entries:
            rows.append({
                "Time":       fields.get("ts", "")[:19],
                "Signal":     fields.get("signal", ""),
                "Confidence": f"{float(fields.get('confidence', 0)):.1%}",
                "Regime":     fields.get("regime", ""),
                "Cluster":    fields.get("cluster", ""),
                "Dev bps":    fields.get("deviation_bps", ""),
                "Model ID":   (fields.get("model_id") or "")[:8],
                "Latency ms": fields.get("latency_ms", ""),
            })
        return rows
    finally:
        r.close()


def _fetch_spread_signals(count: int = 60) -> list[dict]:
    r = redis.from_url(_REDIS_URL, decode_responses=True)
    try:
        return [
            {"ts": _id, **fields}
            for _id, fields in r.xrevrange("ai:BTC-ETH-spread:signals", count=count)
        ]
    finally:
        r.close()


@callback(
    Output("live-signals-table", "data"),
    Output("live-signals-table", "style_data_conditional"),
    Output("deviation-gauge", "figure"),
    Output("spread-zscore-graph", "figure"),
    Input("ml-live-interval", "n_intervals"),
    Input("ml-symbol-dropdown", "value"),
)
def update_live_tab(n, selected):
    empty_gauge = go.Figure(go.Indicator(
        mode="gauge+number", value=0,
        gauge={"axis": {"range": [-5, 5]}, "bar": {"color": "#00BCD4"}},
    ), layout=go.Layout(**_DARK, height=200))
    empty_spread = go.Figure(layout=go.Layout(**_DARK, height=200))

    if not selected:
        return [], [], empty_gauge, empty_spread

    _, symbol = selected.split(":", 1)

    # Signal table
    rows = _fetch_signals(symbol)
    style_cond = [
        {"if": {"filter_query": f'{{Signal}} = "{sig}"', "column_id": "Signal"},
         "backgroundColor": color}
        for sig, color in SIGNAL_COLORS.items()
    ]

    # Deviation gauge
    dev_bps = 0.0
    if rows:
        try:
            dev_bps = float(rows[0]["Dev bps"])
        except (ValueError, KeyError):
            pass
    gauge_color = "#4CAF50" if dev_bps > 0 else "#F44336" if dev_bps < 0 else "#9E9E9E"
    gauge_fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=round(dev_bps, 3),
        title={"text": "Microprice Deviation (bps)", "font": {"color": "#CCC"}},
        gauge={
            "axis": {"range": [-5, 5], "tickcolor": "#CCC"},
            "bar": {"color": gauge_color},
            "bgcolor": "#333",
            "threshold": {"line": {"color": "#FFF", "width": 2}, "value": 0},
        },
        number={"font": {"color": "#CCC"}},
    ), layout=go.Layout(**_DARK, height=250))

    # Spread z-score chart
    spread_rows = _fetch_spread_signals()
    if spread_rows:
        zscores = [float(r.get("zscore", 0)) for r in reversed(spread_rows)]
        spread_fig = go.Figure(layout=go.Layout(
            title="BTC/ETH Spread Z-Score",
            **_DARK, height=200,
        ))
        spread_fig.add_trace(go.Scatter(
            y=zscores, mode="lines",
            line=dict(color="#FF9800", width=1.5),
            name="z-score",
        ))
        spread_fig.add_hline(y=1.5,  line_dash="dash", line_color="#4CAF5066")
        spread_fig.add_hline(y=-1.5, line_dash="dash", line_color="#F4433666")
        spread_fig.add_hline(y=0,    line_dash="dot",  line_color="#555")
    else:
        spread_fig = empty_spread

    return rows, style_cond, gauge_fig, spread_fig
```

### Layout

```python
from dash import dash_table

live_tab_layout = dbc.Row([
    dbc.Col([
        dcc.Graph(id="deviation-gauge", style={"height": "250px"}),
        dcc.Graph(id="spread-zscore-graph", style={"height": "200px"}),
    ], width=4),
    dbc.Col([
        html.H6("Recent Signals"),
        dash_table.DataTable(
            id="live-signals-table",
            columns=[{"name": c, "id": c} for c in
                     ["Time", "Signal", "Confidence", "Regime", "Cluster",
                      "Dev bps", "Model ID", "Latency ms"]],
            style_table={"overflowX": "auto"},
            style_cell={"backgroundColor": "#2a2a2a", "color": "#CCC",
                        "border": "1px solid #444", "fontSize": "12px"},
            style_header={"backgroundColor": "#444", "fontWeight": "bold"},
            page_size=20,
        ),
    ], width=8),
])
```

## Acceptance Criteria

1. Signal table shows last 20 entries from `ai:{symbol}:signals` with columns: Time, Signal, Confidence, Regime, Cluster, Dev bps, Model ID, Latency ms.
2. REVERT rows: green background. TREND rows: red background. FLAT: transparent.
3. Microprice deviation gauge: range ±5 bps, green when positive, red when negative.
4. BTC/ETH spread z-score chart: last 60 points with ±1.5σ reference lines.
5. "Inference loop not running" message when Redis stream is empty.
6. 1s refresh interval.

## Dev Notes

- Uses sync `redis` client in callback (Dash callbacks are sync by default).
- `redis.from_url` creates a new connection per callback — acceptable for 1s interval; connection pool can be added if performance becomes an issue.
- `ai:BTC-ETH-spread:signals` stream key is fixed — no dropdown needed.
- Add `REDIS_URL` to dashboard's `docker-compose.yml` environment block.
