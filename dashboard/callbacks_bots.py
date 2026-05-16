"""Bot management page callbacks."""
from __future__ import annotations

import os

import plotly.graph_objects as go
from dash import Input, Output, State, callback, no_update
import dash_bootstrap_components as dbc
from dash import html

import bot_data

_QUESTDB_URL = os.environ.get("QUESTDB_HTTP_ADDR", "http://questdb:9000")


def _pnl_color(val: float) -> str:
    return "#4CAF50" if val >= 0 else "#f44336"


@callback(
    Output("bot-total-pnl", "children"),
    Output("bot-total-pnl", "style"),
    Output("bot-leaderboard", "data"),
    Output("bot-recent-trades", "data"),
    Input("bot-mode-toggle", "value"),
    Input("bot-refresh-btn", "n_clicks"),
    Input("bot-interval", "n_intervals"),
)
def refresh_bot_overview(mode, _clicks, _n):
    paper = mode == "paper"
    total_pnl = bot_data.fetch_total_pnl(paper, _QUESTDB_URL)
    bots = bot_data.fetch_bot_overview(paper, _QUESTDB_URL)
    trades = bot_data.fetch_recent_trades(paper, _QUESTDB_URL, limit=50)

    pnl_label = f"Total PnL: {total_pnl:+.4f} USDT"
    pnl_style = {
        "fontSize": "20px",
        "fontWeight": "bold",
        "color": _pnl_color(total_pnl),
    }

    lb_data = [
        {
            "strategy": b["strategy"],
            "exchange": b["exchange"],
            "symbol": b["symbol"],
            "trade_count": b["trade_count"],
            "total_pnl": float(b["total_pnl"] or 0),
            "avg_pnl_per_trade": float(b["avg_pnl_per_trade"] or 0),
            "last_trade_ts": str(b.get("last_trade_ts", ""))[:19],
        }
        for b in bots
    ]

    tr_data = [
        {
            "ts": str(t.get("ts", ""))[:19],
            "strategy": t.get("strategy", ""),
            "symbol": t.get("symbol", ""),
            "side": t.get("side", ""),
            "filled_size": round(float(t.get("filled_size", 0) or 0), 4),
            "avg_fill_price": round(float(t.get("avg_fill_price", 0) or 0), 2),
            "realized_pnl": float(t.get("realized_pnl", 0) or 0),
        }
        for t in trades
    ]

    return pnl_label, pnl_style, lb_data, tr_data


@callback(
    Output("bot-leaderboard", "selected_rows"),
    Input("bot-mode-toggle", "value"),
)
def clear_leaderboard_selection(_mode):
    """Clear row selection when mode changes so the detail pane doesn't show stale data."""
    return []


@callback(
    Output("bot-selected", "data"),
    Input("bot-leaderboard", "selected_rows"),
    State("bot-leaderboard", "data"),
    State("bot-mode-toggle", "value"),
    prevent_initial_call=True,
)
def select_bot(selected_rows, lb_data, mode):
    if not selected_rows or not lb_data or selected_rows[0] >= len(lb_data):
        return no_update
    row = lb_data[selected_rows[0]]
    return {
        "strategy": row["strategy"],
        "exchange": row["exchange"],
        "symbol": row["symbol"],
        "mode": mode,
    }


@callback(
    Output("bot-detail-collapse", "is_open"),
    Output("bot-detail-title", "children"),
    Output("bot-metrics-cards", "children"),
    Output("bot-trade-history", "data"),
    Output("bot-equity-curve", "figure"),
    Input("bot-selected", "data"),
    prevent_initial_call=True,
)
def show_bot_detail(selected):
    if not selected:
        return False, "", [], [], go.Figure()

    paper = selected.get("mode", "paper") == "paper"
    strategy = selected["strategy"]
    exchange = selected["exchange"]
    symbol = selected["symbol"]

    trades = bot_data.fetch_bot_trades(strategy, exchange, symbol, paper, _QUESTDB_URL)
    metrics = bot_data.compute_bot_metrics(trades)
    equity_df = bot_data.compute_equity_curve(trades)

    title = f"{strategy} — {exchange}:{symbol} ({'Paper' if paper else 'Live'})"

    cards = dbc.Row(
        [
            _metric_card("Trades", str(metrics["trade_count"]), "#ccc"),
            _metric_card("Total PnL", f"{metrics['total_pnl']:+.4f}", _pnl_color(metrics["total_pnl"])),
            _metric_card("Win Rate", f"{metrics['win_rate']}%", "#ccc"),
            _metric_card("Max Drawdown", f"{metrics['max_drawdown']:+.4f}", "#4CAF50" if metrics["max_drawdown"] == 0.0 else "#f44336"),
            _metric_card("Avg PnL/Trade", f"{metrics['avg_pnl_per_trade']:+.4f}", _pnl_color(metrics["avg_pnl_per_trade"])),
        ],
        className="g-2",
    )

    th_data = [
        {
            "ts": str(t.get("ts", ""))[:19],
            "side": t.get("side", ""),
            "filled_size": round(float(t.get("filled_size", 0) or 0), 4),
            "avg_fill_price": round(float(t.get("avg_fill_price", 0) or 0), 2),
            "realized_pnl": float(t.get("realized_pnl", 0) or 0),
            "signal_type": t.get("signal_type", ""),
        }
        for t in trades
    ]

    if not equity_df.empty:
        fig = go.Figure(
            go.Scatter(
                x=equity_df["ts"],
                y=equity_df["cumulative_pnl"],
                mode="lines",
                line=dict(color="#4CAF50", width=2),
                name="Equity",
                fill="tozeroy",
                fillcolor="rgba(76,175,80,0.1)",
            )
        )
        fig.update_layout(
            template="plotly_dark",
            margin=dict(l=40, r=10, t=30, b=30),
            title="PnL Equity Curve",
            xaxis_title=None,
            yaxis_title="Cumulative PnL (USDT)",
            showlegend=False,
        )
    else:
        fig = go.Figure()
        fig.update_layout(template="plotly_dark", title="No trade data")

    return True, title, cards, th_data, fig


def _metric_card(label: str, value: str, color: str) -> dbc.Col:
    return dbc.Col(
        dbc.Card(
            dbc.CardBody(
                [
                    html.Div(label, style={"color": "#888", "fontSize": "11px"}),
                    html.Div(value, style={"fontSize": "18px", "fontWeight": "bold", "color": color}),
                ]
            ),
            style={"backgroundColor": "#2c2c2c"},
        ),
        width="auto",
    )
