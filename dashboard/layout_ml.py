"""ML Analytics page layout — 4-tab scaffold.

Tabs:
  data      — feature store health and data quality
  clusters  — HDBSCAN cluster visualisation per regime
  models    — model registry and SHAP importance
  live      — live signals from ai:{symbol}:signals streams
"""
import dash_bootstrap_components as dbc
from dash import dcc, html

ml_page_layout = dbc.Container(
    [
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
        # Refresh intervals — active on all tabs
        dcc.Interval(id="ml-slow-interval",  interval=30_000, n_intervals=0),   # 30s
        dcc.Interval(id="ml-live-interval",  interval=1_000,  n_intervals=0),   # 1s
        dcc.Interval(id="ml-model-interval", interval=60_000, n_intervals=0),   # 60s
    ],
    fluid=True,
)
