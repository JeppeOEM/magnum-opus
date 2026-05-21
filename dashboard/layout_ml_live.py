"""Live tab layout — signal feed, deviation gauge, and spread z-score chart."""
import dash_bootstrap_components as dbc
from dash import dash_table, dcc, html

_SIGNAL_COLUMNS = [
    "Time", "Signal", "Confidence", "Regime",
    "Cluster", "Dev bps", "Model ID", "Latency ms",
]

live_tab_layout = dbc.Row(
    [
        dbc.Col(
            [
                dcc.Graph(id="deviation-gauge",    style={"height": "250px"}),
                dcc.Graph(id="spread-zscore-graph", style={"height": "200px"}),
            ],
            width=4,
        ),
        dbc.Col(
            [
                html.H6("Recent Signals"),
                dash_table.DataTable(
                    id="live-signals-table",
                    columns=[{"name": c, "id": c} for c in _SIGNAL_COLUMNS],
                    style_table={"overflowX": "auto"},
                    style_cell={
                        "backgroundColor": "#2a2a2a",
                        "color": "#CCC",
                        "border": "1px solid #444",
                        "fontSize": "12px",
                        "padding": "4px 8px",
                    },
                    style_header={
                        "backgroundColor": "#444",
                        "fontWeight": "bold",
                        "color": "#EEE",
                    },
                    page_size=20,
                ),
            ],
            width=8,
        ),
    ]
)
