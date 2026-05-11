import dash_bootstrap_components as dbc
from dash import dcc

SYMBOL_OPTIONS = [
    {"label": "BTC-USDT (KuCoin)", "value": "kucoin:BTC-USDT"},
    {"label": "ETH-USDT (KuCoin)", "value": "kucoin:ETH-USDT"},
    {"label": "BTC-USDT (Bybit)", "value": "bybit:BTC-USDT"},
]

_PLACEHOLDER_STYLE = {"height": "100%", "background": "#2a2a2a", "color": "#555", "display": "flex", "alignItems": "center", "justifyContent": "center"}

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
                dbc.Col(
                    dcc.Loading(
                        dbc.Card("Candlestick", body=True, style={**_PLACEHOLDER_STYLE, "height": "420px"}),
                        id="loading-candlestick",
                    ),
                    width=5,
                ),
                dbc.Col(
                    dcc.Loading(
                        dbc.Card("Vol Profile", body=True, style={**_PLACEHOLDER_STYLE, "height": "420px"}),
                        id="loading-vol-profile",
                    ),
                    width=2,
                ),
                dbc.Col(
                    dcc.Loading(
                        dbc.Card("Heatmaps", body=True, style={**_PLACEHOLDER_STYLE, "height": "720px"}),
                        id="loading-heatmaps",
                    ),
                    width=5,
                ),
            ],
            style={"marginBottom": "8px"},
        ),
        dbc.Row(
            [
                dbc.Col(
                    dcc.Loading(
                        dbc.Card("CVD", body=True, style={**_PLACEHOLDER_STYLE, "height": "280px"}),
                        id="loading-cvd",
                    ),
                    width=6,
                ),
                dbc.Col(
                    dcc.Loading(
                        dbc.Card("Bid/Ask", body=True, style={**_PLACEHOLDER_STYLE, "height": "280px"}),
                        id="loading-bidask",
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
    ],
    fluid=True,
    style={"padding": "12px"},
)
