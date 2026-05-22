"""Layout for the /backtests page."""
from __future__ import annotations

import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import dcc, html

_TF_OPTIONS = [
    {"label": "1s",  "value": "1s"},
    {"label": "1m",  "value": "1m"},
    {"label": "5m",  "value": "5m"},
    {"label": "15m", "value": "15m"},
    {"label": "1h",  "value": "1h"},
    {"label": "4h",  "value": "4h"},
    {"label": "1d",  "value": "1d"},
]

_EXCHANGE_OPTIONS = [
    {"label": "Bybit",  "value": "bybit"},
    {"label": "KuCoin", "value": "kucoin"},
]

_DATE_MODE_OPTIONS = [
    {"label": "First candle", "value": "first"},
    {"label": "Custom",       "value": "custom"},
]

_DATE_MODE_END_OPTIONS = [
    {"label": "Last candle", "value": "last"},
    {"label": "Custom",      "value": "custom"},
]

_EMPTY_EQUITY_FIG = go.Figure()
_EMPTY_EQUITY_FIG.update_layout(
    template="plotly_dark",
    title="Equity Curve",
    margin={"l": 40, "r": 10, "t": 30, "b": 30},
    xaxis_title=None,
    yaxis_title="Value (USD)",
)

_label_style = {"color": "#888", "fontSize": "11px", "marginBottom": "2px"}

