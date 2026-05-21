"""Bot management page callbacks."""
from __future__ import annotations

import os

import plotly.graph_objects as go
from dash import Input, Output, State, callback, no_update
import dash_bootstrap_components as dbc
from dash import html

import bot_data

_QUESTDB_URL = os.environ.get("QUESTDB_HTTP_ADDR", "http://questdb:9000")
_BOT_SERVICE_URL = os.environ.get("BOT_SERVICE_URL", "http://localhost:8090")


def _pnl_color(val: float | None) -> str:
    if val is None:
        return "#888"
    return "#4CAF50" if val >= 0 else "#f44336"


def _fmt(val: float | None, spec: str = "+.4f") -> str:
    if val is None:
        return "—"
    return format(val, spec)


# ── Overview refresh ──────────────────────────────────────────────────────────

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

    # Fetch from both sources in parallel (sequential is fine at this cadence)
    total_pnl = bot_data.fetch_total_pnl(paper, _QUESTDB_URL)
    running    = bot_data.fetch_running_bots(_BOT_SERVICE_URL)
    trade_stats = bot_data.fetch_bot_overview(paper, _QUESTDB_URL)
    recent_trades = bot_data.fetch_recent_trades(paper, _QUESTDB_URL, limit=50)

    pnl_label = f"Total PnL: {total_pnl:+.4f} USDT"
    pnl_style = {
        "fontSize": "20px",
        "fontWeight": "bold",
        "color": _pnl_color(total_pnl),
    }

    # Merge: running bots from the service + trade stats from QuestDB
    merged = bot_data.merge_bots(running, trade_stats, paper)

    lb_data = [
        {
            "strategy":         r["strategy"],
            "exchange":         r["exchange"],
            "symbol":           r["symbol"],
            "tf":               r.get("tf") or "—",
            "status":           r.get("status") or "unknown",
            "uptime":           r.get("uptime") or "—",
            "stop_loss_pct":    r.get("stop_loss_pct"),
            "max_position_pct": r.get("max_position_pct"),
            "trade_count":      r.get("trade_count"),
            "win_rate_pct":     r.get("win_rate_pct"),
            "total_pnl":        r.get("total_pnl"),
            "avg_pnl_per_trade":r.get("avg_pnl_per_trade"),
            "last_trade_ts":    r.get("last_trade_ts") or "—",
        }
        for r in merged
    ]

    tr_data = [
        {
            "ts":             str(t.get("ts", ""))[:19],
            "strategy":       t.get("strategy", ""),
            "symbol":         t.get("symbol", ""),
            "side":           t.get("side", ""),
            "filled_size":    round(float(t.get("filled_size", 0) or 0), 4),
            "avg_fill_price": round(float(t.get("avg_fill_price", 0) or 0), 2),
            "realized_pnl":   float(t.get("realized_pnl", 0) or 0),
        }
        for t in recent_trades
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
        "symbol":   row["symbol"],
        "mode":     mode,
    }


# ── Detail pane ───────────────────────────────────────────────────────────────

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

    paper    = selected.get("mode", "paper") == "paper"
    strategy = selected["strategy"]
    exchange = selected["exchange"]
    symbol   = selected["symbol"]

    trades  = bot_data.fetch_bot_trades(strategy, exchange, symbol, paper, _QUESTDB_URL)
    metrics = bot_data.compute_bot_metrics(trades)
    eq_df   = bot_data.compute_equity_curve(trades)

    mode_label = "Paper" if paper else "Live"
    title = f"{strategy} — {exchange}:{symbol} ({mode_label})"

    cards = dbc.Row(
        [
            _metric_card("Trades",       str(metrics["trade_count"]),                   "#ccc"),
            _metric_card("Total PnL",    f"{metrics['total_pnl']:+.4f}",               _pnl_color(metrics["total_pnl"])),
            _metric_card("Win Rate",     f"{metrics['win_rate']}%",                     _wr_color(metrics["win_rate"])),
            _metric_card("Max Drawdown", f"{metrics['max_drawdown']:+.4f}",             "#4CAF50" if metrics["max_drawdown"] == 0.0 else "#f44336"),
            _metric_card("Avg PnL/Trade",f"{metrics['avg_pnl_per_trade']:+.4f}",        _pnl_color(metrics["avg_pnl_per_trade"])),
        ],
        className="g-2",
    )

    th_data = [
        {
            "ts":             str(t.get("ts", ""))[:19],
            "side":           t.get("side", ""),
            "filled_size":    round(float(t.get("filled_size", 0) or 0), 4),
            "avg_fill_price": round(float(t.get("avg_fill_price", 0) or 0), 2),
            "realized_pnl":   float(t.get("realized_pnl", 0) or 0),
            "signal_type":    t.get("signal_type", ""),
        }
        for t in trades
    ]

    if not eq_df.empty:
        fig = go.Figure(
            go.Scatter(
                x=eq_df["ts"],
                y=eq_df["cumulative_pnl"],
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
        fig.update_layout(
            template="plotly_dark",
            title="No trades yet",
            annotations=[{
                "text": "Waiting for first trade…",
                "xref": "paper", "yref": "paper",
                "x": 0.5, "y": 0.5,
                "showarrow": False,
                "font": {"size": 14, "color": "#888"},
            }],
        )

    return True, title, cards, th_data, fig


# ── Helpers ───────────────────────────────────────────────────────────────────

def _wr_color(win_rate: float) -> str:
    return "#4CAF50" if win_rate >= 50 else "#f44336"


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
