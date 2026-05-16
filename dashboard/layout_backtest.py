"""Layout for the /backtests page."""
from __future__ import annotations

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

backtest_page_layout = dbc.Container(
    [
        # ── Top controls ──────────────────────────────────────────────────────
        dbc.Row(
            [
                dbc.Col(
                    dcc.Dropdown(
                        id="backtest-strategy-dd",
                        placeholder="Select strategy…",
                        clearable=False,
                    ),
                    width=3,
                ),
                dbc.Col(
                    dcc.Dropdown(
                        id="backtest-exchange-dd",
                        options=_EXCHANGE_OPTIONS,
                        value="bybit",
                        clearable=False,
                    ),
                    width=2,
                ),
                dbc.Col(
                    dbc.Input(
                        id="backtest-symbol-inp",
                        placeholder="BTCUSDT",
                        value="BTCUSDT",
                        debounce=True,
                    ),
                    width=2,
                ),
                dbc.Col(
                    dcc.Dropdown(
                        id="backtest-tf-dd",
                        options=_TF_OPTIONS,
                        value="1s",
                        clearable=False,
                    ),
                    width=1,
                ),
                dbc.Col(
                    dbc.Input(
                        id="backtest-start-date",
                        placeholder="2026-01-01",
                        value="2026-01-01",
                        debounce=True,
                    ),
                    width=2,
                ),
                dbc.Col(
                    dbc.Input(
                        id="backtest-end-date",
                        placeholder="2026-02-01",
                        value="2026-02-01",
                        debounce=True,
                    ),
                    width=1,
                ),
                dbc.Col(
                    dbc.Input(
                        id="backtest-capital-inp",
                        placeholder="10000",
                        value="10000",
                        type="number",
                        debounce=True,
                    ),
                    width=1,
                ),
            ],
            className="mb-2",
            align="center",
        ),
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
        # ── Code + metrics ────────────────────────────────────────────────────
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
                        ),
                        dcc.Graph(
                            id="backtest-equity-chart",
                            figure={},
                            style={"height": "260px"},
                        ),
                    ],
                    width=6,
                ),
            ],
            className="mb-3",
        ),
        # ── Run history ───────────────────────────────────────────────────────
        dbc.Row(
            [
                dbc.Col(
                    html.H6("Run History", className="mb-2"),
                    width=12,
                ),
            ],
        ),
        dbc.Row(
            [
                dbc.Col(
                    dcc.Dropdown(
                        id="backtest-hash-filter-dd",
                        placeholder="Filter by hash…",
                        clearable=True,
                    ),
                    width=3,
                ),
            ],
            className="mb-2",
        ),
        dbc.Row(
            dbc.Col(
                html.Div(id="backtest-history-table"),
                width=12,
            ),
        ),
        # ── Hidden state ──────────────────────────────────────────────────────
        dcc.Store(id="backtest-run-id-store", data=None),
        dcc.Interval(id="backtest-poll-interval", interval=2000, n_intervals=0, disabled=True),
    ],
    fluid=True,
    style={"padding": "12px"},
)