backtest_page_layout = dbc.Container(
    [
        # ── Row 1: strategy + exchange + symbol + tf + capital ────────────────
        dbc.Row(
            [
                dbc.Col(
                    [
                        html.Div("Strategy", style=_label_style),
                        dbc.InputGroup(
                            [
                                dcc.Dropdown(
                                    id="backtest-strategy-dd",
                                    placeholder="Select strategy…",
                                    clearable=False,
                                    style={"flex": "1", "minWidth": "0"},
                                ),
                                dbc.Button(
                                    "↺",
                                    id="backtest-refresh-strategies-btn",
                                    color="secondary",
                                    size="sm",
                                    title="Refresh strategies",
                                    style={"borderRadius": "0 4px 4px 0"},
                                ),
                            ],
                            style={"flexWrap": "nowrap"},
                        ),
                    ],
                    width=4,
                ),
                dbc.Col(
                    [
                        html.Div("Exchange", style=_label_style),
                        dcc.Dropdown(
                            id="backtest-exchange-dd",
                            options=_EXCHANGE_OPTIONS,
                            value="bybit",
                            clearable=False,
                        ),
                    ],
                    width=2,
                ),
                dbc.Col(
                    [
                        html.Div("Symbol", style=_label_style),
                        dcc.Dropdown(
                            id="backtest-symbol-dd",
                            placeholder="Select symbol…",
                            clearable=False,
                        ),
                    ],
                    width=2,
                ),
                dbc.Col(
                    [
                        html.Div("Timeframe", style=_label_style),
                        dcc.Dropdown(
                            id="backtest-tf-dd",
                            options=_TF_OPTIONS,
                            value="1s",
                            clearable=False,
                        ),
                    ],
                    width=2,
                ),
                dbc.Col(
                    [
                        html.Div("Capital (USD)", style=_label_style),
                        dbc.Input(
                            id="backtest-capital-inp",
                            placeholder="10000",
                            value="10000",
                            type="number",
                            debounce=True,
                        ),
                    ],
                    width=2,
                ),
            ],
            className="mb-2",
            align="end",
        ),

        # ── Row 2: date range controls ────────────────────────────────────────
        dbc.Row(
            [
                # Start date
                dbc.Col(
                    [
                        html.Div("Start", style=_label_style),
                        dbc.RadioItems(
                            id="backtest-start-mode",
                            options=_DATE_MODE_OPTIONS,
                            value="first",
                            inline=True,
                            inputClassName="me-1",
                            labelClassName="me-3",
                            style={"fontSize": "13px"},
                        ),
                        dbc.Collapse(
                            dbc.Input(
                                id="backtest-start-date",
                                placeholder="YYYY-MM-DD HH:MM:SS",
                                value="",
                                debounce=True,
                                size="sm",
                                style={"marginTop": "4px"},
                            ),
                            id="backtest-start-collapse",
                            is_open=False,
                        ),
                    ],
                    width=3,
                ),
                # End date
                dbc.Col(
                    [
                        html.Div("End", style=_label_style),
                        dbc.RadioItems(
                            id="backtest-end-mode",
                            options=_DATE_MODE_END_OPTIONS,
                            value="last",
                            inline=True,
                            inputClassName="me-1",
                            labelClassName="me-3",
                            style={"fontSize": "13px"},
                        ),
                        dbc.Collapse(
                            dbc.Input(
                                id="backtest-end-date",
                                placeholder="YYYY-MM-DD HH:MM:SS",
                                value="",
                                debounce=True,
                                size="sm",
                                style={"marginTop": "4px"},
                            ),
                            id="backtest-end-collapse",
                            is_open=False,
                        ),
                    ],
                    width=3,
                ),
                # Resolved date range info
                dbc.Col(
                    html.Div(
                        id="backtest-range-info",
                        style={"color": "#80cbc4", "fontSize": "12px", "marginTop": "18px"},
                    ),
                    width=6,
                ),
            ],
            className="mb-2",
        ),

        # ── Row 3: strategy status warning ────────────────────────────────────
        dbc.Row(
            dbc.Col(
                html.Div(
                    id="backtest-strategy-status",
                    style={"color": "#ff9800", "fontSize": "12px"},
                ),
                width=12,
            ),
            className="mb-1",
        ),

        # ── Row 4: run button + status ────────────────────────────────────────
        dbc.Row(
            [
                dbc.Col(
                    dbc.Button(
                        "▶ Run Backtest",
                        id="backtest-run-btn",
                        color="success",
                        size="sm",
                    ),
                    width="auto",
                ),
                dbc.Col(
                    html.Div(
                        id="backtest-status-div",
                        style={"color": "#aaa", "fontSize": "13px", "lineHeight": "30px"},
                    ),
                    width=10,
                ),
            ],
            className="mb-3",
            align="center",
        ),

        # ── Code + metrics ─────────────────────────────────────────────────────
        dbc.Row(
            [
                dbc.Col(
                    dbc.Card(
                        dbc.CardBody(
                            html.Pre(
                                id="backtest-code-view",
                                style={
                                    "fontSize": "11px",
                                    "maxHeight": "420px",
                                    "overflowY": "auto",
                                    "color": "#ccc",
                                    "background": "transparent",
                                    "margin": 0,
                                },
                            )
                        ),
                        style={"height": "450px", "overflowY": "auto"},
                    ),
                    width=6,
                ),
                dbc.Col(
                    [
                        dbc.Card(
                            dbc.CardBody(
                                html.Div(id="backtest-metrics-div"),
                            ),
                            className="mb-2",
                            style={"minHeight": "160px"},
                        ),
                        dcc.Graph(
                            id="backtest-equity-chart",
                            figure=_EMPTY_EQUITY_FIG,
                            style={"height": "260px"},
                        ),
                    ],
                    width=6,
                ),
            ],
            className="mb-3",
        ),

        # ── Run history ────────────────────────────────────────────────────────
        dbc.Row(dbc.Col(html.H6("Run History", className="mb-2"), width=12)),
        dbc.Row(
            dbc.Col(
                dcc.Dropdown(
                    id="backtest-hash-filter-dd",
                    placeholder="Filter by hash…",
                    clearable=True,
                ),
                width=3,
            ),
            className="mb-2",
        ),
        dbc.Row(dbc.Col(html.Div(id="backtest-history-table"), width=12)),

        # ── Hidden state ───────────────────────────────────────────────────────
        dcc.Store(id="backtest-run-id-store", data=None),
        dcc.Store(id="backtest-symbol-data", data=[]),    # full symbol list with min/max ts
        dcc.Store(id="backtest-date-range", data={}),     # {min_ts, max_ts} for selected symbol
        dcc.Interval(id="backtest-poll-interval", interval=2000, n_intervals=0, disabled=True),
    ],
    fluid=True,
    style={"padding": "12px"},
)
