"""Callbacks for the /chart page — backtest trade viewer."""
from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

import pandas as pd
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import Input, Output, callback, html, no_update

import backtest_data
import charts

_QUESTDB_URL = os.environ.get("QUESTDB_HTTP_ADDR", "http://questdb:9000")
_WINDOW_BARS = 300  # number of bars on each side of the trade


def _parse_search(search: str | None) -> dict[str, str]:
    """Parse '?key=val&...' → dict.  Returns empty dict on bad input."""
    if not search:
        return {}
    try:
        qs = parse_qs(urlparse("x:" + search).query)
        return {k: v[0] for k, v in qs.items() if v}
    except Exception:
        return {}


# ── Load candles + trades when the page URL changes ──────────────────────────

@callback(
    Output("chart-candle-store", "data"),
    Output("chart-trades-store", "data"),
    Output("chart-selected-idx", "data"),
    Output("chart-context-info", "children"),
    Input("url", "pathname"),
    Input("url", "search"),
)
def load_chart_data(pathname: str | None, search: str | None):
    if pathname != "/chart":
        return no_update, no_update, no_update, no_update

    params = _parse_search(search)
    exchange   = params.get("exchange", "bybit")
    symbol     = params.get("symbol", "BTC-USDT")
    tf         = params.get("tf", "1s")
    center_ts  = params.get("center_ts", "")
    run_id     = params.get("run_id", "")
    trade_idx  = int(params.get("trade_idx", "0"))

    # Fetch candle window around the selected trade
    rows: list[dict] = []
    if center_ts:
        rows = backtest_data.fetch_candles_around(
            exchange, symbol, center_ts, tf=tf, window=_WINDOW_BARS
        )

    # Fetch trades from in-memory backtest result
    trades: list[dict] = []
    if run_id:
        status = backtest_data.poll_run_status(run_id)
        if status.get("status") == "done":
            trades = status.get("result", {}).get("trades", [])

    run_short = run_id[:8] + "…" if len(run_id) > 8 else run_id
    n_candles = len(rows)
    n_trades  = len(trades)
    context_info = (
        f"{exchange} : {symbol}  ·  {tf}  ·  "
        f"{n_candles} candles  ·  {n_trades} trades in run  ·  run {run_short}"
    )

    return rows, trades, trade_idx, context_info


# ── Build candlestick chart with trade markers ────────────────────────────────

@callback(
    Output("chart-candlestick", "figure"),
    Input("chart-candle-store", "data"),
    Input("chart-trades-store", "data"),
    Input("chart-selected-idx", "data"),
)
def build_chart(candle_rows, trades, selected_idx):
    df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
    fig = charts.build_candlestick(df)
    if not df.empty:
        fig = charts.add_volume_levels(fig, df)

    trades = trades or []
    if not trades:
        return fig

    # --- All entries (small green triangles up) ---
    entry_x = [t.get("entry_ts") for t in trades if t.get("entry_ts")]
    entry_y = [t.get("entry_price") for t in trades if t.get("entry_ts")]
    if entry_x:
        fig.add_trace(go.Scatter(
            x=entry_x,
            y=entry_y,
            mode="markers",
            marker=dict(symbol="triangle-up", size=10, color="#26A69A",
                        line=dict(color="#1a7a71", width=1)),
            name="Buy",
            hovertemplate="Entry<br>%{x}<br>%{y:.4f}<extra></extra>",
        ))

    # --- All exits (small red triangles down) ---
    exit_x = [t.get("exit_ts") for t in trades if t.get("exit_ts")]
    exit_y = [t.get("exit_price") for t in trades if t.get("exit_ts")]
    if exit_x:
        fig.add_trace(go.Scatter(
            x=exit_x,
            y=exit_y,
            mode="markers",
            marker=dict(symbol="triangle-down", size=10, color="#EF5350",
                        line=dict(color="#b33b38", width=1)),
            name="Sell",
            hovertemplate="Exit<br>%{x}<br>%{y:.4f}<extra></extra>",
        ))

    # --- Highlighted trade (golden, larger) ---
    sel = None
    if selected_idx is not None and 0 <= int(selected_idx) < len(trades):
        sel = trades[int(selected_idx)]

    if sel:
        e_ts = sel.get("entry_ts")
        e_px = sel.get("entry_price")
        x_ts = sel.get("exit_ts")
        x_px = sel.get("exit_price")

        if e_ts and e_px is not None:
            fig.add_trace(go.Scatter(
                x=[e_ts], y=[e_px],
                mode="markers",
                marker=dict(symbol="triangle-up", size=18, color="#FFD700",
                            line=dict(color="#fff", width=2)),
                name="Selected Buy",
                hovertemplate="Selected Entry<br>%{x}<br>%{y:.4f}<extra></extra>",
            ))

        if x_ts and x_px is not None:
            fig.add_trace(go.Scatter(
                x=[x_ts], y=[x_px],
                mode="markers",
                marker=dict(symbol="triangle-down", size=18, color="#FF9800",
                            line=dict(color="#fff", width=2)),
                name="Selected Sell",
                hovertemplate="Selected Exit<br>%{x}<br>%{y:.4f}<extra></extra>",
            ))

        # Draw a shaded region between entry and exit
        if e_ts and x_ts:
            pnl = sel.get("pnl_net", sel.get("pnl", 0)) or 0
            fill_color = "rgba(38,166,154,0.10)" if float(pnl) >= 0 else "rgba(239,83,80,0.10)"
            fig.add_vrect(
                x0=e_ts, x1=x_ts,
                fillcolor=fill_color,
                line_width=0,
                annotation_text=f"{'▲' if float(pnl) >= 0 else '▼'} ${float(pnl):.2f}",
                annotation_font_color="#ccc",
                annotation_font_size=11,
                annotation_position="top left",
            )

    return fig


