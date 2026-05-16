import dash_bootstrap_components as dbc
from dash import dcc, html

SYMBOL_OPTIONS = [
    {"label": "BTC-USDT (KuCoin)", "value": "kucoin:BTC-USDT"},
    {"label": "ETH-USDT (KuCoin)", "value": "kucoin:ETH-USDT"},
    {"label": "BTC-USDT (Bybit)", "value": "bybit:BTC-USDT"},
]

_TF_OPTIONS = [
    {"label": "1s",  "value": "1s"},
    {"label": "1m",  "value": "1m"},
    {"label": "2m",  "value": "2m"},
    {"label": "3m",  "value": "3m"},
    {"label": "4m",  "value": "4m"},
    {"label": "6m",  "value": "6m"},
    {"label": "10m", "value": "10m"},
    {"label": "12m", "value": "12m"},
    {"label": "15m", "value": "15m"},
    {"label": "30m", "value": "30m"},
    {"label": "45m", "value": "45m"},
    {"label": "1h",  "value": "1h"},
    {"label": "2h",  "value": "2h"},
    {"label": "3h",  "value": "3h"},
    {"label": "4h",  "value": "4h"},
    {"label": "6h",  "value": "6h"},
    {"label": "8h",  "value": "8h"},
    {"label": "12h", "value": "12h"},
    {"label": "1d",  "value": "1d"},
    {"label": "1w",  "value": "1w"},
]

_nav = dbc.Nav(
    [
        dbc.NavLink("Charts", href="/", active="exact", id="nav-charts"),
        dbc.NavLink("Bots", href="/bots", active="exact", id="nav-bots"),
        dbc.NavLink("Backtests", href="/backtests", active="exact", id="nav-backtests"),
    ],
    pills=True,
    style={"marginBottom": "10px"},
)

_charts_page = dbc.Container(
    [
        dbc.Row(
            [
                dbc.Col(
                    dcc.Dropdown(
                        id="symbol-dropdown",
                        options=SYMBOL_OPTIONS,
                        value="kucoin:BTC-USDT",
                        clearable=False,
                    ),
                    width=3,
                ),
                dbc.Col(
                    dcc.Dropdown(
                        id="tf-dropdown",
                        options=_TF_OPTIONS,
                        value="1s",
                        clearable=False,
                    ),
                    width=1,
                ),
                dbc.Col(
                    html.Div(
                        id="status-bar",
                        style={"color": "#888", "fontSize": "12px", "lineHeight": "36px", "paddingLeft": "12px"},
                    ),
                    width=8,
                ),
            ],
            style={"marginBottom": "8px"},
        ),
        dbc.Row(
            [
                dbc.Col(
                    [
                        dbc.Row(
                            [
                                dbc.Col(
                                    dcc.Graph(id="candlestick-graph", figure={}, style={"height": "420px"}),
                                    width=10,
                                ),
                                dbc.Col(
                                    dcc.Graph(id="vol-profile-graph", figure={}, style={"height": "420px"}),
                                    width=2,
                                ),
                            ],
                        ),
                        dbc.Row(
                            [
                                dbc.Col(
                                    dcc.Graph(id="cvd-graph", figure={}, style={"height": "280px"}),
                                    width=6,
                                ),
                                dbc.Col(
                                    dcc.Graph(id="bidask-graph", figure={}, style={"height": "280px"}),
                                    width=6,
                                ),
                            ],
                        ),
                    ],
                    width=6,
                ),
                dbc.Col(
                    dcc.Graph(id="heatmap-graph", figure={}, style={"height": "700px"}),
                    width=6,
                ),
            ],
        ),
        dcc.Store(id="candle-store", data=[]),
        dcc.Store(id="last-ts", data=None),
        dcc.Store(id="ob-store", data=[]),
        dcc.Store(id="ob-cursor", data="0"),
        dcc.Store(id="ob-cursor-symbol", data=""),
        dcc.Store(id="active-tf", data="1s"),
        dcc.Interval(id="live-interval", interval=1000, n_intervals=0),
        dbc.Modal(
            [
                dbc.ModalHeader(id="footprint-modal-title"),
                dbc.ModalBody(dcc.Graph(id="footprint-chart", figure={}, style={"height": "500px"})),
            ],
            id="footprint-modal",
            is_open=False,
            size="lg",
        ),
    ],
    fluid=True,
    style={"padding": "0"},
)

layout = dbc.Container(
    [
        dcc.Location(id="url", refresh=False),
        _nav,
        html.Div(id="page-content"),
    ],
    fluid=True,
    style={"padding": "12px"},
)
