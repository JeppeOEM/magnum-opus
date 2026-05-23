"""Callbacks for the /backtests page."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dash_table, dcc, html, no_update, ALL

import backtest_data
import charts
from layout_backtest import _EMPTY_EQUITY_FIG, _EMPTY_CANDLE_FIG


# ── Strategy dropdown ─────────────────────────────────────────────────────────

@callback(
    Output("backtest-strategy-dd", "options"),
    Output("backtest-strategy-status", "children"),
    Input("backtest-strategy-dd", "id"),
    Input("backtest-refresh-strategies-btn", "n_clicks"),
)
def load_strategy_options(_id, _n_clicks):
    strategies = backtest_data.fetch_strategies()
    if not strategies:
        status = html.Span(
            "⚠ No strategies found — is bot-service running? Click ↺ to retry.",
            style={"color": "#ff9800"},
        )
        return [], status
    options = [
        {
            "label": f"{s['name']} [{s.get('folder', 'active')}]",
            "value": s["name"],
        }
        for s in strategies
    ]
    return options, ""


# ── Exchange → symbol dropdown + date range data ──────────────────────────────

@callback(
    Output("backtest-symbol-dd", "options"),
    Output("backtest-symbol-dd", "value"),
    Output("backtest-symbol-data", "data"),
    Input("backtest-exchange-dd", "value"),
)
def load_symbol_options(exchange: str | None):
    symbols = backtest_data.fetch_available_symbols(exchange=exchange)
    options = [{"label": s["symbol"], "value": s["symbol"]} for s in symbols]
    value = symbols[0]["symbol"] if symbols else None
    return options, value, symbols


@callback(
    Output("backtest-date-range", "data"),
    Input("backtest-symbol-dd", "value"),
    State("backtest-symbol-data", "data"),
    State("backtest-exchange-dd", "value"),
)
def update_date_range(symbol, symbol_data, exchange):
    if not symbol or not symbol_data:
        return {}
    for row in (symbol_data or []):
        if row.get("symbol") == symbol and (not exchange or row.get("exchange") == exchange):
            return {"min_ts": row.get("min_ts"), "max_ts": row.get("max_ts")}
    return {}


# ── Date mode toggles ─────────────────────────────────────────────────────────

@callback(
    Output("backtest-start-collapse", "is_open"),
    Input("backtest-start-mode", "value"),
)
def toggle_start_date(mode):
    return mode == "custom"


@callback(
    Output("backtest-end-collapse", "is_open"),
    Input("backtest-end-mode", "value"),
)
def toggle_end_date(mode):
    return mode == "custom"


@callback(
    Output("backtest-range-info", "children"),
    Input("backtest-date-range", "data"),
    Input("backtest-start-mode", "value"),
    Input("backtest-end-mode", "value"),
    Input("backtest-start-date", "value"),
    Input("backtest-end-date", "value"),
)
def update_range_info(date_range, start_mode, end_mode, custom_start, custom_end):
    dr = date_range or {}
    start = _resolve_date(start_mode, "first", custom_start, dr.get("min_ts"))
    end   = _resolve_date(end_mode,   "last",  custom_end,   dr.get("max_ts"))
    if not start and not end:
        return ""
    start_str = start[:19].replace("T", " ") if start else "?"
    end_str   = end[:19].replace("T", " ")   if end   else "?"
    return f"Range: {start_str}  →  {end_str}"


# ── Strategy select → code viewer + history ───────────────────────────────────

@callback(
    Output("backtest-code-view", "children"),
    Output("backtest-hash-filter-dd", "options"),
    Output("backtest-history-table", "children"),
    Input("backtest-strategy-dd", "value"),
)
def on_strategy_select(strategy_name: str | None):
    if not strategy_name:
        return "Select a strategy to view its source code.", [], None
    strategies = backtest_data.fetch_strategies()
    code = next((s["code"] for s in strategies if s["name"] == strategy_name), "")
    df = backtest_data.fetch_run_history(strategy_name=strategy_name)
    hash_opts, table = _build_history(df)
    return code, hash_opts, table


# ── Run button ────────────────────────────────────────────────────────────────

@callback(
    Output("backtest-run-id-store", "data"),
    Output("backtest-status-div", "children"),
    Output("backtest-poll-interval", "disabled"),
    Output("backtest-equity-chart", "figure", allow_duplicate=True),
    Input("backtest-run-btn", "n_clicks"),
    State("backtest-strategy-dd", "value"),
    State("backtest-exchange-dd", "value"),
    State("backtest-symbol-dd", "value"),
    State("backtest-tf-dd", "value"),
    State("backtest-start-mode", "value"),
    State("backtest-end-mode", "value"),
    State("backtest-date-range", "data"),
    State("backtest-start-date", "value"),
    State("backtest-end-date", "value"),
    State("backtest-capital-inp", "value"),
    prevent_initial_call=True,
)
def on_run_click(
    n_clicks,
    strategy_name, exchange, symbol, tf,
    start_mode, end_mode, date_range,
    custom_start, custom_end,
    capital,
):
    if not strategy_name:
        return no_update, "Select a strategy first.", True, no_update
    if not symbol:
        return no_update, "Select a symbol first.", True, no_update

    dr = date_range or {}
    start_date = _resolve_date(start_mode, "first", custom_start, dr.get("min_ts"))
    end_date   = _resolve_date(end_mode,   "last",  custom_end,   dr.get("max_ts"))

    if not start_date:
        return no_update, "⚠ Start date unknown — wait for symbol data to load.", True, no_update
    if not end_date:
        return no_update, "⚠ End date unknown — wait for symbol data to load.", True, no_update

    try:
        cap = float(capital or 10000)
    except (ValueError, TypeError):
        cap = 10000.0

    result = backtest_data.submit_backtest(
        strategy_name=strategy_name,
        symbol=symbol,
        tf=tf or "1s",
        exchange=exchange or "bybit",
        start_date=start_date[:19],
        end_date=end_date[:19],
        capital=cap,
    )
    if "error" in result:
        return None, html.Span(f"❌ Error: {result['error']}", style={"color": "#f44336"}), True, no_update
    run_id = result.get("run_id")
    return run_id, f"Running… (run_id={run_id})", False, _EMPTY_EQUITY_FIG


# ── Polling ───────────────────────────────────────────────────────────────────

@callback(
    Output("backtest-status-div", "children", allow_duplicate=True),
    Output("backtest-metrics-div", "children"),
    Output("backtest-equity-chart", "figure"),
    Output("backtest-poll-interval", "disabled", allow_duplicate=True),
    Output("backtest-history-table", "children", allow_duplicate=True),
    Output("backtest-trades-store", "data"),
    Output("backtest-trades-div", "children"),
    Output("backtest-result-store", "data"),
    Output("backtest-gap-segments-store", "data"),
    Input("backtest-poll-interval", "n_intervals"),
    State("backtest-run-id-store", "data"),
    State("backtest-strategy-dd", "value"),
    State("backtest-exchange-dd", "value"),
    State("backtest-symbol-dd", "value"),
    State("backtest-tf-dd", "value"),
    prevent_initial_call=True,
)
def on_poll(n_intervals, run_id, strategy_name, exchange, symbol, tf):
    if not run_id:
        return no_update, no_update, no_update, True, no_update, no_update, no_update, no_update, no_update
    status_data = backtest_data.poll_run_status(run_id)
    if "error" in status_data:
        err = html.Span(f"❌ Poll error: {status_data['error']}", style={"color": "#f44336"})
        return err, no_update, no_update, True, no_update, no_update, no_update, no_update, no_update
    status = status_data.get("status", "unknown")
    if status == "running":
        return f"Running… (run_id={run_id})", no_update, no_update, False, no_update, no_update, no_update, no_update, no_update
    if status == "failed":
        error_msg = status_data.get("error", "unknown error")
        err = html.Span(f"❌ Failed: {error_msg}", style={"color": "#f44336"})
        return err, no_update, no_update, True, no_update, no_update, no_update, no_update, no_update
    result = status_data.get("result", {})
    segments = result.get("data_segments", [])
    metrics_div = _build_metrics(result, segments)
    eq_fig = _build_equity_figure(result)
    df = backtest_data.fetch_run_history(strategy_name=strategy_name)
    _, history_table = _build_history(df)
    trades = result.get("trades", [])
    trades_div = _build_trades_table(trades, run_id, exchange or "bybit", symbol or "", tf or "1s")
    return (
        html.Span(f"✓ Done (run_id={run_id})", style={"color": "#4caf50"}),
        metrics_div,
        eq_fig,
        True,
        history_table,
        trades,
        trades_div,
        result,
        segments,
    )


# ── Hash filter ───────────────────────────────────────────────────────────────

@callback(
    Output("backtest-history-table", "children", allow_duplicate=True),
    Input("backtest-hash-filter-dd", "value"),
    State("backtest-strategy-dd", "value"),
    prevent_initial_call=True,
)
def on_hash_filter(hash_val, strategy_name):
    df = backtest_data.fetch_run_history(strategy_name=strategy_name, hash_filter=hash_val)
    _, table = _build_history(df)
    return table


# ── Gap modal ─────────────────────────────────────────────────────────────────

@callback(
    Output("backtest-gap-modal", "is_open"),
    Output("backtest-gap-modal-body", "children"),
    Input("backtest-gap-badge-btn", "n_clicks"),
    State("backtest-gap-segments-store", "data"),
    prevent_initial_call=True,
)
def open_gap_modal(n_clicks, segments):
    if not n_clicks:
        return False, no_update
    return True, _build_coverage_detail(segments or [])


# ── Trade chart button → load inline chart ───────────────────────────────────

@callback(
    Output("backtest-chart-candles-store", "data"),
    Output("backtest-chart-trade-idx", "data"),
    Output("backtest-chart-collapse", "is_open"),
    Output("backtest-inline-chart-title", "children"),
    Input({"type": "trade-chart-btn", "index": ALL}, "n_clicks"),
    State("backtest-exchange-dd", "value"),
    State("backtest-symbol-dd", "value"),
    State("backtest-tf-dd", "value"),
    State("backtest-trades-store", "data"),
    prevent_initial_call=True,
)
def on_trade_chart_click(all_clicks, exchange, symbol, tf, trades):
    from dash import ctx
    if not any(c for c in (all_clicks or []) if c):
        return no_update, no_update, no_update, no_update

    triggered = ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        return no_update, no_update, no_update, no_update

    trade_idx = triggered.get("index", 0)
    trades = trades or []
    if not (0 <= trade_idx < len(trades)):
        return no_update, no_update, no_update, no_update

    trade = trades[trade_idx]
    entry_ts = trade.get("entry_ts", "")

    rows: list[dict] = []
    if entry_ts:
        rows = backtest_data.fetch_candles_around(
            exchange or "bybit",
            symbol or "",
            entry_ts,
            tf=tf or "1s",
            window=300,
        )

    title = (
        f"Trade #{trade_idx + 1}  ·  {trade.get('direction','long').upper()}  ·  "
        f"Entry {entry_ts[:19].replace('T', ' ')}  ·  "
        f"PnL ${float(trade.get('pnl_net', 0) or 0):.4f}"
    )
    return rows, trade_idx, True, title


@callback(
    Output("backtest-chart-collapse", "is_open", allow_duplicate=True),
    Input("backtest-close-chart-btn", "n_clicks"),
    prevent_initial_call=True,
)
def close_inline_chart(n_clicks):
    return False


# ── Inline candlestick chart ──────────────────────────────────────────────────

@callback(
    Output("backtest-inline-candlestick", "figure"),
    Input("backtest-chart-candles-store", "data"),
    Input("backtest-chart-trade-idx", "data"),
    State("backtest-trades-store", "data"),
)
def build_inline_chart(candle_rows, trade_idx, all_trades):
    df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
    fig = charts.build_candlestick(df)
    if not df.empty:
        fig = charts.add_volume_levels(fig, df)

    all_trades = all_trades or []
    if not all_trades:
        return fig

    # All entry/exit markers
    entry_x = [t.get("entry_ts") for t in all_trades if t.get("entry_ts")]
    entry_y = [t.get("entry_price") for t in all_trades if t.get("entry_ts")]
    if entry_x:
        fig.add_trace(go.Scatter(
            x=entry_x, y=entry_y, mode="markers",
            marker=dict(symbol="triangle-up", size=10, color="#26A69A",
                        line=dict(color="#1a7a71", width=1)),
            name="Buy",
            hovertemplate="Entry<br>%{x}<br>%{y:.4f}<extra></extra>",
        ))

    exit_x = [t.get("exit_ts") for t in all_trades if t.get("exit_ts")]
    exit_y = [t.get("exit_price") for t in all_trades if t.get("exit_ts")]
    if exit_x:
        fig.add_trace(go.Scatter(
            x=exit_x, y=exit_y, mode="markers",
            marker=dict(symbol="triangle-down", size=10, color="#EF5350",
                        line=dict(color="#b33b38", width=1)),
            name="Sell",
            hovertemplate="Exit<br>%{x}<br>%{y:.4f}<extra></extra>",
        ))

    # Highlighted trade
    sel = None
    if trade_idx is not None and 0 <= int(trade_idx) < len(all_trades):
        sel = all_trades[int(trade_idx)]

    if sel:
        e_ts, e_px = sel.get("entry_ts"), sel.get("entry_price")
        x_ts, x_px = sel.get("exit_ts"),  sel.get("exit_price")
        if e_ts and e_px is not None:
            fig.add_trace(go.Scatter(
                x=[e_ts], y=[e_px], mode="markers",
                marker=dict(symbol="triangle-up", size=18, color="#FFD700",
                            line=dict(color="#fff", width=2)),
                name="Selected Buy",
                hovertemplate="Selected Entry<br>%{x}<br>%{y:.4f}<extra></extra>",
            ))
        if x_ts and x_px is not None:
            fig.add_trace(go.Scatter(
                x=[x_ts], y=[x_px], mode="markers",
                marker=dict(symbol="triangle-down", size=18, color="#FF9800",
                            line=dict(color="#fff", width=2)),
                name="Selected Sell",
                hovertemplate="Selected Exit<br>%{x}<br>%{y:.4f}<extra></extra>",
            ))
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


@callback(
    Output("backtest-inline-cvd", "figure"),
    Input("backtest-chart-candles-store", "data"),
)
def update_inline_cvd(candle_rows):
    df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
    return charts.build_cvd_panel(df)


@callback(
    Output("backtest-inline-bidask", "figure"),
    Input("backtest-chart-candles-store", "data"),
)
def update_inline_bidask(candle_rows):
    df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
    return charts.build_bidask_panel(df)


# ── Candle detail modal (click on inline chart) ───────────────────────────────

@callback(
    Output("backtest-candle-modal", "is_open"),
    Output("backtest-candle-modal-title", "children"),
    Output("backtest-candle-modal-body", "children"),
    Input("backtest-inline-candlestick", "clickData"),
    State("backtest-exchange-dd", "value"),
    State("backtest-symbol-dd", "value"),
    State("backtest-tf-dd", "value"),
    prevent_initial_call=True,
)
def open_candle_detail_modal(click_data, exchange, symbol, tf):
    if not click_data:
        return False, no_update, no_update

    pts = click_data.get("points", [])
    if not pts:
        return False, no_update, no_update

    # Candlestick click gives x = timestamp string
    ts = pts[0].get("x", "")
    if not ts:
        return False, no_update, no_update

    detail = backtest_data.fetch_candle_detail(
        exchange or "bybit", symbol or "", ts, tf=tf or "1s"
    )

    title = f"Candle @ {str(ts)[:19].replace('T', ' ')}  ·  {exchange} : {symbol}  ·  {tf}"

    if not detail:
        body = html.Div("No data found for this candle.", style={"color": "#888"})
        return True, title, body

    body = _build_candle_detail_table(detail)
    return True, title, body


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_date(mode: str, sentinel: str, custom: str | None, ts_from_db: str | None) -> str:
    if mode == sentinel:
        return (ts_from_db or "")
    return (custom or "")


def _build_metrics(result: dict, segments: list | None = None) -> object:
    if not result:
        return html.Div("Run a backtest to see metrics.", style={"color": "#888", "fontSize": "13px"})

    def _cell(label: str, value: str, color: str = "#ccc") -> dbc.Col:
        return dbc.Col([
            html.Div(label, style={"color": "#666", "fontSize": "10px", "marginBottom": "1px"}),
            html.Div(value, style={"color": color, "fontSize": "13px", "fontWeight": "500"}),
        ], width=3, style={"marginBottom": "6px"})

    def _section(title: str, cells: list) -> html.Div:
        return html.Div([
            html.Div(title, style={
                "color": "#555", "fontSize": "10px", "letterSpacing": "0.08em",
                "textTransform": "uppercase", "marginBottom": "4px", "marginTop": "8px",
                "borderBottom": "1px solid #2a2a2a", "paddingBottom": "2px",
            }),
            dbc.Row(cells),
        ])

    pct = result.get("total_return_pct", 0)
    ret_color = "#4caf50" if pct >= 0 else "#f44336"
    cap = float(result.get("initial_capital", 1) or 1)
    final = float(result.get("final_value", cap))
    pnl_usd = final - cap

    fee_pass = result.get("passes_fee_gate", False)

    sections = [
        _section("Returns", [
            _cell("Total Return", f"{pct:.2f}%", ret_color),
            _cell("Annualised", f"{result.get('annualized_return_pct', 0):.2f}%", ret_color),
            _cell("P&L (USD)", f"${pnl_usd:+,.2f}", ret_color),
            _cell("Avg / Trade", f"${result.get('avg_pnl_per_trade', 0):.4f}"),
        ]),
        _section("Risk", [
            _cell("Sharpe", f"{result.get('sharpe_ratio', 0):.3f}"),
            _cell("SQN", f"{result.get('sqn', 0):.2f}"),
            _cell("Max DD %", f"{result.get('max_drawdown_pct', 0):.2f}%", "#ff9800"),
            _cell("Max DD $", f"${result.get('max_drawdown_usd', 0):.2f}", "#ff9800"),
            _cell("DD Duration", f"{result.get('max_drawdown_duration_bars', 0)} bars"),
        ]),
        _section("Trades", [
            _cell("Total", str(result.get("n_trades", 0))),
            _cell("Win Rate", f"{result.get('win_rate_pct', 0):.1f}%"),
            _cell("Profit Factor", f"{result.get('profit_factor', 0):.3f}"),
            _cell("Long / Short", f"{result.get('n_long_trades', 0)} / {result.get('n_short_trades', 0)}"),
            _cell("Avg Win", f"${result.get('avg_win_usd', 0):.4f}", "#4caf50"),
            _cell("Avg Loss", f"${result.get('avg_loss_usd', 0):.4f}", "#f44336"),
            _cell("Best", f"${result.get('best_trade_usd', 0):.4f}", "#4caf50"),
            _cell("Worst", f"${result.get('worst_trade_usd', 0):.4f}", "#f44336"),
            _cell("Consec W/L", f"{result.get('max_consec_wins', 0)} / {result.get('max_consec_losses', 0)}"),
            _cell("Avg Bars", f"{result.get('avg_trade_bars', 0):.1f}"),
        ]),
        _section("Costs", [
            _cell("Fees (USD)", f"${result.get('total_fees_usd', 0):.4f}"),
            _cell("Fee Gate", "✓ Pass" if fee_pass else "✗ Fail",
                  "#4caf50" if fee_pass else "#f44336"),
        ]),
    ]

    # Gap badge — collapsed by default, click opens modal
    gap_badge = _build_coverage_badge(segments or [])

    return html.Div([html.Div(sections), gap_badge])


def _build_coverage_badge(segments: list[dict]) -> object:
    """Return a compact ⚡ badge if there are gaps; nothing if data is clean."""
    if not segments:
        return html.Div()

    total_bars = sum(s["bars"] for s in segments)
    n_gaps = max(0, len(segments) - 1)

    if n_gaps == 0:
        # Single contiguous segment — show a small green note
        return html.Div(
            html.Span(f"✓ {total_bars:,} bars  ·  no gaps",
                      style={"color": "#4caf50", "fontSize": "11px"}),
            style={"marginTop": "6px"},
        )

    return html.Div(
        dbc.Button(
            [
                html.Span("⚡", style={"marginRight": "4px"}),
                html.Span(
                    f"Gap detected ({n_gaps} gap{'s' if n_gaps != 1 else ''}  ·  "
                    f"{total_bars:,} usable bars)",
                    style={"fontSize": "11px"},
                ),
            ],
            id="backtest-gap-badge-btn",
            color="warning",
            outline=True,
            size="sm",
            style={"marginTop": "8px"},
        ),
        style={"marginTop": "4px"},
    )


def _build_coverage_detail(segments: list[dict]) -> object:
    """Full coverage detail for the gap modal."""
    from datetime import datetime as _dt

    if not segments:
        return html.Div("No segment data available.", style={"color": "#888"})

    total_bars = sum(s["bars"] for s in segments)
    n_segs = len(segments)

    def _fmt_gap(t1_iso: str, t2_iso: str) -> str:
        try:
            gap_sec = int((_dt.fromisoformat(t2_iso) - _dt.fromisoformat(t1_iso)).total_seconds())
            if gap_sec >= 3600:
                return f"{gap_sec // 3600}h {(gap_sec % 3600) // 60}m"
            if gap_sec >= 60:
                return f"{gap_sec // 60}m {gap_sec % 60}s"
            return f"{gap_sec}s"
        except Exception:
            return "?"

    _mono = {"fontFamily": "monospace", "fontSize": "12px"}
    items: list = []

    for i, seg in enumerate(segments):
        start = seg["start"][:19].replace("T", " ")
        end   = seg["end"][:19].replace("T", " ")
        bars  = seg["bars"]
        items.append(
            html.Div([
                html.Span(f"▶ {start}", style={**_mono, "color": "#4fc3f7"}),
                html.Span("  →  ", style={"color": "#555"}),
                html.Span(end, style={**_mono, "color": "#4fc3f7"}),
                html.Span(f"  ({bars:,} bars)", style={"color": "#888", "fontSize": "11px"}),
            ], style={"marginBottom": "4px"})
        )
        if i < len(segments) - 1:
            gap_str = _fmt_gap(seg["end"], segments[i + 1]["start"])
            items.append(html.Div(
                f"  ⚡ gap  {gap_str}",
                style={**_mono, "color": "#ff9800", "marginBottom": "4px"},
            ))

    items.append(html.Hr(style={"borderColor": "#333", "margin": "10px 0"}))
    items.append(html.Div(
        f"{total_bars:,} usable bars across {n_segs} segment{'s' if n_segs != 1 else ''}",
        style={"color": "#888", "fontSize": "12px"},
    ))
    return html.Div(items)


def _trade_duration(entry_ts: str, exit_ts: str) -> str:
    try:
        from datetime import datetime as _dt
        delta = _dt.fromisoformat(exit_ts) - _dt.fromisoformat(entry_ts)
        s = int(delta.total_seconds())
        if s < 0:
            return "?"
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m {s % 60}s"
        return f"{s // 3600}h {(s % 3600) // 60}m"
    except Exception:
        return "?"


def _build_trades_table(
    trades: list[dict],
    run_id: str,
    exchange: str,
    symbol: str,
    tf: str,
) -> object:
    """Render the per-trade table.  Each row has a 📊 button to load the inline chart."""
    if not trades:
        return html.Div("No trades recorded.", style={"color": "#555"})

    _mono = {"fontFamily": "monospace", "fontSize": "12px"}
    rows = []
    running_pnl = 0.0
    for i, t in enumerate(trades):
        pnl_net = float(t.get("pnl_net", t.get("pnl", 0)) or 0)
        running_pnl += pnl_net
        pnl_color = "#4caf50" if pnl_net >= 0 else "#f44336"
        run_color = "#4caf50" if running_pnl >= 0 else "#f44336"
        entry_ts = t.get("entry_ts", "")
        exit_ts  = t.get("exit_ts", "")
        dur = _trade_duration(entry_ts, exit_ts)

        rows.append(html.Tr([
            html.Td(str(i + 1), style={"color": "#555"}),
            html.Td(
                html.Span(t.get("direction", "long")[0].upper(),
                          style={"color": "#80cbc4" if t.get("direction") == "long" else "#ff9800"}),
            ),
            html.Td(entry_ts[:19].replace("T", " "), style=_mono),
            html.Td(exit_ts[:19].replace("T", " "),  style=_mono),
            html.Td(dur, style={"color": "#888", "fontSize": "11px"}),
            html.Td(f"{t.get('entry_price', 0):.4f}", style=_mono),
            html.Td(f"{t.get('exit_price', 0):.4f}",  style=_mono),
            html.Td(f"${pnl_net:+.4f}",    style={"color": pnl_color, **_mono}),
            html.Td(f"${running_pnl:+.4f}", style={"color": run_color, **_mono}),
            html.Td(
                dbc.Button(
                    "📊",
                    id={"type": "trade-chart-btn", "index": i},
                    color="link",
                    size="sm",
                    style={"color": "#80cbc4", "fontSize": "14px", "padding": "0 4px"},
                ),
            ),
        ], style={"fontSize": "12px",
                  "backgroundColor": "transparent" if i % 2 == 0 else "#1a1a1a"}))

    header = html.Thead(html.Tr([
        html.Th("#",        style={"width": "3%"}),
        html.Th("Dir",      style={"width": "3%"}),
        html.Th("Entry Time"),
        html.Th("Exit Time"),
        html.Th("Dur"),
        html.Th("Entry $"),
        html.Th("Exit $"),
        html.Th("PnL Net"),
        html.Th("Run PnL"),
        html.Th(""),
    ], style={"color": "#666", "fontSize": "11px", "borderBottom": "1px solid #333"}))

    return html.Div(
        html.Table(
            [header, html.Tbody(rows)],
            style={"width": "100%", "borderCollapse": "collapse"},
        ),
        style={"maxHeight": "420px", "overflowY": "auto"},
    )


def _build_equity_figure(result: dict) -> go.Figure:
    """Build equity curve figure from the in-memory result dict."""
    df = backtest_data.build_equity_from_result(result)
    if df.empty:
        # Fallback: try QuestDB (handles the case where result was loaded from
        # history rather than the current in-memory store)
        run_id = result.get("run_id", "")
        if run_id:
            df = backtest_data.fetch_equity_curve(run_id)
    if df.empty:
        fig = go.Figure()
        fig.update_layout(template="plotly_dark", title="No equity data")
        return fig

    equity = df["portfolio_value"]
    roll_max = equity.cummax()
    dd_pct = (equity - roll_max) / roll_max * 100.0

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.65, 0.35],
        vertical_spacing=0.04,
    )

    fig.add_trace(go.Scatter(
        x=df["bar_ts"],
        y=equity,
        mode="lines",
        line={"color": "#4fc3f7", "width": 1.5},
        name="Portfolio Value",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df["bar_ts"],
        y=dd_pct,
        mode="lines",
        fill="tozeroy",
        line={"color": "#ff9800", "width": 1.0},
        fillcolor="rgba(255,152,0,0.15)",
        name="Drawdown %",
    ), row=2, col=1)

    fig.update_layout(
        template="plotly_dark",
        margin={"l": 40, "r": 10, "t": 30, "b": 30},
        legend={"orientation": "h", "y": 1.06, "x": 0, "font": {"size": 11}},
        showlegend=True,
    )
    fig.update_yaxes(title_text="USD", row=1, col=1)
    fig.update_yaxes(title_text="DD %", row=2, col=1)
    return fig


def _build_history(df: pd.DataFrame) -> tuple[list[dict], object | None]:
    if df.empty:
        return [], None
    hash_opts = [{"label": h, "value": h} for h in df["hash"].unique()] if "hash" in df.columns else []
    display_cols = [
        "run_at", "hash", "strategy_name", "symbol", "tf",
        "total_return_pct", "sharpe_ratio", "max_drawdown_pct",
        "n_trades", "win_rate_pct", "passes_fee_gate",
    ]
    cols_present = [c for c in display_cols if c in df.columns]
    tbl = dash_table.DataTable(
        data=df[cols_present].to_dict("records"),
        columns=[{"name": c, "id": c} for c in cols_present],
        page_size=15,
        sort_action="native",
        filter_action="native",
        style_table={"overflowX": "auto"},
        style_header={"backgroundColor": "#2a2a2a", "color": "#eee", "fontWeight": "bold"},
        style_cell={"backgroundColor": "#1e1e1e", "color": "#ccc", "fontSize": "12px", "padding": "4px 8px"},
        style_data_conditional=[{"if": {"row_index": "odd"}, "backgroundColor": "#262626"}],
    )
    return hash_opts, tbl


def _build_candle_detail_table(detail: dict) -> object:
    """Render all candle fields grouped by category in a two-column table."""
    _GROUPS = [
        ("OHLCV", ["open", "high", "low", "close", "volume", "quote_volume",
                   "trade_count", "twap"]),
        ("Mid-price", ["mid_price_open", "mid_price_high", "mid_price_low", "vwmp"]),
        ("Spread", ["spread_high", "spread_low", "spread_mean", "effective_spread"]),
        ("OB Best Quotes", ["best_bid_open", "best_ask_open", "best_bid", "best_ask"]),
        ("Trade Flow", ["buy_volume", "sell_volume", "buy_count",
                        "block_buy_volume", "block_sell_volume"]),
        ("Trade Distribution", ["max_trade_size", "large_bid_orders", "large_ask_orders",
                                 "first_trade_offset_ms", "last_trade_offset_ms",
                                 "trade_clustering", "max_consecutive_run"]),
        ("Volatility", ["realized_vol", "realized_skewness", "uptick_count", "downtick_count"]),
        ("OFI", ["ofi", "ofi_l1"]),
        ("CVD", ["cum_delta", "cvd_divergence"]),
        ("OB Activity", ["bid_order_arrivals", "ask_order_arrivals",
                          "bid_cancel_count", "ask_cancel_count", "ob_modify_count",
                          "avg_bid_order_size", "avg_ask_order_size",
                          "best_bid_changes", "best_ask_changes", "quote_stuff_ratio"]),
        ("Microstructure Signals", ["hawkes_intensity", "microprice", "microprice_mid_delta",
                                     "cancel_bias", "trade_aggressiveness",
                                     "trade_sign_autocorr", "inter_trade_interval_std_ms",
                                     "num_trade_price_levels"]),
        ("Footprint", ["poc_price", "value_area_high", "value_area_low", "poc_volume",
                        "imbalance_ratio", "imbalance_buy_count", "imbalance_sell_count",
                        "imbalance_stack_buy", "imbalance_stack_sell", "single_print_count",
                        "unfinished_top", "unfinished_bottom", "absorption_detected",
                        "footprint_delta_divergence"]),
        ("Iceberg", ["iceberg_bid_detected", "iceberg_ask_detected", "iceberg_price"]),
        ("VWAP Deviation", ["buy_vwap_deviation_bps", "sell_vwap_deviation_bps"]),
        ("Quality", ["is_partial", "gap_count", "bar_count"]),
    ]

    _cell_style = {"padding": "3px 8px", "fontSize": "12px", "borderBottom": "1px solid #2a2a2a"}
    _label_style = {"color": "#888", "fontFamily": "monospace"}
    _val_style   = {"color": "#ccc", "fontFamily": "monospace"}
    _head_style  = {
        "color": "#555", "fontSize": "10px", "letterSpacing": "0.08em",
        "textTransform": "uppercase", "padding": "6px 8px 2px",
        "borderBottom": "1px solid #333",
    }

    def _fmt(v) -> str:
        if v is None:
            return "—"
        if isinstance(v, bool):
            return "✓" if v else "✗"
        if isinstance(v, float):
            return f"{v:.6g}"
        if isinstance(v, str) and len(v) > 60:
            return v[:57] + "…"
        return str(v)

    sections: list = []
    for group_name, fields in _GROUPS:
        rows_in_group = []
        for field in fields:
            if field in detail:
                val = detail[field]
                rows_in_group.append(html.Tr([
                    html.Td(field, style={**_cell_style, **_label_style}),
                    html.Td(_fmt(val), style={**_cell_style, **_val_style}),
                ]))
        if rows_in_group:
            sections.append(html.Tr(
                html.Td(group_name, colSpan=2, style=_head_style)
            ))
            sections.extend(rows_in_group)

    # Also show any fields not covered by the groups above
    known = {f for _, fields in _GROUPS for f in fields}
    extras = [
        html.Tr([
            html.Td(k, style={**_cell_style, **_label_style}),
            html.Td(_fmt(v), style={**_cell_style, **_val_style}),
        ])
        for k, v in detail.items()
        if k not in known and k not in ("ts", "exchange", "symbol", "footprint_json",
                                         "single_print_levels_json")
    ]
    if extras:
        sections.append(html.Tr(html.Td("Other", colSpan=2, style=_head_style)))
        sections.extend(extras)

    return html.Div(
        html.Table(
            sections,
            style={"width": "100%", "borderCollapse": "collapse"},
        ),
        style={"maxHeight": "70vh", "overflowY": "auto"},
    )
