"""Clusters tab layout — PCA scatter and SHAP feature importances."""
import dash_bootstrap_components as dbc
from dash import dcc, html

clusters_tab_layout = dbc.Row(
    [
        dbc.Col(
            [
                dbc.Row(
                    [
                        dbc.Col(
                            dcc.Dropdown(
                                id="cluster-regime-dropdown",
                                placeholder="Select symbol:regime...",
                                style={"backgroundColor": "#333", "color": "#ccc"},
                            ),
                            width=4,
                        ),
                    ],
                    className="mb-3",
                ),
                dcc.Graph(id="pca-scatter-graph",     style={"height": "350px"}),
                dcc.Graph(id="shap-importance-graph", style={"height": "400px"}),
            ]
        )
    ]
)