# ── CVD panel ─────────────────────────────────────────────────────────────────

@callback(
    Output("chart-cvd", "figure"),
    Input("chart-candle-store", "data"),
)
def update_cvd(candle_rows):
    df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
    return charts.build_cvd_panel(df)


# ── Bid/ask panel ─────────────────────────────────────────────────────────────

@callback(
    Output("chart-bidask", "figure"),
    Input("chart-candle-store", "data"),
)
def update_bidask(candle_rows):
    df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
    return charts.build_bidask_panel(df)


# ── Selected trade info card ──────────────────────────────────────────────────

@callback(
    Output("chart-trade-info", "children"),
    Input("chart-trades-store", "data"),
    Input("chart-selected-idx", "data"),
)
def update_trade_info(trades, selected_idx):
    trades = trades or []
    if not trades or selected_idx is None:
        return html.Span("No trade selected.", style={"color": "#666"})

    idx = int(selected_idx)
    if not (0 <= idx < len(trades)):
        return html.Span("Trade index out of range.", style={"color": "#666"})

    t = trades[idx]
    pnl     = t.get("pnl", 0) or 0
    pnl_net = t.get("pnl_net", pnl) or 0
    color   = "#4caf50" if float(pnl_net) >= 0 else "#f44336"

    def _cell(label, value, val_color="#ccc"):
        return dbc.Col([
            html.Span(label, style={"color": "#666", "fontSize": "11px", "display": "block"}),
            html.Span(str(value), style={"color": val_color, "fontSize": "13px"}),
        ], width="auto", style={"marginRight": "24px"})

    return dbc.Row([
        _cell(f"Trade #{idx + 1}", t.get("direction", "long").upper()),
        _cell("Entry",  t.get("entry_ts", "")[:19].replace("T", " ")),
        _cell("Exit",   t.get("exit_ts", "")[:19].replace("T", " ")),
        _cell("Entry $", f"{t.get('entry_price', 0):.4f}"),
        _cell("Exit $",  f"{t.get('exit_price', 0):.4f}"),
        _cell("Size",    f"{t.get('size', 0):.6f}"),
        _cell("PnL",     f"${float(pnl):.4f}", color),
        _cell("PnL Net", f"${float(pnl_net):.4f}", color),
    ], align="center")


# ── All-trades mini table ─────────────────────────────────────────────────────

@callback(
    Output("chart-all-trades", "children"),
    Input("chart-trades-store", "data"),
    Input("chart-selected-idx", "data"),
)
def update_all_trades(trades, selected_idx):
    trades = trades or []
    if not trades:
        return html.Div("No trades.", style={"color": "#666", "fontSize": "12px"})

    rows = []
    for i, t in enumerate(trades):
        pnl_net = t.get("pnl_net", t.get("pnl", 0)) or 0
        pnl_color = "#4caf50" if float(pnl_net) >= 0 else "#f44336"
        is_sel = (selected_idx is not None and int(selected_idx) == i)
        bg = "#2a3a2a" if is_sel else ("transparent" if i % 2 == 0 else "#1e1e1e")
        rows.append(html.Tr([
            html.Td(str(i + 1),     style={"color": "#888"}),
            html.Td(t.get("entry_ts", "")[:19].replace("T", " "), style={"fontFamily": "monospace"}),
            html.Td(t.get("exit_ts", "")[:19].replace("T", " "),  style={"fontFamily": "monospace"}),
            html.Td(f"{t.get('entry_price', 0):.4f}"),
            html.Td(f"{t.get('exit_price',  0):.4f}"),
            html.Td(f"${float(pnl_net):.4f}", style={"color": pnl_color}),
        ], style={"backgroundColor": bg, "fontSize": "12px"}))

    return html.Table(
        [
            html.Thead(html.Tr([
                html.Th("#"),
                html.Th("Entry Time"),
                html.Th("Exit Time"),
                html.Th("Entry $"),
                html.Th("Exit $"),
                html.Th("PnL Net"),
            ], style={"color": "#888", "fontSize": "11px"})),
            html.Tbody(rows),
        ],
        style={
            "width": "100%",
            "borderCollapse": "collapse",
            "maxHeight": "320px",
            "overflowY": "auto",
            "display": "block",
        },
    )
