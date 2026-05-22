"""Layout for the /chart page — backtest trade viewer with full chart context."""
from __future__ import annotations

import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import dcc, html

_DARK = dict(
    paper_bgcolor="#222222",
    plot_bgcolor="#222222",
    font_color="#CCCCCC",
)

_EMPTY_CANDLE = go.Figure()
_EMPTY_CANDLE.update_layout(
    **_DARK,
    margin={"l": 40, "r": 10, "t": 30, "b": 30},
    xaxis_rangeslider_visible=False,
    title="Select a trade from the Backtests page to load chart",
)

_label_style = {"color": "#888", "fontSize": "11px"}

chart_page_layout = dbc.Container(
    [
        # ── Header: back link + context info ──────────────────────────────────
        dbc.Row(
            [
                dbc.Col(
                    dcc.Link(
                        "← Backtests",
                        href="/backtests",
                        style={"color": "#80cbc4", "fontSize": "13px", "textDecoration": "none"},
                    ),
                    width="auto",
                ),
                dbc.Col(
                    html.Div(
                        id="chart-context-info",
                        style={"color": "#aaa", "fontSize": "12px", "lineHeight": "28px"},
                    ),
                ),
            ],
            className="mb-2",
            align="center",
        ),

        # ── Selected trade summary card ────────────────────────────────────────
        dbc.Row(
            dbc.Col(
                dbc.Card(
                    dbc.CardBody(
                        html.Div(id="chart-trade-info", style={"fontSize": "12px"}),
                    ),
                    style={"minHeight": "48px"},
                    className="mb-2",
                ),
                width=12,
            ),
        ),

        # ── Main candlestick chart ────────────────────────────────────────────
        dbc.Row(
            dbc.Col(
                dcc.Graph(
                    id="chart-candlestick",
                    figure=_EMPTY_CANDLE,
                    style={"height": "460px"},
                    config={
                        "displayModeBar": True,
                        "scrollZoom": True,
                        "modeBarButtonsToRemove": ["select2d", "lasso2d"],
                    },
                ),
                width=12,
            ),
        ),

        # ── CVD + Bid/Ask panels ───────────────────────────────────────────────
        dbc.Row(
            [
                dbc.Col(
                    dcc.Graph(id="chart-cvd", figure={}, style={"height": "200px"}),
                    width=6,
                ),
                dbc.Col(
                    dcc.Graph(id="chart-bidask", figure={}, style={"height": "200px"}),
                    width=6,
                ),
            ],
            className="mt-1",
        ),

        # ── Trade list (all trades in this run) ───────────────────────────────
        dbc.Row(
            [
                dbc.Col(
                    html.H6(
                        "All Trades in Run",
                        style={"color": "#888", "fontSize": "12px", "marginTop": "16px"},
                    ),
                    width=12,
                ),
                dbc.Col(
                    html.Div(id="chart-all-trades"),
                    width=12,
                ),
            ],
        ),

        # ── Hidden stores ─────────────────────────────────────────────────────
        dcc.Store(id="chart-candle-store", data=[]),
        dcc.Store(id="chart-trades-store", data=[]),
        dcc.Store(id="chart-selected-idx", data=None),
    ],
    fluid=True,
    style={"padding": "12px"},
)
