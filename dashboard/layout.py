import dash_bootstrap_components as dbc
from dash import dcc

SYMBOL_OPTIONS = [
    {"label": "BTC-USDT (KuCoin)", "value": "kucoin:BTC-USDT"},
    {"label": "ETH-USDT (KuCoin)", "value": "kucoin:ETH-USDT"},
    {"label": "BTC-USDT (Bybit)", "value": "bybit:BTC-USDT"},
]

layout = dbc.Container(
    [
        dbc.Row(
            dbc.Col(
                dcc.Dropdown(
                    id="symbol-dropdown",
                    options=SYMBOL_OPTIONS,
                    value="kucoin:BTC-USDT",
                    clearable=False,
                    style={"marginBottom": "8px"},
                )
            )
        ),
        dbc.Row(
            [
                # Left column: top panels + bottom panels stacked
                dbc.Col(
                    [
                        dbc.Row(
                            [
                                dbc.Col(
                                    dcc.Loading(
                                        dcc.Graph(id="candlestick-graph", figure={}, style={"height": "420px"}),
                                        id="loading-candlestick",
                                    ),
                                    width=10,
                                ),
                                dbc.Col(
                                    dcc.Loading(
                                        dcc.Graph(id="vol-profile-graph", figure={}, style={"height": "420px"}),
                                        id="loading-vol-profile",
                                    ),
                                    width=2,
                                ),
                            ],
                        ),
                        dbc.Row(
                            [
                                dbc.Col(
                                    dcc.Loading(
                                        dcc.Graph(id="cvd-graph", figure={}, style={"height": "280px"}),
                                        id="loading-cvd",
                                    ),
                                    width=6,
                                ),
                                dbc.Col(
                                    dcc.Loading(
                                        dcc.Graph(id="bidask-graph", figure={}, style={"height": "280px"}),
                                        id="loading-bidask",
                                    ),
                                    width=6,
                                ),
                            ],
                        ),
                    ],
                    width=6,
                ),
                # Right column: heatmap spanning full height of both left sub-rows
                dbc.Col(
                    dcc.Loading(
                        dcc.Graph(id="heatmap-graph", figure={}, style={"height": "700px"}),
                        id="loading-heatmaps",
                    ),
                    width=6,
                ),
            ],
        ),
        # dcc.Store components — data layer, invisible
        dcc.Store(id="candle-store", data=[]),
        dcc.Store(id="last-ts", data=None),
        dcc.Store(id="ob-store", data=[]),
        dcc.Store(id="ob-cursor", data="0"),
        dcc.Store(id="ob-cursor-symbol", data=""),
        dcc.Interval(id="live-interval", interval=1000, n_intervals=0),
        # Footprint modal — opened by clicking a candlestick bar
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
    style={"padding": "12px"},
)
