"""Data tab layout — regime timeline and microprice deviation charts."""
import dash_bootstrap_components as dbc
from dash import dcc, html

from layout import SYMBOL_OPTIONS  # reuse existing symbol list

REGIME_COLORS = {
    "TRENDING_UP":   "#4CAF50",
    "TRENDING_DOWN": "#F44336",
    "HIGH_VOL":      "#FF9800",
    "THIN_BOOK":     "#FFC107",
    "RANGING":       "#9E9E9E",
}

data_tab_layout = dbc.Row(
    [
        dbc.Col(
            [
                dbc.Row(
                    [
                        dbc.Col(
                            dcc.Dropdown(
                                id="ml-symbol-dropdown",
                                options=SYMBOL_OPTIONS,
                                placeholder="Select symbol...",
                                style={"backgroundColor": "#333", "color": "#ccc"},
                            ),
                            width=4,
                        ),
                    ],
                    className="mb-3",
                ),
                dcc.Graph(id="regime-timeline-graph", style={"height": "200px"}),
                dcc.Graph(id="microprice-deviation-graph", style={"height": "300px"}),
            ]
        )
    ]
)
