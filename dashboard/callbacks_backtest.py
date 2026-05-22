"""Callbacks for the /backtests page."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dash_table, html, no_update

import backtest_data
from layout_backtest import _EMPTY_EQUITY_FIG


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
    start_str = start[:10] if start else "?"
    end_str   = end[:10]   if end   else "?"
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
        start_date=start_date[:19],   # trim sub-second precision if present
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
    Input("backtest-poll-interval", "n_intervals"),
    State("backtest-run-id-store", "data"),
    State("backtest-strategy-dd", "value"),
    prevent_initial_call=True,
)
def on_poll(n_intervals, run_id, strategy_name):
    if not run_id:
        return no_update, no_update, no_update, True, no_update
    status_data = backtest_data.poll_run_status(run_id)
    if "error" in status_data:
        return html.Span(f"❌ Poll error: {status_data['error']}", style={"color": "#f44336"}), no_update, no_update, True, no_update
    status = status_data.get("status", "unknown")
    if status == "running":
        return f"Running… (run_id={run_id})", no_update, no_update, False, no_update
    if status == "failed":
        error_msg = status_data.get("error", "unknown error")
        return html.Span(f"❌ Failed: {error_msg}", style={"color": "#f44336"}), no_update, no_update, True, no_update
    metrics_div = _build_metrics(status_data.get("result", {}))
    eq_fig = _build_equity_figure(run_id)
    df = backtest_data.fetch_run_history(strategy_name=strategy_name)
    _, history_table = _build_history(df)
    return html.Span(f"✓ Done (run_id={run_id})", style={"color": "#4caf50"}), metrics_div, eq_fig, True, history_table


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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_date(mode: str, sentinel: str, custom: str | None, ts_from_db: str | None) -> str:
    """Return the ISO date string to use for start/end.

    - If mode matches sentinel ("first"/"last"), use ts_from_db.
    - Otherwise use the custom input value.
    """
    if mode == sentinel:
        return (ts_from_db or "")
    return (custom or "")


def _build_metrics(result: dict) -> object:
    if not result:
        return html.Div("Run a backtest to see metrics.", style={"color": "#888", "fontSize": "13px"})
    rows = [
        _metric_row("Return",       f"{result.get('total_return_pct', 0):.2f}%"),
        _metric_row("Sharpe",       f"{result.get('sharpe_ratio', 0):.2f}"),
        _metric_row("Max Drawdown", f"{result.get('max_drawdown_pct', 0):.2f}%"),
        _metric_row("Trades",       str(result.get("n_trades", 0))),
        _metric_row("Win Rate",     f"{result.get('win_rate_pct', 0):.1f}%"),
        _metric_row(
            "Fee Gate",
            "✓ Pass" if result.get("passes_fee_gate") else "✗ Fail",
            "#4caf50" if result.get("passes_fee_gate") else "#f44336",
        ),
    ]
    return html.Div(rows)


def _metric_row(label: str, value: str, color: str = "#ccc") -> dbc.Row:
    return dbc.Row([
        dbc.Col(html.Span(label, style={"color": "#888", "fontSize": "12px"}), width=5),
        dbc.Col(html.Span(value, style={"color": color}), width=7),
    ], className="mb-1")


def _build_equity_figure(run_id: str) -> go.Figure:
    df = backtest_data.fetch_equity_curve(run_id)
    fig = go.Figure()
    if df.empty:
        fig.update_layout(template="plotly_dark", title="No equity data")
        return fig
    fig.add_trace(go.Scatter(
        x=df["bar_ts"],
        y=df["portfolio_value"],
        mode="lines",
        line={"color": "#4fc3f7", "width": 1.5},
        name="Portfolio Value",
    ))
    fig.update_layout(
        template="plotly_dark",
        margin={"l": 40, "r": 10, "t": 30, "b": 30},
        title="Equity Curve",
        xaxis_title=None,
        yaxis_title="Value (USD)",
    )
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
