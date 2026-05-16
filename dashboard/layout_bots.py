"""Bot management page layout."""
import dash_bootstrap_components as dbc
from dash import dash_table, dcc, html

_MODE_OPTIONS = [
    {"label": "Paper", "value": "paper"},
    {"label": "Live", "value": "live"},
]

_TABLE_STYLE_DARK = {
    "backgroundColor": "#1e1e1e",
    "color": "#ccc",
    "border": "1px solid #444",
}

_CELL_COND = [
    {"if": {"column_id": "total_pnl"}, "color": "inherit"},
    {"if": {"column_id": "avg_pnl_per_trade"}, "color": "inherit"},
]

bot_page_layout = dbc.Container(
    [
        # ── Top bar ───────────────────────────────────────────────────────────
        dbc.Row(
            [
                dbc.Col(
                    html.H5("Bot Management", style={"color": "#fff", "marginBottom": 0}),
                    width="auto",
                    className="d-flex align-items-center",
                ),
                dbc.Col(
                    dbc.RadioItems(
                        id="bot-mode-toggle",
                        options=_MODE_OPTIONS,
                        value="paper",
                        inline=True,
                        inputClassName="me-1",
                        labelClassName="me-3",
                    ),
                    width="auto",
                    className="d-flex align-items-center ms-3",
                ),
                dbc.Col(
                    html.Div(
                        id="bot-total-pnl",
                        style={"fontSize": "20px", "fontWeight": "bold"},
                    ),
                    width="auto",
                    className="d-flex align-items-center ms-4",
                ),
                dbc.Col(
                    dbc.Button("Refresh", id="bot-refresh-btn", color="secondary", size="sm"),
                    width="auto",
                    className="ms-auto d-flex align-items-center",
                ),
            ],
            align="center",
            style={"marginBottom": "14px"},
        ),
        # ── Leaderboard + Recent trades ───────────────────────────────────────
        dbc.Row(
            [
                dbc.Col(
                    [
                        html.H6("Bot Leaderboard (click row to drill down)", style={"color": "#aaa"}),
                        dash_table.DataTable(
                            id="bot-leaderboard",
                            columns=[
                                {"name": "Strategy", "id": "strategy"},
                                {"name": "Exchange", "id": "exchange"},
                                {"name": "Symbol", "id": "symbol"},
                                {"name": "Trades", "id": "trade_count", "type": "numeric"},
                                {"name": "Total PnL", "id": "total_pnl", "type": "numeric", "format": {"specifier": "+.4f"}},
                                {"name": "Avg/Trade", "id": "avg_pnl_per_trade", "type": "numeric", "format": {"specifier": "+.4f"}},
                                {"name": "Last Trade", "id": "last_trade_ts"},
                            ],
                            data=[],
                            row_selectable="single",
                            selected_rows=[],
                            style_table={"overflowX": "auto"},
                            style_header={
                                "backgroundColor": "#2c2c2c",
                                "color": "#aaa",
                                "borderBottom": "1px solid #555",
                                "fontWeight": "bold",
                            },
                            style_cell={
                                "backgroundColor": "#1e1e1e",
                                "color": "#ccc",
                                "border": "1px solid #333",
                                "padding": "6px 10px",
                                "fontSize": "12px",
                            },
                            style_data_conditional=[
                                {
                                    "if": {"filter_query": "{total_pnl} >= 0", "column_id": "total_pnl"},
                                    "color": "#4CAF50",
                                },
                                {
                                    "if": {"filter_query": "{total_pnl} < 0", "column_id": "total_pnl"},
                                    "color": "#f44336",
                                },
                                {
                                    "if": {"filter_query": "{avg_pnl_per_trade} >= 0", "column_id": "avg_pnl_per_trade"},
                                    "color": "#4CAF50",
                                },
                                {
                                    "if": {"filter_query": "{avg_pnl_per_trade} < 0", "column_id": "avg_pnl_per_trade"},
                                    "color": "#f44336",
                                },
                                {
                                    "if": {"state": "selected"},
                                    "backgroundColor": "#2a3a4a",
                                    "border": "1px solid #4CAF50",
                                },
                            ],
                            page_size=10,
                        ),
                    ],
                    width=6,
                ),
                dbc.Col(
                    [
                        html.H6("Recent Trades", style={"color": "#aaa"}),
                        dash_table.DataTable(
                            id="bot-recent-trades",
                            columns=[
                                {"name": "Time", "id": "ts"},
                                {"name": "Strategy", "id": "strategy"},
                                {"name": "Symbol", "id": "symbol"},
                                {"name": "Side", "id": "side"},
                                {"name": "Size", "id": "filled_size", "type": "numeric"},
                                {"name": "Price", "id": "avg_fill_price", "type": "numeric"},
                                {"name": "PnL", "id": "realized_pnl", "type": "numeric", "format": {"specifier": "+.4f"}},
                            ],
                            data=[],
                            style_table={"overflowX": "auto"},
                            style_header={
                                "backgroundColor": "#2c2c2c",
                                "color": "#aaa",
                                "borderBottom": "1px solid #555",
                                "fontWeight": "bold",
                            },
                            style_cell={
                                "backgroundColor": "#1e1e1e",
                                "color": "#ccc",
                                "border": "1px solid #333",
                                "padding": "6px 10px",
                                "fontSize": "12px",
                            },
                            style_data_conditional=[
                                {
                                    "if": {"filter_query": "{realized_pnl} >= 0", "column_id": "realized_pnl"},
                                    "color": "#4CAF50",
                                },
                                {
                                    "if": {"filter_query": "{realized_pnl} < 0", "column_id": "realized_pnl"},
                                    "color": "#f44336",
                                },
                            ],
                            page_size=15,
                        ),
                    ],
                    width=6,
                ),
            ],
            style={"marginBottom": "16px"},
        ),
        # ── Bot detail (hidden until row selected) ────────────────────────────
        dbc.Collapse(
            [
                html.Hr(style={"borderColor": "#444"}),
                html.H6(id="bot-detail-title", style={"color": "#ccc", "marginBottom": "10px"}),
                dbc.Row(
                    [
                        dbc.Col(
                            html.Div(id="bot-metrics-cards", style={"marginBottom": "12px"}),
                            width=12,
                        ),
                    ],
                    style={"marginBottom": "12px"},
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                html.H6("Trade History", style={"color": "#aaa"}),
                                dash_table.DataTable(
                                    id="bot-trade-history",
                                    columns=[
                                        {"name": "Time", "id": "ts"},
                                        {"name": "Side", "id": "side"},
                                        {"name": "Size", "id": "filled_size", "type": "numeric"},
                                        {"name": "Price", "id": "avg_fill_price", "type": "numeric"},
                                        {"name": "PnL", "id": "realized_pnl", "type": "numeric", "format": {"specifier": "+.4f"}},
                                        {"name": "Signal", "id": "signal_type"},
                                    ],
                                    data=[],
                                    style_table={"overflowX": "auto"},
                                    style_header={
                                        "backgroundColor": "#2c2c2c",
                                        "color": "#aaa",
                                        "borderBottom": "1px solid #555",
                                        "fontWeight": "bold",
                                    },
                                    style_cell={
                                        "backgroundColor": "#1e1e1e",
                                        "color": "#ccc",
                                        "border": "1px solid #333",
                                        "padding": "6px 10px",
                                        "fontSize": "12px",
                                    },
                                    style_data_conditional=[
                                        {
                                            "if": {"filter_query": "{realized_pnl} >= 0", "column_id": "realized_pnl"},
                                            "color": "#4CAF50",
                                        },
                                        {
                                            "if": {"filter_query": "{realized_pnl} < 0", "column_id": "realized_pnl"},
                                            "color": "#f44336",
                                        },
                                    ],
                                    page_size=20,
                                    sort_action="native",
                                    sort_by=[{"column_id": "ts", "direction": "desc"}],
                                ),
                            ],
                            width=6,
                        ),
                        dbc.Col(
                            dcc.Graph(
                                id="bot-equity-curve",
                                figure={},
                                style={"height": "320px"},
                            ),
                            width=6,
                        ),
                    ],
                ),
            ],
            id="bot-detail-collapse",
            is_open=False,
        ),
        # ── Hidden state ──────────────────────────────────────────────────────
        dcc.Store(id="bot-selected", data=None),
        dcc.Interval(id="bot-interval", interval=10_000, n_intervals=0),
    ],
    fluid=True,
    style={"padding": "12px"},
)
