"""Callbacks for the /backtests page."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dash_table, html, no_update

import backtest_data


# ── Strategy select → code viewer + history ───────────────────────────────────

@callback(
    Output("backtest-strategy-dd", "options"),
    Input("backtest-strategy-dd", "id"),  # fires once on page load
)
def load_strategy_options(_: str) -> list[dict]:
    strategies = backtest_data.fetch_strategies()
    return [{"label": s["name"], "value": s["name"]} for s in strategies]


@callback(
    Output("backtest-code-view", "children"),
    Output("backtest-hash-filter-dd", "options"),
    Output("backtest-history-table", "children"),
    Input("backtest-strategy-dd", "value"),
)
def on_strategy_select(strategy_name: str | None):
    if not strategy_name:
        return "Select a strategy to view its source code.", [], None

    # Source code
    strategies = backtest_data.fetch_strategies()
    code = next((s["code"] for s in strategies if s["name"] == strategy_name), "")

    # History
    df = backtest_data.fetch_run_history(strategy_name=strategy_name)
    hash_opts, table = _build_history(df)

    return code, hash_opts, table


# ── Run button ────────────────────────────────────────────────────────────────

@callback(
    Output("backtest-run-id-store", "data"),
    Output("backtest-status-div", "children"),
    Output("backtest-poll-interval", "disabled"),
    Input("backtest-run-btn", "n_clicks"),
    State("backtest-strategy-dd", "value"),
    State("backtest-exchange-dd", "value"),
    State("backtest-symbol-inp", "value"),
    State("backtest-tf-dd", "value"),
    State("backtest-start-date", "value"),
    State("backtest-end-date", "value"),
    State("backtest-capital-inp", "value"),
    prevent_initial_call=True,
)
def on_run_click(n_clicks, strategy_name, exchange, symbol, tf, start, end, capital):
    if not strategy_name:
        return no_update, "Select a strategy first.", True
    try:
        cap = float(capital or 10000)
    except ValueError:
        cap = 10000.0
    result = backtest_data.submit_backtest(
        strategy_name=strategy_name,
        symbol=symbol or "BTCUSDT",
        tf=tf or "1s",
        exchange=exchange or "bybit",
        start_date=start or "2026-01-01",
        end_date=end or "2026-02-01",
        capital=cap,
    )
    if "error" in result:
        return None, f"Error: {result['error']}", True
    run_id = result.get("run_id")
    return run_id, f"Running… (run_id={run_id})", False


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
        return f"Poll error: {status_data['error']}", no_update, no_update, True, no_update

    status = status_data.get("status", "unknown")

    if status == "running":
        return f"Running… (run_id={run_id})", no_update, no_update, False, no_update

    if status == "failed":
        return f"Failed: {status_data.get('error', 'unknown error')}", no_update, no_update, True, no_update

    # Done — extract metrics and equity curve
    metrics_div = _build_metrics(status_data.get("result", {}))
    eq_fig = _build_equity_figure(run_id)

    # Refresh history table
    df = backtest_data.fetch_run_history(strategy_name=strategy_name)
    _, history_table = _build_history(df)

    return f"Done (run_id={run_id})", metrics_div, eq_fig, True, history_table


# ── Hash filter ───────────────────────────────────────────────────────────────

@callback(
    Output("backtest-history-table", "children", allow_duplicate=True),
    Input("backtest-hash-filter-dd", "value"),
    State("backtest-strategy-dd", "value"),
    prevent_initial_call=True,
)
def on_hash_filter(hash_val: str | None, strategy_name: str | None):
    df = backtest_data.fetch_run_history(strategy_name=strategy_name, hash_filter=hash_val)
    _, table = _build_history(df)
    return table


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_metrics(result: dict) -> object:
    if not result:
        return "No result yet."
    rows = [
        dbc.Row([
            dbc.Col(html.Span("Return", style={"color": "#888", "fontSize": "12px"}), width=4),
            dbc.Col(html.Span(f"{result.get('total_return_pct', 0):.2f}%"), width=8),
        ]),
        dbc.Row([
            dbc.Col(html.Span("Sharpe", style={"color": "#888", "fontSize": "12px"}), width=4),
            dbc.Col(html.Span(f"{result.get('sharpe_ratio', 0):.2f}"), width=8),
        ]),
        dbc.Row([
            dbc.Col(html.Span("Max Drawdown", style={"color": "#888", "fontSize": "12px"}), width=4),
            dbc.Col(html.Span(f"{result.get('max_drawdown_pct', 0):.2f}%"), width=8),
        ]),
        dbc.Row([
            dbc.Col(html.Span("Trades", style={"color": "#888", "fontSize": "12px"}), width=4),
            dbc.Col(html.Span(f"{result.get('n_trades', 0)}"), width=8),
        ]),
        dbc.Row([
            dbc.Col(html.Span("Win Rate", style={"color": "#888", "fontSize": "12px"}), width=4),
            dbc.Col(html.Span(f"{result.get('win_rate_pct', 0):.1f}%"), width=8),
        ]),
        dbc.Row([
            dbc.Col(html.Span("Fee Gate", style={"color": "#888", "fontSize": "12px"}), width=4),
            dbc.Col(
                html.Span(
                    "✓" if result.get("passes_fee_gate") else "✗",
                    style={"color": "#4caf50" if result.get("passes_fee_gate") else "#f44336"},
                ),
                width=8,
            ),
        ]),
    ]
    return rows


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

    # Hash options for the filter dropdown
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
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": "#262626"},
        ],
    )
    return hash_opts, tbl
